# Checkpoint — 2026-09-18

## Locked decisions
- Checkout TTL: 8 minutes.
- Late automatic match grace: 5 minutes.
- Reservation lifetime: 13 minutes.
- Offset range: 0..29 cents.
- Overflow: REMARK_PRIMARY; checkout must not fail merely because all cent slots are busy.
- Historical recovery default: 7 days using exact Remark and/or Trx ID.
- Amount-only matching after reservation expiry is forbidden.
- Partial and multiple payments are supported; excess is tracked explicitly.
- Conflicting evidence fails closed.
- Webhooks are at-least-once with stable event IDs.
- PostgreSQL row locks/SKIP LOCKED own concurrency.
- Telegram defaults to SHADOW evidence.
- Live Telegram and SHADOW promotion each require a separate explicit enable flag.
- CreativeStudioWeb remains production payment/fulfillment authority until a later approved cutover.

## Verified
- PostgreSQL migration + Alembic drift gate: PASS.
- Focused PostgreSQL suite: 29/29 PASS.
- 100-way same-price allocation: 30 DUAL + 70 REMARK_PRIMARY.
- Duplicate settlement: exactly one allocation.
- 8 SKIP LOCKED workers: 40/40 rows exactly once.
- Docker/Compose API + settlement + webhook path: PASS.
- HTTP intent -> ABA evidence -> worker -> PAID: PASS.
- Shadow evidence stays PENDING while worker runs; explicit promotion then settles PAID: PASS.
- Real Creative Studio ABA journal parse: 35/35.
- Live Creative Studio PostgreSQL Trx/amount parity: 35/35.
- API ID/hash copied into git-ignored standalone .env without disclosure.
- Non-zero cent offset is accounted as required payable amount, not excess: PASS.
- Local RC gate from empty PostgreSQL volume: PASS.
- Python wheel contains both server package and khqr_sdk: PASS.
- Fresh loopback core deployment (API + PostgreSQL + settlement + webhook): healthy with clean database.
- Telegram shadow preflight currently blocks only on missing dedicated standalone session.

## Next task
Core v0.1.0 is release-ready and deployed loopback-only. Highest-impact next work is client integration/onboarding: create a real Business + PaymentSource for the first consuming project, wire the khqr_sdk intent/webhook contract, and run a no-money integration smoke while CreativeStudioWeb remains authoritative. In parallel, create a dedicated Telegram session under runtime/ for later SHADOW-only parity; do not reuse Creative Studio's active admin_session.session and do not enable ALLOW_SHADOW_PROMOTION or ALLOW_LIVE_TELEGRAM.
