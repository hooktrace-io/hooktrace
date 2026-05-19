# Contributing to hooktrace

Thanks for considering a contribution. This is a learning-focused side-project — contributions are welcome but please read this first.

## Project status

This is a personal learning project to practice modern DevOps (now on Fly.io, after a migration from GCP in 2026-05). The maintainer reserves the right to refuse contributions that don't align with the learning goals or the design philosophy in [`docs/specs/`](docs/specs/).

If you're unsure whether a change is welcome, **open an issue first** to discuss.

## Development setup

Requires:
- Python 3.13 (`pyenv install 3.13`)
- [uv](https://github.com/astral-sh/uv) for dependency management
- Docker + docker-compose (for running the stack locally)

```bash
git clone https://github.com/hooktrace-io/hooktrace.git
cd hooktrace
uv sync
uv run pre-commit install   # one-time, sets up git hooks
make up                      # starts the full stack on docker-compose
make test                    # runs unit + integration tests
```

## Branch strategy

- `main` is the deployment branch — every merge triggers a production deploy via GitHub Actions (web + ingestor + worker via `.github/workflows/deploy.yml`)
- Open a PR from a feature branch (`feat/...`, `fix/...`, `docs/...`, `chore/...`, etc.)
- Squash-merge into `main` once CI is green

## Commit conventions

We use [Conventional Commits](https://www.conventionalcommits.org/):

- `feat:` new feature
- `fix:` bug fix
- `docs:` documentation changes
- `refactor:` code change that neither fixes a bug nor adds a feature
- `test:` adding or fixing tests
- `chore:` tooling, dependencies, CI
- `style:` formatting, no logic change
- `ci:` CI configuration

Scopes are encouraged: `feat(web):`, `fix(infra):`, `test(domain):`.

**No `Co-Authored-By: Claude` trailer.** AI assistance is disclosed in the README; we keep it off individual commits.

## Code quality

Before submitting a PR, ensure:

```bash
make lint        # ruff check + format
make type        # mypy strict
make test        # full test suite
```

The pre-commit hook will catch most issues automatically.

### Test coverage

The CI enforces ≥ 95% coverage on the `domain` and `application` layers (the units that should be testable in isolation without I/O). Infrastructure and web layers are integration-tested with testcontainers — they don't contribute to the gate.

## Architecture

See [`docs/specs/`](docs/specs/) for the design rationale and [`docs/superpowers/plans/`](docs/superpowers/plans/) for past implementation plans. The architecture follows:

- **Clean Architecture** — domain / application / infrastructure / web layers, with strict inward-only dependencies. Domain has zero external imports; use cases depend on port interfaces (ABCs), never concretes.
- **Test-Driven Development** — write the failing test before the implementation
- **Conventional Commits**

Look at existing tests (`tests/unit/`, `tests/integration/`) for patterns before adding new code.

### Where to find things

| Layer | Location | Notes |
|---|---|---|
| Domain | `src/webhook_inspector/domain/` | Entities + ports (ABCs) + pure domain services. No I/O. |
| Application | `src/webhook_inspector/application/use_cases/` | Use cases orchestrating ports. Async, no framework imports. |
| Infrastructure | `src/webhook_inspector/infrastructure/` | Postgres repos, R2 blob storage, arq queue, OTEL adapter. |
| Web | `src/webhook_inspector/web/{app,ingestor}/` | FastAPI routes + Jinja templates. |
| Jobs | `src/webhook_inspector/jobs/` | arq worker entry + cron jobs (cleaner, abuse_scan). |

## What we welcome

- Bug fixes with a regression test
- Documentation improvements
- New tests for under-tested areas
- DevOps improvements (CI speedups, infra hardening, observability)
- Performance improvements with a benchmark

## What we typically refuse

- Pure styling / refactor PRs without a clear benefit
- New features that aren't in the roadmap (open an issue first)
- Changes that break tests or weaken type safety
- Adding new layers / abstractions beyond what the task requires (cf. the "don't add features beyond what the task requires" rule in [`CLAUDE.md`](CLAUDE.md))

## Security

For vulnerability reports, **do not open a public issue**. See [`SECURITY.md`](SECURITY.md) for the disclosure process via private GitHub Security Advisory.

## Branch protection (maintainer)

The `main` branch should have the following rules configured (Settings → Branches in the GitHub UI):

- Require a pull request before merging
- Require status checks to pass before merging:
  - `lint`
  - `type`
  - `unit`
  - `integration`
  - `Analyze Python` (CodeQL)
  - `scan` (Trivy)
- Require linear history (squash-merges only)
- Require conversation resolution before merging

These rules can't be enforced from the repo — they live in GitHub's repo settings. The settings are documented here so a new maintainer (or future-you on a new machine) knows what state is expected.

## Questions

Open a [discussion](https://github.com/hooktrace-io/hooktrace/discussions) or an issue tagged `question`.
