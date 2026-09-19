from datetime import timedelta

from fastapi.testclient import TestClient

from app import core, models
from app.main import app


client = TestClient(app)
SECRET = "test-internal-secret-32-characters"


def login():
    response = client.post(
        "/dashboard/api/login",
        json={"secret": SECRET},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["authenticated"] is True
    assert body["csrf_token"]
    return body["csrf_token"]


def test_dashboard_page_and_auth_gate():
    page = client.get("/dashboard")
    assert page.status_code == 200
    assert "KHQR Control Center" in page.text

    denied = client.get("/dashboard/api/overview")
    assert denied.status_code == 401

    wrong = client.post(
        "/dashboard/api/login",
        json={"secret": "definitely-wrong-secret"},
    )
    assert wrong.status_code == 401

def test_dashboard_requires_csrf_for_mutation():
    csrf = login()

    missing = client.post(
        "/dashboard/api/businesses",
        json={"name": "Dashboard Store", "slug": "dashboard-store"},
    )
    assert missing.status_code == 403

    created = client.post(
        "/dashboard/api/businesses",
        headers={"X-CSRF-Token": csrf},
        json={"name": "Dashboard Store", "slug": "dashboard-store"},
    )
    assert created.status_code == 200
    body = created.json()
    assert body["api_key"].startswith("khqr_live_")
    assert body["one_time_secret"] is True


def test_dashboard_source_starts_disabled_and_enable_needs_phrase(db):
    csrf = login()
    business = client.post(
        "/dashboard/api/businesses",
        headers={"X-CSRF-Token": csrf},
        json={"name": "Safe UI Store", "slug": "safe-ui-store"},
    ).json()

    source_response = client.post(
        "/dashboard/api/sources",
        headers={"X-CSRF-Token": csrf},
        json={
            "business_id": business["id"],
            "name": "ABA Main",
            "currency": "USD",
            "telegram_group_id": -100818181,
            "telegram_sender_id": 818181,
            "merchant_alias": "SAFE UI STORE",
            "static_khqr": "STATIC-KHQR",
        },
    )
    assert source_response.status_code == 200
    source = source_response.json()
    assert source["enabled"] is False
    assert source["ready"] is False
    assert source["configured"] is True
    assert "static_khqr" not in source
    assert source["static_khqr_configured"] is True

    refused = client.post(
        f"/dashboard/api/sources/{source['id']}/enabled",
        headers={"X-CSRF-Token": csrf},
        json={"enabled": True, "confirm": ""},
    )
    assert refused.status_code == 400

    blocked_without_test = client.post(
        f"/dashboard/api/sources/{source['id']}/enabled",
        headers={"X-CSRF-Token": csrf},
        json={"enabled": True, "confirm": "ENABLE SOURCE"},
    )
    assert blocked_without_test.status_code == 409

    now = core.utcnow()
    db.add(
        models.PaymentIntent(
            business_id=business["id"],
            source_id=source["id"],
            external_id="verified-acceptance",
            idempotency_key="verified-acceptance",
            base_amount_minor=100,
            currency="USD",
            metadata_json='{"acceptance_test":true,"result":"VERIFIED"}',
            checkout_expires_at=now + timedelta(minutes=8),
            history_expires_at=now + timedelta(days=7),
        )
    )
    db.commit()

    enabled = client.post(
        f"/dashboard/api/sources/{source['id']}/enabled",
        headers={"X-CSRF-Token": csrf},
        json={"enabled": True, "confirm": "ENABLE SOURCE"},
    )
    assert enabled.status_code == 200
    assert enabled.json()["enabled"] is True
    assert enabled.json()["ready"] is True

    disabled = client.post(
        f"/dashboard/api/sources/{source['id']}/enabled",
        headers={"X-CSRF-Token": csrf},
        json={"enabled": False, "confirm": ""},
    )
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False


def test_dashboard_overview_is_sanitized():
    csrf = login()
    response = client.get("/dashboard/api/overview")
    assert response.status_code == 200
    body = response.json()
    assert "setup" in body
    assert "safety" in body
    assert "runtime" in body
    assert SECRET not in str(body)
    assert "internal_secret" not in body
    assert "internal_secret" not in body["setup"]

    logout = client.post(
        "/dashboard/api/logout",
        headers={"X-CSRF-Token": csrf},
        json={},
    )
    assert logout.status_code == 200
