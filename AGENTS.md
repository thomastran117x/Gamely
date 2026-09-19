# Agent Instructions: Games

Read `CLAUDE.md` before changing this repository. Also read `ARCHITECTURE.md` for system design and
`CONTRIBUTING.md` for the human workflow and validation contract. This file adds agent-specific
execution and pull request requirements.

When making changes:

- Keep the Angular frontend and FastAPI backend independently buildable.
- Preserve strict Python typing; run `cd backend && uv run mypy` and `uv run pytest` for backend changes.
- Add or update focused tests whenever behavior changes. Integration tests are marked `integration` and require Compose services.
- Use the IoC container in `backend/src/main.py` for new backend dependencies. Choose lifetimes deliberately: singleton, scoped, or transient.
- Use shared application exceptions and the established error JSON contract for expected HTTP failures. Do not expose stack traces or raw exception messages.
- Keep all secrets in the ignored root `.env`; update `.env.example` when adding configurable values.
- Keep Compose host ports dynamic. Defaults are frontend `3040` and backend `8040`; internal API communication remains `api:8000`.
- Avoid unrelated refactors and preserve existing user changes.

Before reporting completion, run the relevant build, type-check, test, and Compose-config commands and clearly state any validation that could not run.

## Agent workflow

- Inspect the worktree before changing it. Preserve user changes and never discard unrelated work.
- Work from a branch named `<type>/<short-kebab-name>`. Do not commit directly to `main`.
- Keep the change focused on the requested outcome. Do not add speculative product behavior or
  unrelated cleanup.
- Use a Conventional Commit pull request title in the form
  `type(optional-scope)!: imperative summary`. Explain breaking changes with a
  `BREAKING CHANGE:` entry.
- Fill every applicable section of `.github/PULL_REQUEST_TEMPLATE.md`. Validation must list exact
  commands and outcomes; do not claim an unrun check passed.
- Open a draft pull request when required work or checks remain. Otherwise open it ready for
  review.
- When credentials, network access, and repository permissions are available, commit, push, and
  open the pull request after validation. If any prerequisite is unavailable, provide the proposed
  title, completed body, branch name, and exact blocker in the handoff.
- Pull requests are squash-merged. Ensure the final title is suitable as the commit subject.
