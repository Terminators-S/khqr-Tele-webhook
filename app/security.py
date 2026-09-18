import hashlib
import hmac
import secrets


def generate_api_key() -> str:
    return "khqr_live_" + secrets.token_urlsafe(32)


def generate_webhook_secret() -> str:
    return "whsec_" + secrets.token_urlsafe(32)


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def verify_secret(value: str, expected_hash: str) -> bool:
    return hmac.compare_digest(hash_secret(value), expected_hash)


def sign_webhook(secret: str, timestamp: str, body: bytes) -> str:
    payload = timestamp.encode("ascii") + b"." + body
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
