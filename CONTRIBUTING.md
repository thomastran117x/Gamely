# Contributing to Games

Thank you for contributing. This guide is the source of truth for the development and pull request
workflow. Read [ARCHITECTURE.md](ARCHITECTURE.md) before changing system boundaries and the relevant
component README for local setup details.

By participating, you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Before starting

- Search existing issues and pull requests before opening a duplicate.
- Use a bug or feature issue for work that benefits from discussion. Small fixes and documentation
  improvements may go directly to a pull request.
- Never include credentials, private vulnerability details, production data, or personal data in an
  issue, commit, test fixture, log, or pull request.
- Report vulnerabilities through the private process in [SECURITY.md](SECURITY.md).

## Local setup

For the complete stack, copy `.env.example` to `.env`, set a random `AUTH_JWT_SECRET` of at least 32
characters, and run:

```bash
docker compose up --build
```

For component-specific native workflows, follow [backend/README.md](backend/README.md) and
[frontend/README.md](frontend/README.md).

## Branches and commits

Create branches from an up-to-date `main` using:

```text
<type>/<short-kebab-name>
```

Examples are `feat/game-library`, `fix/refresh-cookie`, and `docs/project-documentation`.

Use these Conventional Commit types for branch prefixes, commits, and pull request titles:

- `feat`: user-visible capability
- `fix`: user-visible defect correction
- `docs`: documentation only
- `refactor`: internal change without changed behavior
- `test`: test-only change
- `perf`: performance improvement
- `build`: build system or dependency change
- `ci`: continuous-integration change
- `chore`: maintenance not covered above
- `revert`: revert an earlier change

Pull request titles use `type(optional-scope)!: imperative summary`. The scope is optional. Use `!`
and a `BREAKING CHANGE:` body entry whenever consumers must change their behavior. Examples:

```text
feat(auth): add passkey challenge
fix(frontend): preserve return URL after login
docs: clarify local ports
feat(api)!: replace the session response
```

Pull requests are squash-merged. The final pull request title becomes the commit subject on `main`,
so it must follow this format even if intermediate commits do not.

## Development expectations

- Keep each pull request focused on one concern and avoid unrelated refactors.
- Preserve strict Python typing and Angular's strict TypeScript configuration.
- Add or update focused tests whenever behavior changes.
- Register backend dependencies in `build_container` and choose singleton, scoped, or transient
  lifetime deliberately.
- Use shared application exceptions and the established error JSON contract for expected HTTP
  failures.
- Add an Alembic migration for every schema change. Do not create tables during application
  startup.
- Keep secrets in the ignored root `.env`. Update `.env.example` with safe values when configuration
  changes.
- Keep Compose host ports configurable and internal API traffic on `api:8000`.
- Update public documentation and [CHANGELOG.md](CHANGELOG.md) when a user-visible change requires
  it.

## Validation

Run every check relevant to the files you change.

| Change                                 | Required checks                                                             |
| -------------------------------------- | --------------------------------------------------------------------------- |
| Frontend                               | `npm run format:check`, `npm run typecheck`, `npm run build`, focused tests |
| Backend                                | `uv run ruff format --check`, `uv run mypy`, `uv run pytest`                |
| Infrastructure or integration behavior | Backend checks plus `uv run pytest -m integration`                          |
| Compose or environment                 | `docker compose --env-file .env.example config`                             |
| Documentation                          | Link, command, port, heading, and formatting review                         |

Run frontend commands from `frontend/` and backend commands from `backend/`. Integration tests
require Docker. If a required check cannot run, explain why in the pull request; do not report it as
passing.

## Pull requests

Before requesting review:

1. Rebase or otherwise update the branch from `main` without overwriting other contributors' work.
2. Complete every applicable section of the pull request template.
3. Describe the motivation and observable result, not just the files changed.
4. Include exact commands and outcomes under Validation.
5. Add screenshots for visual changes, or state why they do not apply.
6. Disclose migrations, configuration changes, compatibility concerns, security impact, rollout
   steps, and rollback considerations.
7. Link related issues with GitHub closing keywords when the pull request fully resolves them.

Open a draft pull request while required work or validation remains. Mark it ready only when the
change is complete and reviewable. Address review feedback with additional commits; the final merge
will squash them.

## Releases and changelog

Games uses one repository-wide Semantic Version. Git tags are authoritative; component manifest
versions will be aligned as part of the first release rather than in unrelated changes.

Record meaningful user-visible additions, changes, deprecations, removals, fixes, and security work
under `Unreleased` in [CHANGELOG.md](CHANGELOG.md). At release time, move those entries to a dated
version section, create the matching tag, and publish release notes. Do not reconstruct untagged
historical releases.
