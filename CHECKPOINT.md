# Checkpoint — 2026-09-19

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
- Reusable client onboarding command added; credentials are owner-only (0600) and secrets stay out of stdout.
- No-money integration smoke PASS through the running API, settlement worker, and webhook worker: SDK intent -> synthetic RECEIVED evidence -> PAID -> verified signed webhook.
- Client onboarding supports staged disabled sources when the trusted Telegram sender is not yet known.
- Guarded source activation API added: sender can change only while disabled; enabling fails closed unless source configuration is complete.
- Focused onboarding/API/SDK/webhook suite: 15/15 PASS on isolated PostgreSQL.
- Full isolated PostgreSQL suite after activation slice: 36/36 PASS.
- PostgreSQL concurrency gates after activation slice: 30 DUAL + 70 overflow, one duplicate-settlement winner, 40/40 SKIP LOCKED exactly once.
- Fresh Docker image build/import PASS after the client-integration slice.
- Core redeployed healthy on loopback after destructive gate cleanup.
- First consuming project staged: Creative Studio Business + ABA source exists in standalone with real source identity, source disabled, sender unset, 0 intents, 0 evidence. CreativeStudioWeb remains authoritative.
- Staged Creative Studio credentials are stored only under git-ignored runtime/ with mode 0600.

## Next task
Create a dedicated standalone Telegram session under runtime/ and run SHADOW-only observation against the staged Creative Studio source. Determine the real trusted ABA notification sender from the dedicated session, compare parsed Trx ID/amount/time/source against Creative Studio history, then configure the sender through the guarded internal endpoint. Keep the source disabled until shadow parity is reviewed. Do not reuse Creative Studio's active admin_session.session and do not enable ALLOW_SHADOW_PROMOTION or ALLOW_LIVE_TELEGRAM.
