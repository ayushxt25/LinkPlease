import logging

from fastapi import FastAPI

from app.api.routes import router
from app.core.config import settings

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    logger.warning(
        "webhook_signature_verification_config enabled=%s",
        settings.verify_webhook_signatures,
    )
    app = FastAPI(title=settings.app_name)
    app.include_router(router)
    return app


app = create_app()
