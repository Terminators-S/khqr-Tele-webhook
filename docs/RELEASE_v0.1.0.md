# KHQR Self-Develop v0.1.0 Core Release

Date: 2026-09-18

## Release scope
This release ships the standalone core only: PostgreSQL authority, payment-intent API, cent-fingerprint allocation, immutable ABA evidence, idempotent settlement worker, signed webhook outbox, and Python client SDK.

Telegram collection is intentionally excluded from the core release. Its Compose profile remains optional and shadow-only by default. CreativeStudioWeb remains the live commerce/payment authority until a later cutover.

## Release gate
The local isolated RC gate passed from an empty PostgreSQL volume:

- Release environment preflight: PASS.
- Alembic migration from zero: PASS.
- Alembic drift check: PASS.
- Test suite: 29/29 PASS.
- 100-way same-price allocation: 30 DUAL + 70 REMARK_PRIMARY.
- Duplicate settlement contention: exactly one allocation.
- 8 SKIP LOCKED workers: 40/40 evidence rows processed exactly once.
- Non-zero cent fingerprint accounting: PASS; offset is required amount, not excess.
- Split-payment accounting to full fingerprinted amount: PASS.
- Python wheel build: PASS and contains both app/ and khqr_sdk/.
- Docker image import: PASS.
- Destructive gate data removed after verification.

## Artifacts
- Wheel: dist/khqr_self_develop-0.1.0-py3-none-any.whl
- Wheel SHA256: a3907b39d6d6f2d2bf8b2c09480565d88bf674250a671e1ef46d862c6ca2da55
- Canonical image tag: khqr-self-develop:0.1.0
- Canonical image ID: sha256:3dceb56fe9eba344f7920fbd2de2c5a60c551ca8970b89d4cff55c039dabaafc

## Deployed core
The fresh core stack is running loopback-only:
- API: 127.0.0.1:8088
- PostgreSQL: 127.0.0.1:55432
- settlement worker: running
- webhook worker: running
- API health: healthy
- Database after deployment: businesses=0, intents=0, evidence=0

The database and internal API credentials were rotated to strong random values and .env is mode 0600.

## Release commands
- Full destructive isolated proof: make rc-gate
- Non-destructive core deployment: make deploy-core
- Stop core workers/API: make stop-core
- Telegram-only safety check: make shadow-preflight

## Known non-blockers
The test stack emits deprecation warnings from the current Starlette/httpx TestClient combination. They do not affect runtime behavior.

A dedicated standalone Telegram session does not exist yet. This does not block the core API release. Telegram stays disabled/shadow-only until that session is created and the separate shadow parity gate passes.
