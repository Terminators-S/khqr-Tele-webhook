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

    blocked_without_webhook = client.post(
        f"/dashboard/api/sources/{source_id}/enabled",
        headers={"X-CSRF-Token": csrf},
        json={"enabled": True, "confirm": "ENABLE SOURCE"},
    )
    assert blocked_without_webhook.status_code == 409
    assert "project webhook" in blocked_without_webhook.json()["detail"]

    configured_project = client.post(
        f"/dashboard/api/stores/{store['id']}",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": store["name"],
            "currency": "USD",
            "webhook_url": "https://merchant.example/khqr/webhook",
        },
    )
    assert configured_project.status_code == 200
    assert configured_project.json()["webhook_secret"]

    blocked_live_lock = client.post(
        f"/dashboard/api/sources/{source_id}/enabled",
        headers={"X-CSRF-Token": csrf},
        json={"enabled": True, "confirm": "ENABLE SOURCE"},
    )
    assert blocked_live_lock.status_code == 409
    assert "live Telegram collector is locked" in blocked_live_lock.json()["detail"]

    settings = khqr_asset.get_settings()
    monkeypatch.setattr(settings, "telegram_shadow_only", False)
    monkeypatch.setattr(settings, "allow_live_telegram", True)

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


def test_dashboard_integration_manifest_and_secret_rotation(db):
    csrf = login()
    store = create_store(csrf, "Integration Store")
    store_id = store["id"]

    manifest = client.get(f"/dashboard/api/stores/{store_id}/integration")
    assert manifest.status_code == 200
    body = manifest.json()
    assert body["store_id"] == store_id
    assert body["source_id"] == store["source"]["id"]
    assert body["api_key_configured"] is True
    assert body["webhook_configured"] is False
    assert body["source_enabled"] is False
    assert "api_key" not in body
    assert "webhook_secret" not in body

    old_key = store["api_key"]
    rotated = client.post(
        f"/dashboard/api/stores/{store_id}/rotate-api-key",
        headers={"X-CSRF-Token": csrf},
        json={"confirm": "ROTATE API KEY"},
    )
    assert rotated.status_code == 200
    new_key = rotated.json()["api_key"]
    assert new_key.startswith("khqr_live_")
    assert new_key != old_key
    assert core.business_for_api_key(db, new_key).id == store_id
    try:
        core.business_for_api_key(db, old_key)
        assert False, "old API key should be invalid after rotation"
    except core.NotFound:
        pass

    updated = client.post(
        f"/dashboard/api/stores/{store_id}",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "Integration Store",
            "currency": "USD",
            "webhook_url": "https://merchant.example/khqr/webhook",
        },
    )
    assert updated.status_code == 200
    first_secret = updated.json()["webhook_secret"]
    assert first_secret.startswith("whsec_")

    rotated_secret = client.post(
        f"/dashboard/api/stores/{store_id}/rotate-webhook-secret",
        headers={"X-CSRF-Token": csrf},
        json={"confirm": "ROTATE WEBHOOK SECRET"},
    )
    assert rotated_secret.status_code == 200
    assert rotated_secret.json()["webhook_secret"].startswith("whsec_")
    assert rotated_secret.json()["webhook_secret"] != first_secret


def test_store_exposes_multiple_sources_and_bulk_khqr_upload(monkeypatch, tmp_path):
    use_asset_root(monkeypatch, tmp_path)
    csrf = login()
    store = create_store(csrf, "Multi QR Store")
    assert len(store["sources"]) == 1
    first_source_id = store["source"]["id"]

    first_image = khqr_png_bytes(
        account_id="multi-one@aba",
        merchant_name="Multi One",
        currency="USD",
    )
    second_image = khqr_png_bytes(
        account_id="multi-two@aba",
        merchant_name="Multi Two",
        currency="USD",
    )
    response = client.post(
        f"/dashboard/api/stores/{store['id']}/khqr-images",
        headers={"X-CSRF-Token": csrf},
        files=[
            ("files", ("one.png", first_image, "image/png")),
            ("files", ("two.png", second_image, "image/png")),
        ],
    )
    assert response.status_code == 200
    body = response.json()
    assert body["errors"] == []
    assert len(body["created"]) == 2
    assert body["store"]["source_count"] == 2
    assert len(body["store"]["sources"]) == 2
    assert body["store"]["sources"][0]["id"] == first_source_id
    assert all(row["khqr_valid"] for row in body["store"]["sources"])

    account_ids = {row["khqr_account_id"] for row in body["store"]["sources"]}
    assert account_ids == {"multi-one@aba", "multi-two@aba"}

    duplicate = client.post(
        f"/dashboard/api/stores/{store['id']}/khqr-images",
        headers={"X-CSRF-Token": csrf},
        files=[("files", ("duplicate.png", first_image, "image/png"))],
    )
    assert duplicate.status_code == 200
    duplicate_body = duplicate.json()
    assert duplicate_body["created"] == []
    assert len(duplicate_body["errors"]) == 1
    assert "already configured" in duplicate_body["errors"][0]["error"]
    assert duplicate_body["store"]["source_count"] == 2


