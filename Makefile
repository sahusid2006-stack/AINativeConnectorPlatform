.PHONY: dev stop status logs verify-setup verify-config verify-restart

dev:
	docker compose up --build --wait --wait-timeout 180

stop:
	docker compose down

status:
	docker compose ps

logs:
	docker compose logs --tail 100

verify-config:
	python3 -m unittest discover -s tests -p 'test_setup.py' -v

verify-setup: verify-config
	python3 tests/smoke_setup.py

verify-restart: verify-config
	python3 tests/smoke_setup.py --restart
