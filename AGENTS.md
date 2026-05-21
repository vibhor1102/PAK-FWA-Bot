# AGENTS.md
- Keep config in `bot/config.py`.
- Local/testing reads `.env`; Render reads env vars.
- Use `FeatureVisibility` for local/test/prod gates.
- Keep secrets out of logs and settings output.
- Put Postgres access behind `bot/database.py`.
- Add new settings through shared helpers, not ad hoc checks.
- Keep changes small and docs in sync.
