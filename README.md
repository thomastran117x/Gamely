# Games

Games is an early-stage full-stack application built as an Angular server-rendered frontend and a
FastAPI backend. The backend currently provides account authentication, email verification,
password recovery, OAuth token verification, session management, rate limiting, and asynchronous
email delivery. The frontend is still the Angular starter and is not yet connected to those flows.

The repository is under active development. Interfaces and deployment practices may change before
the first tagged release.

## Technology

- Angular 20, TypeScript, SSR, and Tailwind CSS
- FastAPI, Python 3.13, Pydantic, and SQLAlchemy
- PostgreSQL, Redis, OpenSearch, and RabbitMQ
- AWS S3 for features that require object storage
- Docker Compose for the local application stack
- GitHub Actions for formatting, type checks, builds, and tests

## Repository layout

```text
.
|-- frontend/              Angular SSR application
|-- backend/               FastAPI application, workers, migrations, and tests
|-- backend/config/        Layered backend configuration
|-- .github/               CI, pull request template, and issue forms
|-- docker-compose.yml     Local application and infrastructure stack
`-- .env.example           Environment and Compose value template
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for component boundaries and runtime flows.

## Quick start with Docker Compose

Prerequisites are Docker with the Compose plugin and Git.

1. Copy `.env.example` to `.env`.
2. Set `AUTH_JWT_SECRET` in `.env` to a random value of at least 32 characters. Add SMTP, OAuth,
   and AWS values only when exercising those integrations.
3. Start the stack from the repository root:

   ```bash
   docker compose up --build
   ```

   Compose runs the committed Alembic migrations in a one-shot `migrate` service after PostgreSQL
   is healthy. The API starts only after all pending migrations succeed.

4. Open the frontend at <http://localhost:3040> or the API documentation at
   <http://127.0.0.1:8040/docs>.

Default host endpoints are:

| Service             | Endpoint                 |
| ------------------- | ------------------------ |
| Angular frontend    | `http://localhost:3040`  |
| FastAPI backend     | `http://127.0.0.1:8040`  |
| PostgreSQL          | `localhost:5432`         |
| Redis               | `localhost:6379`         |
| OpenSearch          | `http://localhost:9200`  |
| RabbitMQ            | `localhost:5672`         |
| RabbitMQ management | `http://localhost:15672` |

Host ports can be changed in `.env`. Compose provides `http://api:8000` as the internal API base
URL for the frontend's future server-side integration, regardless of the backend host port.

Stop the stack with `docker compose down`. This preserves named volumes; use Docker's volume
management commands only when you intentionally want to remove local data.

## Native development

For backend work, install [uv](https://docs.astral.sh/uv/) and Python 3.13:

```bash
cd backend
uv sync --all-groups
uv run uvicorn src.main:app --reload
```

The native API uses `http://127.0.0.1:8000` unless a different port is passed to Uvicorn. Its
PostgreSQL, Redis, OpenSearch, and RabbitMQ URLs default to services on localhost. If you started
from `.env.example`, replace the Docker service names in those URLs with `127.0.0.1` before running
the backend directly on the host.

For frontend work, install Node.js 22:

```bash
cd frontend
npm ci
npm start
```

Angular's native development server uses <http://localhost:4200>. Port `3040` is the Compose host
port, not the Angular CLI default.

## Validation

Run checks for the area you change. The full local matrix is:

```bash
cd frontend
npm run format:check
npm run typecheck
npm run build
npm test -- --watch=false --browsers=ChromeHeadless
```

```bash
cd backend
uv run ruff format --check
uv run mypy
uv run pytest
uv run pytest -m integration
```

Integration tests require a running Docker engine; Testcontainers creates and removes their
infrastructure. Validate Compose changes from the repository root:

```bash
docker compose --env-file .env.example config
```

## Project documentation

- [Architecture and roadmap](ARCHITECTURE.md)
- [Contributing guide](CONTRIBUTING.md)
- [Backend development](backend/README.md)
- [Frontend development](frontend/README.md)
- [Support](SUPPORT.md)
- [Security policy](SECURITY.md)
- [Code of Conduct](CODE_OF_CONDUCT.md)
- [Changelog](CHANGELOG.md)

## License

Games is available under the [MIT License](LICENSE). The adapted
[Code of Conduct](CODE_OF_CONDUCT.md) is separately available under CC BY-SA 4.0 as noted in that
file.
