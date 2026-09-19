import pytest

from app.khqr_payload import KhqrPayloadError, validate_static_khqr
from tests.khqr_test_utils import valid_khqr_payload


def test_validate_static_usd_individual_khqr():
    payload = valid_khqr_payload(
        account_id="demo@aba",
        merchant_name="Demo Store",
        currency="USD",
        account_type="individual",
    )
    result = validate_static_khqr(payload)
    assert result.valid is True
    assert result.currency == "USD"
    assert result.account_type == "individual"
    assert result.bakong_id == "demo@aba"
    assert result.merchant_name == "Demo Store"


def test_validate_static_khr_merchant_khqr():
    payload = valid_khqr_payload(
        account_id="merchant@aba",
        merchant_name="Merchant Store",
        currency="KHR",
        account_type="merchant",
    )
    result = validate_static_khqr(payload)
    assert result.valid is True
    assert result.currency == "KHR"
    assert result.account_type == "merchant"


def test_tampered_khqr_crc_is_rejected():
    payload = valid_khqr_payload()
    tampered = payload[:-4] + ("0000" if payload[-4:] != "0000" else "FFFF")
    with pytest.raises(KhqrPayloadError, match="CRC"):
        validate_static_khqr(tampered)


def test_arbitrary_text_is_not_khqr():
    with pytest.raises(KhqrPayloadError):
        validate_static_khqr("not-a-khqr")
