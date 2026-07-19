from sqlalchemy import text

from app.db.session import get_sessionmaker


async def check_database_ready() -> None:
    async with get_sessionmaker()() as session:
        await session.execute(text("SELECT 1"))
