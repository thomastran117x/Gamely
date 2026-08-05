# Games backend

## Local setup

1. Install [uv](https://docs.astral.sh/uv/) and copy the repository-root `.env.example` to `.env` and configure AWS S3 if needed.
2. From the repository root, start the API and local infrastructure:

   ```powershell
   docker compose up --build
   ```

   The API is at `http://127.0.0.1:8040`, OpenSearch is at `http://127.0.0.1:9200`, and RabbitMQ management is at `http://127.0.0.1:15672` (`games` / `games`).

3. For local Python development:

   ```powershell
   cd backend
   uv sync --all-groups
   uv run uvicorn src.main:app --reload
   ```

## Database migrations

```powershell
cd backend
uv run alembic upgrade head
uv run alembic revision --autogenerate -m "describe change"
```

## Dependency injection

The application uses a typed IoC container with singleton, scoped, and transient lifetimes. `Settings` and infrastructure services are singletons; `AsyncSession` is request-scoped and is disposed after each request. Register additional services in `build_container` in `src/main.py`.
## Static typing

```powershell
cd backend
uv run mypy
```
## Tests

Fast tests do not need Docker:

```powershell
cd backend
uv run pytest
```

Start Compose first, then run live dependency checks explicitly:

```powershell
uv run pytest -m integration
```

AWS S3 is intentionally not emulated or readiness-checked. Configure valid AWS credentials and `S3_BUCKET` only in environments that use S3-backed features.