def test_store_integration_can_target_selected_payment_source(monkeypatch, tmp_path):
    use_asset_root(monkeypatch, tmp_path)
    csrf = login()
    store = create_store(csrf, "Selected Source Store")
    images = [
        khqr_png_bytes(account_id="selected-one@aba", merchant_name="Selected One"),
        khqr_png_bytes(account_id="selected-two@aba", merchant_name="Selected Two"),
    ]
    uploaded = client.post(
        f"/dashboard/api/stores/{store['id']}/khqr-images",
        headers={"X-CSRF-Token": csrf},
        files=[
            ("files", ("selected-one.png", images[0], "image/png")),
            ("files", ("selected-two.png", images[1], "image/png")),
        ],
    ).json()
    second = uploaded["store"]["sources"][1]

    manifest = client.get(
        f"/dashboard/api/stores/{store['id']}/integration?source_id={second['id']}"
    )
    assert manifest.status_code == 200
    assert manifest.json()["source_id"] == second["id"]
    assert manifest.json()["currency"] == second["currency"]


def test_bulk_invalid_qr_does_not_consume_default_source(monkeypatch, tmp_path):
    use_asset_root(monkeypatch, tmp_path)
    csrf = login()
    store = create_store(csrf, "Bulk Invalid Store")
    original_source_id = store["source"]["id"]

    import io
    import qrcode

    bad_image = qrcode.make("not-khqr")
    output = io.BytesIO()
    bad_image.save(output, format="PNG")
    good_image = khqr_png_bytes(
        account_id="bulk-good@aba",
        merchant_name="Bulk Good",
    )
    result = client.post(
        f"/dashboard/api/stores/{store['id']}/khqr-images",
        headers={"X-CSRF-Token": csrf},
        files=[
            ("files", ("bad.png", output.getvalue(), "image/png")),
            ("files", ("good.png", good_image, "image/png")),
        ],
    )
    assert result.status_code == 200
    body = result.json()
    assert len(body["errors"]) == 1
    assert len(body["created"]) == 1
    assert body["created"][0]["id"] == original_source_id
    assert body["store"]["source_count"] == 1
    assert body["store"]["source"]["khqr_account_id"] == "bulk-good@aba"


def test_dashboard_payment_operations_show_remark_and_recover_exact_trx(
    db, business_and_source
):
    business, source, _api_key = business_and_source
    csrf = login()
    intent, request = core.create_payment_intent(
        db,
        business,
        source_id=source.id,
        external_id="BB-INV-1001",
        idempotency_key="BB-INV-1001",
        amount_minor=1500,
        currency="USD",
        metadata={"application": "bikeboss"},
        remark_prefix="BB",
    )
    evidence = core.ingest_evidence(
        db,
        source_id=source.id,
        transport="telegram",
        transport_message_id="msg-bb-recovery",
        sender_id=source.telegram_sender_id,
        raw_text=f"Transaction ID: 178900001234567\nUSD {request.payable_amount_minor / 100:.2f}",
        trx_id="178900001234567",
        amount_minor=request.payable_amount_minor,
        currency="USD",
    )
    evidence.state = "UNMATCHED"
    db.commit()

    listed = client.get("/dashboard/api/intents?limit=20")
    assert listed.status_code == 200
    row = next(item for item in listed.json() if item["id"] == intent.id)
    assert row["remark"].startswith("BB")
    assert row["payable_amount_minor"] == request.payable_amount_minor

    blocked = client.post(
        f"/dashboard/api/intents/{intent.id}/recover",
        headers={"X-CSRF-Token": csrf},
        json={"trx_id": evidence.trx_id, "confirm": "NO"},
    )
    assert blocked.status_code == 400

    recovered = client.post(
        f"/dashboard/api/intents/{intent.id}/recover",
        headers={"X-CSRF-Token": csrf},
        json={"trx_id": evidence.trx_id, "confirm": "RECOVER PAYMENT"},
    )
    assert recovered.status_code == 200
    assert recovered.json()["status"] == "PAID"
    assert recovered.json()["remark"].startswith("BB")
