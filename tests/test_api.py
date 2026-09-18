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
    }

    first = client.post("/v1/payment-intents", headers=headers, json=body)
    second = client.post("/v1/payment-intents", headers=headers, json=body)
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["payment_request"]["mode"] == "DUAL"


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
