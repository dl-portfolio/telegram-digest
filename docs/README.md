# Telegram Digest - Documentation

Setup, architecture and API overview are in the [main README](../README.md).

## Documents

- **[TESTING-STRATEGY.md](./TESTING-STRATEGY.md)** - testing approach (unit, integration, real-data checks) and the "112 passing tests, broken app" lesson
- **[VERIFICATION-PIPELINE.md](./VERIFICATION-PIPELINE.md)** - checklist used before committing changes
- **[VALIDATION-SUMMARY.md](./VALIDATION-SUMMARY.md)** - Epic 1 and 2 validation log (2026-01-08), including the real-data deduplication run

These are development notes written while the project was active. Some of the manual shell scripts they mention were kept locally and are not in this repository.

## Test commands

```bash
# Integration tests (need the stack running: docker compose up -d)
docker compose exec backend pytest tests/integration/ -v

# Unit and API tests against the stack's database
docker compose exec backend pytest tests/ --ignore=tests/integration

# Live OpenAI embedding tests (need a real OPENAI_API_KEY in .env)
docker compose exec backend pytest tests/services/test_embeddings.py -v
```
