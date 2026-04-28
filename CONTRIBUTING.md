# Contributing to kodezart

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) package manager
- Git
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) (for live
  tests)

## Dev Setup

```bash
git clone https://github.com/YalDan/kodezart.git
cd kodezart
uv sync --all-groups
cp .env.example .env
make check
```

## Project Structure

| Directory                     | Responsibility                                   |
| ----------------------------- | ------------------------------------------------ |
| `src/kodezart/adapters/`      | Infrastructure protocol implementations          |
| `src/kodezart/agents/`        | Reserved for future agent definitions            |
| `src/kodezart/api/`           | FastAPI routers and endpoints                    |
| `src/kodezart/chains/`        | LangGraph workflow graphs                        |
| `src/kodezart/core/`          | Config, logging, protocol definitions            |
| `src/kodezart/domain/`        | Pure functions, no I/O                           |
| `src/kodezart/handlers/`      | Request unpacking, delegation to services        |
| `src/kodezart/prompts/`       | Claude prompt templates                          |
| `src/kodezart/services/`      | Orchestration layer                              |
| `src/kodezart/types/`         | Pydantic models by concern                       |
| `src/kodezart/types/domain/`  | Domain value objects and event types             |
| `src/kodezart/types/requests/`| API request models                               |
| `src/kodezart/types/responses/`| API response models                             |
| `src/kodezart/utils/`         | Standalone helpers                               |

## Code Style

- **Linter**: Ruff with rules `E,W,F,I,B,C4,UP,N,ANN,S,A,ARG,RUF`
- **Line length**: 88
- **Formatter**: `ruff format` (double quotes, space indent)
- **Type checker**: mypy strict mode with `pydantic.mypy` plugin
- **Docstrings**: PEP 257 required on all public APIs
- **Naming**: `CamelCaseModel` for all API-facing Pydantic types

Run `make format` to auto-format and `make lint-fix` to auto-fix lint issues.

## Testing

```bash
make test                     # run all tests (excluding live)
pytest -m live                # run live Claude CLI tests
```

### Fake Adapters Pattern

Tests use fake implementations of protocols instead of mocks:

- `FakeAgentExecutor` - yields predetermined events
- `FakeWorkspaceProvider` - returns fixed workspace paths
- `FakeGitService` - records git operations without subprocess calls

This pattern provides compile-time safety and avoids brittle mock
configurations.

### Live Tests

Tests marked with `@pytest.mark.live` require a running Claude Code CLI and are
skipped by default. Run them explicitly with `pytest -m live`.

## Architecture Conventions

- **Protocol-based DI**: All cross-boundary dependencies are defined as the 12
  protocols in `core/protocols.py`
- **No inheritance coupling**: Adapters implement protocols, not abstract base
  classes
- **Pure domain**: `domain/` contains only pure functions with no I/O or side
  effects
- **Handlers delegate to services**: Handlers unpack requests and delegate to
  service methods
- **Services orchestrate**: Services compose protocol collaborators to implement
  business workflows
- **Adapters implement protocols**: Each adapter satisfies one or more protocol
  interfaces

See [docs/architecture.md](docs/architecture.md) for the full architecture
guide.

## PR Process

1. Branch from `main`
2. Make your changes
3. Run `make check` (CI runs the same pipeline: lint, type-check, test)
4. Describe your changes in the PR

## Adding a New Adapter

1. **Define protocol** in `core/protocols.py` if a new port is needed
2. **Implement** the adapter in `adapters/`
3. **Wire** it in `main.py` `lifespan()` function
4. **Add a fake** in `tests/` for testing
5. Run `make check` to verify
