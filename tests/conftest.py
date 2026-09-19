import os

import pytest
from sqlalchemy import delete, make_url

TEST_DATABASE_URL = os.getenv(
    "KHQR_TEST_DATABASE_URL",
    "postgresql+psycopg2://khqr_test:khqr_test@127.0.0.1:55433/khqr_test",
)
_test_url = make_url(TEST_DATABASE_URL)
if (
    _test_url.host not in {"127.0.0.1", "localhost"}
    or _test_url.port != 55433
    or _test_url.database != "khqr_test"
):
    raise RuntimeError(
        "pytest refuses non-isolated DATABASE_URL; use loopback port 55433 database khqr_test"
    )
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["INTERNAL_SECRET"] = "test-internal-secret-32-characters"

from app import models
from app.db import SessionLocal


@pytest.fixture(autouse=True)
def clean_database():
    db = SessionLocal()
    try:
        for table in reversed(models.Base.metadata.sorted_tables):
            db.execute(delete(table))
        db.commit()
    finally:
        db.close()
    yield


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def business_and_source(db):
    from app import core

    business, api_key, _ = core.create_business(db, "Test Store", "test-store")
    source = core.create_source(
        db,
        business.id,
        name="ABA Main",
        currency="USD",
        telegram_group_id=-1001234567890,
        telegram_sender_id=424242,
        merchant_alias="TEST STORE",
        static_khqr="000201010211TESTSTATICQR6304ABCD",
        enabled=True,
    )
    return business, source, api_key
