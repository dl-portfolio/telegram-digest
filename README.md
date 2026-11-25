# Telegram Digest

**Semantic deduplication for Telegram channels**

Telegram Digest collects messages from a small set of Telegram channels, embeds them with OpenAI and groups messages that say the same thing, so a story posted several times (or reworded across channels) is read once.

It was planned as a single application covering collection, deduplication, analysis and reporting. Collection and semantic deduplication were built and validated; LLM analysis and a reporting / content-planning interface stayed in the backlog.

---

## Features

### Channel management and collection
- Manage up to 3 Telegram channels per user via the API (create, list, update, delete; duplicates rejected)
- Log in to Telegram once through the API; the Telethon session is stored in `telegram_sessions/` (git-ignored)
- Scrape messages with metadata (views, forwards, author, timestamps), on demand or hourly via an ARQ cron job
- Incremental scraping (only messages newer than the last one stored) and flood-wait handling

### Semantic deduplication
- Embeddings with OpenAI `text-embedding-3-small` at 768 dimensions, stored in a pgvector column with an HNSW (cosine) index
- A deduplication job groups messages whose cosine similarity reaches a configurable threshold (default 0.85), with a small boost for messages posted within 6 hours of each other
- Each group records a primary message (highest view count), per-message similarity scores and a group confidence score
- Validated on real data: 195 messages from 2 public channels, 167 embeddings, 5 duplicate groups at a 0.70 threshold (see [docs/VALIDATION-SUMMARY.md](docs/VALIDATION-SUMMARY.md))

### Not built (backlog)
- LLM insight generation ("What happened" / "Why it matters")
- Content-planning interface

---

## Quick start

### Prerequisites

