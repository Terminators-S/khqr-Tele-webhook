from scripts import creative_studio_parity as parity


def observation():
    return {
        "group_id": -5498240950,
        "rows": [
            {
                "trx_id": "178900000000001",
                "amount_minor": 101,
                "received_at": "2026-09-19T00:00:00+00:00",
            },
            {
                "trx_id": "178900000000002",
                "amount_minor": 250,
                "received_at": "2026-09-19T00:01:00+00:00",
            },
        ],
    }


def test_compare_accepts_exact_incumbent_parity():
    incumbent = {
        "178900000000001": {
            "amount": 1.01,
            "received_at": "2026-09-19T00:00:00Z",
            "source": "ABA Merchant (-5498240950)",
        },
        "178900000000002": {
            "amount": 2.50,
            "received_at": "2026-09-19T00:01:03+00:00",
            "source": "telethon-live:-5498240950:42",
        },
    }
    result = parity.compare(observation(), incumbent)
    assert result["parity_pass"] is True
    assert result["trx_found"] == 2
    assert result["amount_match"] == 2
    assert result["time_within_5s"] == 2
    assert result["source_group_match"] == 2
    assert result["max_time_delta_seconds"] == 3.0


def test_compare_fails_on_missing_or_wrong_amount():
    incumbent = {
        "178900000000001": {
            "amount": 9.99,
            "received_at": "2026-09-19T00:00:00Z",
            "source": "ABA Merchant (-5498240950)",
        },
    }
    result = parity.compare(observation(), incumbent)
    assert result["parity_pass"] is False
    assert result["trx_found"] == 1
    assert result["amount_match"] == 0
    assert result["missing"] == 1
