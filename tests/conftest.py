import os

import pytest
from dotenv import dotenv_values
from sqlalchemy import delete

_local_env = dotenv_values(".env")
os.environ.setdefault(
    "DATABASE_URL",
    _local_env.get("DATABASE_URL") or "postgresql+psycopg2://khqr:khqr@127.0.0.1:55432/khqr",
)
os.environ.setdefault("INTERNAL_SECRET", "test-internal-secret-32-characters")

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
