.PHONY: dev stop migrate worker beat test logs shell clean

#  Infrastructure 
dev:
	docker compose up --build

dev-d:
	docker compose up --build -d

stop:
	docker compose down

clean:
	docker compose down -v --remove-orphans

# Database
migrate:
	docker compose exec api alembic upgrade head

migrate-down:
	docker compose exec api alembic downgrade -1

migrate-gen:
	@read -p "Migration message: " msg; \
	docker compose exec api alembic revision --autogenerate -m "$$msg"

# Workers
worker:
	celery -A app.workers.celery_app worker --loglevel=info --concurrency=4

beat:
	celery -A app.workers.celery_app beat --loglevel=info

# Testing
test:
	docker compose exec api pytest tests/ -v

test-local:
	pytest tests/ -v

# Observability
logs:
	docker compose logs -f

logs-api:
	docker compose logs -f api

logs-worker:
	docker compose logs -f worker

# Dev utilities
shell:
	docker compose exec api python

lint:
	ruff check app/ tests/
	mypy app/

fmt:
	ruff format app/ tests/
