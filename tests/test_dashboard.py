from datetime import timedelta

from fastapi.testclient import TestClient

from app import core, models
import app.khqr_asset as khqr_asset
from app.main import app
from tests.khqr_test_utils import khqr_png_bytes, valid_khqr_payload


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


def use_asset_root(monkeypatch, tmp_path):
    settings = khqr_asset.get_settings()
    monkeypatch.setattr(
        settings,
        "khqr_asset_root",
        str(tmp_path / "store-assets"),
    )


def create_store(csrf, name="Dashboard Store"):
    response = client.post(
        "/dashboard/api/stores",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": name,
            "currency": "USD",
            "webhook_url": None,
        },
    )
    assert response.status_code == 200
    return response.json()


def upload_qr(csrf, source_id, image_bytes, filename="merchant.png"):
    return client.post(
        f"/dashboard/api/sources/{source_id}/khqr-image",
        headers={"X-CSRF-Token": csrf},
        files={"file": (filename, image_bytes, "image/png")},
    )


def test_dashboard_page_and_auth_gate():
    page = client.get("/dashboard")
    assert page.status_code == 200
    assert "KHQR Store Setup" in page.text
    assert "Upload your KHQR" in page.text
    assert "Choose KHQR image" in page.text
    assert "Bakong account ID" not in page.text
    assert "Static KHQR payload" not in page.text
    assert "Telegram group ID" not in page.text

    denied = client.get("/dashboard/api/overview")
    assert denied.status_code == 401

    wrong = client.post(
        "/dashboard/api/login",
        json={"secret": "definitely-wrong-secret"},
    )
    assert wrong.status_code == 401
def test_create_store_makes_isolated_disabled_source():
    csrf = login()
    store = create_store(csrf, "New Merchant")

    assert store["name"] == "New Merchant"
    assert store["api_key"].startswith("khqr_live_")
    assert store["source"]["enabled"] is False
    assert store["source"]["currency"] == "USD"
    assert store["source"]["khqr_image_configured"] is False
    assert store["source"]["configured"] is False

    rows = client.get("/dashboard/api/stores")
    assert rows.status_code == 200
    assert any(row["id"] == store["id"] for row in rows.json())


def test_store_create_requires_csrf():
    login()
    response = client.post(
        "/dashboard/api/stores",
        json={"name": "No CSRF Store", "currency": "USD"},
    )
    assert response.status_code == 403


def test_uploaded_khqr_is_decoded_validated_and_served_byte_for_byte(
    monkeypatch, tmp_path
):
    use_asset_root(monkeypatch, tmp_path)
    csrf = login()
    store = create_store(csrf, "Image Store")
    source = store["source"]

    original = khqr_png_bytes(
        account_id="image-store@aba",
        merchant_name="Image Store",
        currency="USD",
    )
    uploaded = upload_qr(csrf, source["id"], original)
    assert uploaded.status_code == 200
    body = uploaded.json()
    assert body["upload_valid"] is True
    assert body["khqr_valid"] is True
    assert body["khqr_image_configured"] is True
    assert body["khqr_account_id"] == "image-store@aba"
    assert body["khqr_merchant_name"] == "Image Store"
    assert body["currency"] == "USD"
    assert body["enabled"] is False

    served = client.get(body["khqr_image_url"])
    assert served.status_code == 200
    assert served.headers["content-type"] == "image/png"
    assert served.content == original

    legacy_alias = client.get(
        f"/dashboard/api/sources/{source['id']}/qr.png"
    )
    assert legacy_alias.status_code == 200
    assert legacy_alias.content == original
def test_qr_upload_rejects_non_khqr_image(monkeypatch, tmp_path):
    use_asset_root(monkeypatch, tmp_path)
    csrf = login()
    store = create_store(csrf, "Bad QR Store")
    source_id = store["source"]["id"]

    arbitrary_qr = khqr_png_bytes()
    # Corrupt the embedded payload by producing a QR for plain text.
    import io
    import qrcode

    image = qrcode.make("this-is-not-khqr")
    output = io.BytesIO()
    image.save(output, format="PNG")

    response = upload_qr(
        csrf,
        source_id,
        output.getvalue(),
        "not-khqr.png",
    )
    assert response.status_code == 400
    assert "not a valid reusable Cambodia KHQR" in response.json()["detail"]

    rows = client.get("/dashboard/api/sources").json()
    source = next(row for row in rows if row["id"] == source_id)
    assert source["khqr_image_configured"] is False
    assert source["configured"] is False


