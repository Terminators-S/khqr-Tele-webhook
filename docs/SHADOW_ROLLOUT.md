# Telegram Shadow Rollout

## Safety invariant
Shadow collection may observe and persist real ABA notifications, but it must not settle payment intents or notify client fulfillment.

`TELEGRAM_SHADOW_ONLY=true` is the default and is also forced in the Compose Telegram service. The collector writes evidence with state `SHADOW`. Settlement workers only consume `RECEIVED`, direct settlement refuses non-RECEIVED rows, and Trx-ID recovery refuses SHADOW evidence. Two additional fuses default off: `ALLOW_LIVE_TELEGRAM=false` and `ALLOW_SHADOW_PROMOTION=false`.

## Shadow phase
1. Create a dedicated standalone Telegram session with `scripts/create_telegram_shadow_session.py`. Never copy or reuse Creative Studio's active `admin_session.session`.
2. Configure a PaymentSource with the exact Telegram group ID, expected sender ID, merchant alias and static KHQR.
3. Keep CreativeStudioWeb unchanged and authoritative.
4. Run `scripts/telegram_shadow_preflight.py`; do not start the collector unless it passes.
5. Start only the standalone Telegram profile with `TELEGRAM_SHADOW_ONLY=true`.
6. Compare parsed Trx ID, amount, timestamp and source against Creative Studio history.
7. Watch `/internal/runtime` for SHADOW counts, rejects and quarantine behavior.
8. Keep both live activation and shadow promotion fuses false during observation.

## Cutover gate
Cutover requires explicit approval after live shadow parity. Stop/coordinate the previous listener first. Enable `ALLOW_SHADOW_PROMOTION=true` only for the reviewed promotion window, promote reviewed evidence with `POST /internal/sources/{source_id}/promote-shadow`, then disable promotion again. To activate live collection, set `TELEGRAM_SHADOW_ONLY=false` and `ALLOW_LIVE_TELEGRAM=true` together and restart the collector. The two independent fuses prevent observation from silently becoming payment authority.

Rollback is to stop the standalone collector/client integration and leave Creative Studio's existing payment path untouched.