- **Docker** with Docker Compose
- **Telegram API credentials** from [my.telegram.org/apps](https://my.telegram.org/apps)
- **OpenAI API key** from [platform.openai.com/api-keys](https://platform.openai.com/api-keys) (the backend will not start without one)

### Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/dl-portfolio/telegram-digest.git
   cd telegram-digest
   ```

2. **Create your environment file:**
   ```bash
   cp .env.example .env
   ```

3. **Fill in `.env`:**
   ```bash
   # Required: Telegram API credentials
   TELEGRAM_API_ID=your_api_id_here
   TELEGRAM_API_HASH=your_api_hash_here

   # Required: OpenAI API key
   OPENAI_API_KEY=sk-proj-your_key_here

   # Change these defaults
   SECRET_KEY=generate_secure_random_key
   POSTGRES_PASSWORD=choose_secure_password
   FIRST_SUPERUSER_PASSWORD=choose_secure_password
   ```

   Generate random values with:
   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

4. **Start the stack** (Postgres + pgvector, Redis, backend, ARQ worker, plus Adminer, Mailcatcher and a local Traefik proxy from `docker-compose.override.yml`):
   ```bash
   docker compose up -d
   ```

5. **Check it is running:**
   ```bash
   curl http://localhost:8000/api/v1/utils/health-check/
   ```
   Interactive API docs: http://localhost:8000/docs

6. **Get an API token.** Log in as the superuser (`FIRST_SUPERUSER` and its password from `.env`):
   ```bash
   curl -X POST http://localhost:8000/api/v1/login/access-token \
     -d 'username=<FIRST_SUPERUSER>' -d 'password=<FIRST_SUPERUSER_PASSWORD>'
   TOKEN=<access_token from the response>
   ```
   Every endpoint below except the health check needs `Authorization: Bearer $TOKEN`.

7. **Log in to Telegram** (once; the session is saved to `telegram_sessions/`). These endpoints control the server's single shared Telegram session, so they need a **superuser** token:
   ```bash
   curl -X POST http://localhost:8000/api/v1/telegram/start  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"phone": "+447700900123"}'
   curl -X POST http://localhost:8000/api/v1/telegram/verify -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"code": "12345"}'
   # With two-step verification: POST /api/v1/telegram/verify-2fa with {"password": "..."}
   ```

8. **Add channels, scrape, embed, deduplicate** with the endpoints below.

---

## API overview

All paths are under `/api/v1`.

| Area | Endpoints |
|---|---|
| Health | `GET /utils/health-check/` |
| Telegram login (superuser only) | `GET /telegram/status`, `POST /telegram/start`, `POST /telegram/verify`, `POST /telegram/verify-2fa`, `POST /telegram/logout` |
| Channels | `GET /channels/`, `POST /channels/`, `GET/PATCH/DELETE /channels/{id}`, `POST /channels/{id}/scrape`, `POST /channels/scrape-all` |
| Messages | `GET /messages/`, `GET /messages/{id}`, `GET /messages/channel/{channel_id}`, `GET /messages/stats/summary` |
| Deduplication | Superuser only (they act on every user's messages): `POST /deduplication/embeddings/generate`, `POST /deduplication/jobs` (e.g. `{"similarity_threshold": 0.7}`), `GET /deduplication/stats`. Any user (scoped to their channels): `GET /deduplication/messages`, `GET /deduplication/messages/{id}/duplicates` |

The user, login and item endpoints come from the template the project started from (see Credits).

---

## Architecture

```
                 ┌──────────────┐        ┌────────────┐
  HTTP client ──>│   Backend    │───────>│ PostgreSQL │
                 │  (FastAPI)   │        │ + pgvector │
                 └──────────────┘        └────────────┘
                        │ enqueue               ^
                        v                       │
                 ┌──────────────┐        ┌──────────────┐
                 │    Redis     │<──────>│ Worker (ARQ) │──> Telegram API (Telethon)
                 └──────────────┘        │ scrape/embed │──> OpenAI embeddings API
                                         │ deduplicate  │
                                         └──────────────┘
```

### Technology stack
- **Backend:** Python 3.12, FastAPI, SQLModel, Alembic
- **Database:** PostgreSQL 17 with pgvector
- **Task queue:** ARQ on Redis
- **Telegram:** Telethon
- **Embeddings:** OpenAI `text-embedding-3-small`
- **Deployment:** Docker Compose
- **Testing:** pytest

---

## Testing

```bash
# Unit and API tests (use the stack's database)
docker compose exec backend pytest tests/ --ignore=tests/integration

# Integration tests against the running stack
docker compose exec backend pytest tests/integration/ -v

# Full pipeline check (needs channels already scraped, embedded and deduplicated)
docker compose exec backend pytest tests/integration/test_epic2_full_pipeline.py -v -s
```

`tests/services/test_embeddings.py` calls the live OpenAI API, so it needs a real key. CI runs the unit and API tests without it. See [docs/TESTING-STRATEGY.md](docs/TESTING-STRATEGY.md) for the approach and the lesson behind it (112 unit tests passing while the Docker app was broken).

---

## Security

- Never commit `.env` or `*.session` files (both are git-ignored); `.env.example` contains placeholders only.
- Change the default superuser (`admin@example.com`) and every password in `.env.example`.
- The Telegram login endpoints are not behind JWT auth, so run the stack on a trusted machine only.

See [SECURITY.md](SECURITY.md).

---

## Troubleshooting

```bash
# "Incorrect email or password": re-run migrations and initial data
docker compose up -d prestart

# Logs
docker compose logs -f backend
docker compose logs -f worker

# Full reset (deletes the database volume)
docker compose down -v && docker compose up -d
```

---

## Credits

The backend scaffold (FastAPI app layout, users and authentication, SQLModel/Alembic setup, email templates, Docker Compose files with Traefik labels, CI test workflows) comes from the **[Full Stack FastAPI Template](https://github.com/fastapi/full-stack-fastapi-template)** by Sebastián Ramírez and contributors, used under the MIT licence. The template's React frontend, deployment scripts and project-management workflows were not used and have been removed.

Also built with [pgvector](https://github.com/pgvector/pgvector), [Telethon](https://github.com/LonamiWebs/Telethon), [ARQ](https://github.com/python-arq/arq) and the [OpenAI embeddings API](https://platform.openai.com/docs/guides/embeddings).

## Licence

MIT - see [LICENSE](LICENSE). It keeps the original template's copyright notice alongside this project's.
