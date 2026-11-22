"""ARQ Worker settings and configuration."""

import logging
from typing import Any

import structlog
from arq import cron
from arq.connections import RedisSettings

from app.core.config import settings
from app.worker.tasks import health_check, scrape_channel, scrape_all_channels
from app.worker.deduplication_tasks import generate_embeddings, deduplicate_messages

# Configure structlog for worker
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.dev.set_exc_info,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer() if settings.ENVIRONMENT == "local" else structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=True,
)


def get_redis_settings() -> RedisSettings:
    """Get Redis connection settings from config."""
    return RedisSettings(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
    )


class WorkerSettings:
    """ARQ Worker settings class."""

    # Redis connection
    redis_settings = get_redis_settings()

    # List of task functions the worker can execute
    functions = [
        health_check,
        scrape_channel,
        scrape_all_channels,
        generate_embeddings,
        deduplicate_messages,
    ]

    # Cron jobs (scheduled tasks)
    cron_jobs = [
        # Health check every 5 minutes
        cron(health_check, minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55}),
        # Scrape all channels every hour
        cron(scrape_all_channels, minute=0),
    ]

    # Worker settings
    max_jobs = 10  # Max concurrent jobs
    job_timeout = 600  # 10 minutes timeout for scraping jobs
    keep_result = 3600  # Keep results for 1 hour
    keep_result_forever = False

    # Retry settings
    max_tries = 5  # More retries for rate-limited jobs
    retry_jobs = True

    # Health check timeout (shorter)
    health_check_interval = 300  # 5 minutes

    # Logging
    @staticmethod
    async def on_startup(ctx: dict[str, Any]) -> None:
        """Called when worker starts."""
        log = structlog.get_logger()
        await log.ainfo(
            "Worker started",
            redis_host=settings.REDIS_HOST,
            redis_port=settings.REDIS_PORT,
            environment=settings.ENVIRONMENT,
        )

    @staticmethod
    async def on_shutdown(ctx: dict[str, Any]) -> None:
        """Called when worker shuts down."""
        log = structlog.get_logger()
        await log.ainfo("Worker shutting down")

    @staticmethod
    async def on_job_start(ctx: dict[str, Any]) -> None:
        """Called before each job starts."""
        log = structlog.get_logger()
        job_id = ctx.get("job_id", "unknown")
        await log.adebug("Job starting", job_id=job_id)

    @staticmethod
    async def on_job_end(ctx: dict[str, Any]) -> None:
        """Called after each job ends."""
        log = structlog.get_logger()
        job_id = ctx.get("job_id", "unknown")
        await log.adebug("Job completed", job_id=job_id)
