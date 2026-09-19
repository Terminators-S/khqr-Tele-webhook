from __future__ import annotations

from dataclasses import dataclass


class KhqrPayloadError(ValueError):
    pass


@dataclass(frozen=True)
class KhqrValidation:
    valid: bool
    currency: str | None
    account_type: str | None
    bakong_id: str | None
    merchant_name: str | None
    merchant_city: str | None


def _crc16_ccitt_false(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def _parse_tlv(payload: str) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    index = 0
    while index < len(payload):
        if index + 4 > len(payload):
            raise KhqrPayloadError("KHQR payload has a truncated TLV header")
        tag = payload[index:index + 2]
        raw_length = payload[index + 2:index + 4]
        if not raw_length.isdigit():
            raise KhqrPayloadError("KHQR payload has an invalid TLV length")
        length = int(raw_length)
        start = index + 4
        end = start + length
        if end > len(payload):
            raise KhqrPayloadError("KHQR payload has a truncated TLV value")
        rows.append((tag, payload[start:end]))
        index = end
    return rows


def validate_static_khqr(payload: str) -> KhqrValidation:
    value = (payload or "").strip()
    if not value:
        raise KhqrPayloadError("KHQR payload is empty")
    if not value.isascii():
        raise KhqrPayloadError("KHQR payload must be ASCII")
    if len(value) < 80 or len(value) > 1024:
        raise KhqrPayloadError("KHQR payload length is invalid")

    rows = _parse_tlv(value)
    tags = {tag: item for tag, item in rows}

    if tags.get("00") != "01":
        raise KhqrPayloadError("KHQR payload format indicator is invalid")
    if tags.get("01") != "11":
        raise KhqrPayloadError("KHQR must be a reusable static QR")
    if tags.get("58") != "KH":
        raise KhqrPayloadError("KHQR country code must be KH")

    account_type = None
    account_value = None
    if "29" in tags:
        account_type = "merchant"
        account_value = tags["29"]
    elif "30" in tags:
        account_type = "individual"
        account_value = tags["30"]
    else:
        raise KhqrPayloadError("KHQR merchant account information is missing")

    account_rows = _parse_tlv(account_value or "")
    account_tags = {tag: item for tag, item in account_rows}
    bakong_id = account_tags.get("00")
    if not bakong_id:
        raise KhqrPayloadError("KHQR Bakong account ID is missing")

    currency_code = tags.get("53")
    currency = {"840": "USD", "116": "KHR"}.get(currency_code)
    if currency is None:
        raise KhqrPayloadError("KHQR currency is unsupported")

    if "63" not in tags or len(tags["63"]) != 4:
        raise KhqrPayloadError("KHQR CRC is missing")
    crc_input = value[:-4].encode("ascii")
    actual_crc = f"{_crc16_ccitt_false(crc_input):04X}"
    if tags["63"].upper() != actual_crc:
        raise KhqrPayloadError("KHQR CRC validation failed")

    return KhqrValidation(
        valid=True,
        currency=currency,
        account_type=account_type,
        bakong_id=bakong_id,
        merchant_name=tags.get("59"),
        merchant_city=tags.get("60"),
    )
