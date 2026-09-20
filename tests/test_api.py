from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)
INTERNAL = {"X-Internal-Secret": "test-internal-secret-32-characters"}


def test_internal_auth_is_required():
    response = client.post(
        "/internal/businesses",
        json={"name": "Denied", "slug": "denied"},
    )
    assert response.status_code == 422


def test_onboard_source_and_create_idempotent_intent():
    business_response = client.post(
        "/internal/businesses",
        headers=INTERNAL,
        json={"name": "API Store", "slug": "api-store"},
    )
    assert business_response.status_code == 200
    business = business_response.json()

    source_response = client.post(
        f"/internal/businesses/{business['id']}/sources",
        headers=INTERNAL,
        json={
            "name": "ABA",
            "currency": "USD",
            "telegram_group_id": -100555,
            "telegram_sender_id": 555,
            "merchant_alias": "API STORE",
            "static_khqr": "STATIC",
            "enabled": True,
        },
    )
    assert source_response.status_code == 200
    source = source_response.json()
    assert source["ready"] is True

    headers = {"X-Api-Key": business["api_key"], "Idempotency-Key": "idem-api-1"}
    body = {
        "source_id": source["id"],
        "external_id": "order-api-1",
        "amount_minor": 141,
        "currency": "USD",
        "metadata": {"cart": "A"},
        "remark_prefix": "BB",
    }

    first = client.post("/v1/payment-intents", headers=headers, json=body)
    second = client.post("/v1/payment-intents", headers=headers, json=body)
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["payment_request"]["mode"] == "DUAL"
    assert first.json()["payment_request"]["remark"].startswith("BB")
    assert len(first.json()["payment_request"]["remark"]) == 7


def test_ready_requires_at_least_one_valid_enabled_source():
    response = client.get("/ready")
    assert response.status_code == 503


def test_shadow_promotion_endpoint_is_disabled_by_default():
    response = client.post(
        "/internal/sources/not-a-real-source/promote-shadow",
        headers=INTERNAL,
        json={"limit": 10},
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "shadow promotion is disabled by configuration"


def test_staged_source_activation_requires_verified_sender_and_explicit_enable():
    business_response = client.post(
        "/internal/businesses",
        headers=INTERNAL,
        json={"name": "Staged Store", "slug": "staged-store"},
    )
    assert business_response.status_code == 200
    business = business_response.json()

    source_response = client.post(
        f"/internal/businesses/{business['id']}/sources",
        headers=INTERNAL,
        json={
            "name": "ABA staged",
            "currency": "USD",
            "telegram_group_id": -100777,
            "telegram_sender_id": None,
            "merchant_alias": "STAGED",
            "static_khqr": "STATIC",
            "enabled": False,
        },
    )
    assert source_response.status_code == 200
    source = source_response.json()
    assert source["enabled"] is False
    assert source["ready"] is False

    premature_enable = client.post(
        f"/internal/sources/{source['id']}/enabled",
        headers=INTERNAL,
        json={"enabled": True},
    )
    assert premature_enable.status_code == 409
    assert "incomplete" in premature_enable.json()["detail"]

    configured = client.post(
        f"/internal/sources/{source['id']}/sender",
        headers=INTERNAL,
        json={"telegram_sender_id": 777},
    )
    assert configured.status_code == 200
    assert configured.json()["telegram_sender_id"] == 777
    assert configured.json()["enabled"] is False
    assert configured.json()["ready"] is False

    enabled = client.post(
        f"/internal/sources/{source['id']}/enabled",
        headers=INTERNAL,
        json={"enabled": True},
    )
    assert enabled.status_code == 200
    assert enabled.json()["enabled"] is True
    assert enabled.json()["ready"] is True

    live_sender_change = client.post(
        f"/internal/sources/{source['id']}/sender",
        headers=INTERNAL,
        json={"telegram_sender_id": 778},
    )
    assert live_sender_change.status_code == 409
    assert "disable" in live_sender_change.json()["detail"]

    disabled = client.post(
        f"/internal/sources/{source['id']}/enabled",
        headers=INTERNAL,
        json={"enabled": False},
    )
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False
    assert disabled.json()["ready"] is False

    corrected = client.post(
        f"/internal/sources/{source['id']}/sender",
        headers=INTERNAL,
        json={"telegram_sender_id": 778},
    )
    assert corrected.status_code == 200
    assert corrected.json()["telegram_sender_id"] == 778


def test_payment_intent_rejects_invalid_remark_prefix(business_and_source):
    business, source, api_key = business_and_source
    response = client.post(
        "/v1/payment-intents",
        headers={"X-Api-Key": api_key, "Idempotency-Key": "bad-prefix"},
        json={
            "source_id": source.id,
            "external_id": "order-bad-prefix",
            "amount_minor": 1500,
            "currency": "USD",
            "remark_prefix": "B-",
        },
    )
    assert response.status_code == 422
