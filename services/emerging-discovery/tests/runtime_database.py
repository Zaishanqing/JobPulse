from app.infrastructure.database import Base
from app.main import app

__all__ = ["Base", "SessionLocal", "engine"]


engine = app.state.database.engine
SessionLocal = app.state.database.session_factory
