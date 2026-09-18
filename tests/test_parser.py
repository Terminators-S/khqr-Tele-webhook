from app.parser import parse_aba_text


def test_parse_aba_transaction_with_remark():
    parsed = parse_aba_text(
        "Payment received\nTransaction ID: 123456789012\n"
        "Amount: USD 1.41\nRemark: KQABCDEF123456"
    )
    assert parsed.trx_id == "123456789012"
    assert parsed.amount_minor == 141
    assert parsed.currency == "USD"
    assert parsed.remark == "KQABCDEF123456"


def test_parse_aba_amount_without_remark():
    parsed = parse_aba_text("Trx. ID: 9876543210\nYou received $10.09")
    assert parsed.trx_id == "9876543210"
    assert parsed.amount_minor == 1009
    assert parsed.remark is None


def test_real_aba_notification_shape_with_blank_remark():
    parsed = parse_aba_text(
        "$2.70 paid by CUSTOMER (*098) on Sep 05, 04:09 PM "
        "via ABA PAY at MERCHANT. Remark:  . "
        "Trx. ID: 178859934628331, APV: 964372."
    )
    assert parsed.trx_id == "178859934628331"
    assert parsed.amount_minor == 270
    assert parsed.remark is None


def test_expected_real_shape_with_nonempty_remark():
    parsed = parse_aba_text(
        "$1.41 paid by CUSTOMER (*007) on Sep 18, 04:07 PM "
        "via ABA PAY at MERCHANT. Remark: KQABCDEF123456. "
        "Trx. ID: 178959999999999, APV: 123456."
    )
    assert parsed.trx_id == "178959999999999"
    assert parsed.amount_minor == 141
    assert parsed.remark == "KQABCDEF123456"
