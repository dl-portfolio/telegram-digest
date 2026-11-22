from fastapi import APIRouter

from app.api.routes import channels, deduplication, items, login, messages, private, telegram, users, utils
from app.core.config import settings

api_router = APIRouter()
api_router.include_router(login.router)
api_router.include_router(users.router)
api_router.include_router(utils.router)
api_router.include_router(items.router)
api_router.include_router(telegram.router)
api_router.include_router(channels.router)
api_router.include_router(messages.router)
api_router.include_router(deduplication.router)


if settings.ENVIRONMENT == "local":
    api_router.include_router(private.router)