def test_payload_without_uploaded_image_is_not_dashboard_ready():
    csrf = login()
    business = client.post(
        "/dashboard/api/businesses",
        headers={"X-CSRF-Token": csrf},
        json={"name": "Legacy Store", "slug": "legacy-store"},
    ).json()

    response = client.post(
        "/dashboard/api/sources",
        headers={"X-CSRF-Token": csrf},
        json={
            "business_id": business["id"],
            "name": "Legacy source",
            "currency": "USD",
            "telegram_group_id": -100123123,
            "telegram_sender_id": 123123,
            "merchant_alias": "Legacy Store",
            "static_khqr": valid_khqr_payload(
                account_id="legacy@aba",
                merchant_name="Legacy Store",
            ),
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["static_khqr_configured"] is True
    assert body["khqr_image_configured"] is False
    assert body["khqr_valid"] is False
    assert body["configured"] is False
def test_dashboard_activation_requires_uploaded_qr_and_verified_test(
    db, monkeypatch, tmp_path
):
    use_asset_root(monkeypatch, tmp_path)
    csrf = login()
    store = create_store(csrf, "Safe UI Store")
    source_id = store["source"]["id"]

    core.configure_source_group(db, source_id, -100818181)
    core.configure_source_sender(db, source_id, 818181)

    blocked_no_qr = client.post(
        f"/dashboard/api/sources/{source_id}/enabled",
        headers={"X-CSRF-Token": csrf},
        json={"enabled": True, "confirm": "ENABLE SOURCE"},
    )
    assert blocked_no_qr.status_code == 409
    assert "uploaded verified KHQR" in blocked_no_qr.json()["detail"]

    image = khqr_png_bytes(
        account_id="safe-ui@aba",
        merchant_name="SAFE UI STORE",
    )
    uploaded = upload_qr(csrf, source_id, image)
    assert uploaded.status_code == 200

    blocked_without_test = client.post(
        f"/dashboard/api/sources/{source_id}/enabled",
        headers={"X-CSRF-Token": csrf},
        json={"enabled": True, "confirm": "ENABLE SOURCE"},
    )
    assert blocked_without_test.status_code == 409
    assert "Real Payment Test" in blocked_without_test.json()["detail"]

    now = core.utcnow()
    db.add(
        models.PaymentIntent(
            business_id=store["id"],
            source_id=source_id,
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
        f"/dashboard/api/sources/{source_id}/enabled",
        headers={"X-CSRF-Token": csrf},
        json={"enabled": True, "confirm": "ENABLE SOURCE"},
    )
    assert enabled.status_code == 200
    assert enabled.json()["enabled"] is True
    assert enabled.json()["ready"] is True

    disabled = client.post(
        f"/dashboard/api/sources/{source_id}/enabled",
        headers={"X-CSRF-Token": csrf},
        json={"enabled": False, "confirm": ""},
    )
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False
def test_store_edit_is_scoped_and_currency_locks_after_qr(
    monkeypatch, tmp_path
):
    use_asset_root(monkeypatch, tmp_path)
    csrf = login()
    first = create_store(csrf, "First Store")
    second = create_store(csrf, "Second Store")

    image = khqr_png_bytes(
        account_id="first@aba",
        merchant_name="First Store",
        currency="USD",
    )
    assert upload_qr(
        csrf,
        first["source"]["id"],
        image,
    ).status_code == 200

    renamed = client.post(
        f"/dashboard/api/stores/{second['id']}",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "Second Store Renamed",
            "currency": "KHR",
            "webhook_url": None,
        },
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Second Store Renamed"
    assert renamed.json()["source"]["currency"] == "KHR"

    blocked_currency = client.post(
        f"/dashboard/api/stores/{first['id']}",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "First Store",
            "currency": "KHR",
            "webhook_url": None,
        },
    )
    assert blocked_currency.status_code == 409
    assert "replace the uploaded KHQR" in blocked_currency.json()["detail"]


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
