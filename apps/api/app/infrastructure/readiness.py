from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError


def check_readiness(engine: Engine) -> tuple[bool, dict]:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return False, {"status": "not_ready", "checks": {"database": {"ready": False}}}
    return True, {"status": "ready", "checks": {"database": {"ready": True}}}
