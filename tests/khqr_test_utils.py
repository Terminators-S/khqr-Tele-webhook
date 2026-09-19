from __future__ import annotations

import io

import qrcode

from app.khqr_payload import _crc16_ccitt_false


def _tlv(tag: str, value: str) -> str:
    return f"{tag}{len(value):02d}{value}"


def valid_khqr_payload(
    *,
    account_id: str = "demo@aba",
    merchant_name: str = "Demo Store",
    currency: str = "USD",
    account_type: str = "individual",
) -> str:
    account_info = _tlv("00", account_id)
    account_tag = "29" if account_type == "merchant" else "30"
    currency_code = "116" if currency == "KHR" else "840"
    body = "".join(
        (
            _tlv("00", "01"),
            _tlv("01", "11"),
            _tlv(account_tag, account_info),
            _tlv("53", currency_code),
            _tlv("58", "KH"),
            _tlv("59", merchant_name),
            _tlv("60", "Phnom Penh"),
            _tlv("62", _tlv("05", "STORE123")),
        )
    )
    with_crc_tag = body + "6304"
    crc = f"{_crc16_ccitt_false(with_crc_tag.encode('ascii')):04X}"
    return with_crc_tag + crc


def khqr_png_bytes(**kwargs) -> bytes:
    image = qrcode.make(valid_khqr_payload(**kwargs))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()
