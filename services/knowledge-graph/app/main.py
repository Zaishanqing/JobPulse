from app.api.router import router
from app.bootstrap.application import create_app as _create_app
from app.config import Settings


def create_app(settings: Settings | None = None):
    application = _create_app(settings)
    application.include_router(router)
    return application


app = create_app()
