from fastapi import FastAPI

from app.ai.routes import router as ai_settings_router
from app.core.config import settings
from app.core.logging import configure_logging
from app.files.routes import router as files_router
from app.laws.routes import router as laws_router
from app.projects.routes import router as projects_router
from app.reviews.routes import router as reviews_router
from app.rules.routes import router as rules_router
from app.telegram.webhook import router as telegram_router
from app.users.routes import router as users_router


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title=settings.app_name)

    @app.get("/healthz", tags=["system"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(telegram_router)
    app.include_router(ai_settings_router)
    app.include_router(users_router)
    app.include_router(projects_router)
    app.include_router(files_router)
    app.include_router(laws_router)
    app.include_router(rules_router)
    app.include_router(reviews_router)
    return app


app = create_app()
