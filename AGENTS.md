# Agent instructions (Spookhub)

Read this first. Then read only the docs your task touches.

## Doc map

- `docs/architecture.md`: backend modules, data model, reliability, cue rules, API/WS.
- `docs/features.md`: user-facing behavior per page.
- `docs/decisions.md`: open-spec decisions. Append, never silently change.
- `docs/development.md`: dev setup, simulator, checks.
- `docs/operations.md`: deploy, paths, hardware, troubleshooting.
- `docs/roadmap/`: phase plans. Each task has a status checkbox.
- `docs/spec-v1.md`: frozen. Never edit.

## Keep docs current (mandatory)

Every change that alters behavior, API, data model, config, deploy or dev workflow updates the
matching doc **in the same commit**:

| You changed | Update |
|---|---|
| Backend module, model field, migration, REST route, WS message type | `docs/architecture.md` |
| UI page, user-visible behavior, keyboard shortcut | `docs/features.md` |
| A choice between valid options | new row in `docs/decisions.md` |
| Config key, install/deploy script, hardware requirement | `docs/operations.md` (+ `deploy/config.example.toml`) |
| Dev scripts, simulator, test commands | `docs/development.md` |
| Finished a roadmap task | tick its checkbox in `docs/roadmap/phase-*.md`, set phase status in `docs/roadmap/README.md` |
| Milestone/phase done | README "Status" line |

Style: short sentences, tables and bullets, no marketing. Describe what *is*, not history; history
belongs in git. Keep each doc under ~250 lines; split by topic if it grows.

## Checks before commit

```bash
cd backend && uv run pytest -q && uv run ruff check . && uv run ruff format --check .
cd frontend && npx tsc -b && npm run lint
```

## Code rules

- Python 3.13, FastAPI, asyncio, SQLModel + Alembic. Schema change ⇒ new Alembic revision in
  `backend/spookhub/migrations/versions/`.
- Board commands go through desired state + `BoardManager` queue. Never call WLED directly from routes.
- The show engine owns mpv during a show. Setup features (test pattern, alignment, audio test) must
  refuse or stop cleanly while a show plays, and restore the idle state after.
- UI never polls: push state over `/ws` via `EventBus.publish`.
- Dashboard, Profiles, Boards must work at 375 px width.
- Comments explain why, not what.
- Commit per finished roadmap task group or phase, conventional commit messages.
