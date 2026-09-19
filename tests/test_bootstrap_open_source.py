from scripts import bootstrap_open_source as bootstrap


def test_generate_values_are_secure_and_url_safe():
    values = bootstrap.generate_values(
        telegram_api_id="12345",
        telegram_api_hash="abcdef",
        cookie_secure=True,
    )
    assert len(values["POSTGRES_PASSWORD"]) >= 32
    assert len(values["INTERNAL_SECRET"]) >= 48
    assert values["POSTGRES_PASSWORD"] in values["DATABASE_URL"]
    assert values["TELEGRAM_API_ID"] == "12345"
    assert values["TELEGRAM_API_HASH"] == "abcdef"
    assert values["DASHBOARD_COOKIE_SECURE"] == "true"


def test_render_env_replaces_values_without_duplication():
    template = (
        "POSTGRES_PASSWORD=old\n"
        "DATABASE_URL=old-url\n"
        "INTERNAL_SECRET=old-secret\n"
        "DASHBOARD_COOKIE_SECURE=false\n"
        "KEEP_ME=yes\n"
    )
    values = {
        "POSTGRES_PASSWORD": "new-pass",
        "DATABASE_URL": "new-url",
        "INTERNAL_SECRET": "new-secret",
        "DASHBOARD_COOKIE_SECURE": "true",
    }
    rendered = bootstrap.render_env(template, values)
    assert rendered.count("POSTGRES_PASSWORD=") == 1
    assert "POSTGRES_PASSWORD=new-pass" in rendered
    assert "DATABASE_URL=new-url" in rendered
    assert "INTERNAL_SECRET=new-secret" in rendered
    assert "DASHBOARD_COOKIE_SECURE=true" in rendered
    assert "KEEP_ME=yes" in rendered
