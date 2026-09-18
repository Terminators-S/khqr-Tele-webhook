# KHQR Self-Develop Operating Guide

This repository is the standalone KHQR/ABA payment service extracted from CreativeStudioWeb.

## Core contracts
- PostgreSQL is the production authority.
- Money is integer minor units; never use float equality for new matching logic.
- Checkout TTL is 8 minutes.
- Late automatic amount-match grace is 5 minutes.
- Cent-offset reservations live for 13 minutes total.
- Offset candidates are 0..29 cents. If exhausted, continue in REMARK_PRIMARY mode.
- Exact Remark is a first-class payment identity. Transaction ID is immutable and one-use.
- Amount-only matching is allowed only while a unique reservation is live.
- After 13 minutes, recycled amount-only matching is forbidden; use exact Remark and/or Trx-ID recovery.
- Telegram is evidence transport, never payment authority.
- Payment settlement is worker-owned and idempotent.
- Creative Studio order/fulfillment/provider logic stays outside this service.

## Change discipline
Read README.md and CHECKPOINT.md before editing payment behavior.
Keep API, collector, settlement worker, and webhook worker separable.
Do not weaken unique constraints, row locks, idempotency, or source/business isolation.
Run focused tests after each slice and PostgreSQL concurrency gates before calling payment logic production-ready.
