# Verification — 2026-09-18

Verified against PostgreSQL, not only SQLite.

- Python compile: PASS.
- Alembic initial migration from empty PostgreSQL: PASS.
- Alembic schema drift check: PASS, no new operations.
- Focused test suite: 29/29 PASS.
- 100 concurrent same-base-amount intents: 30 DUAL reservations and 70 safe REMARK_PRIMARY overflow.
- 16 concurrent settlement attempts for one evidence row: one allocation, one paid amount.
- 8 SKIP LOCKED settlement workers: 40 seeded evidence rows processed exactly once.
- Webhook outbox signing/delivery test: PASS.
- Docker image build/import: PASS.
- Compose API/PostgreSQL/settlement/webhook startup: PASS.
- HTTP health/ready semantics: PASS.
- Container HTTP intent -> internal ABA evidence -> background settlement -> HTTP PAID: PASS.
- Real Creative Studio shadow parser: 35/35 journal ABA notifications parsed.
- Live Creative Studio PostgreSQL parity: 35/35 Trx IDs present and 35/35 amounts equal.
- Shadow evidence guard: cannot settle/recover before explicit promotion; promotion then settlement PASS.
- Container shadow smoke: active settlement worker left SHADOW evidence PENDING; explicit promotion changed it to RECEIVED and the worker then settled PAID.
- Shadow collector preflight: API credentials configured; currently blocks only because the dedicated standalone Telegram session does not yet exist.
- Non-zero cent fingerprint accounting: payable offset is part of required amount and is not reported as excess; split-payment regression PASS.
- Local RC gate from a fresh PostgreSQL volume: PASS.
- Wheel build includes both app/ and khqr_sdk/: PASS.
- Fresh core deployment after gate cleanup: API/PostgreSQL/settlement/webhook healthy; database starts empty.

Known limitation: retained real ABA samples contain no non-empty payer Remark. Non-empty Remark parsing is covered by fixtures, not yet observed in real retained production evidence.

No CreativeStudioWeb production payment code was changed and the standalone Telegram collector has not been attached to the live group.
