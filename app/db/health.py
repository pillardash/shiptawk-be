from sqlalchemy import text

from app.db.session import get_sessionmaker


def check_database_ready() -> None:
    with get_sessionmaker()() as session:
        session.execute(text("SELECT 1"))
