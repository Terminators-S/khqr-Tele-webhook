.PHONY: test rc-gate deploy-core stop-core shadow-preflight

test:
	.venv/bin/pytest -q

rc-gate:
	./scripts/rc_gate_local.sh

deploy-core:
	./scripts/deploy_core.sh

stop-core:
	docker compose stop api settlement webhook

shadow-preflight:
	.venv/bin/python scripts/telegram_shadow_preflight.py
