import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from db_base import Base
from economy import repositories as wallet_repo
import economy.models  # noqa: F401


@pytest_asyncio.fixture()
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    async with async_session() as sess:
        yield sess
    await engine.dispose()


@pytest.mark.asyncio
async def test_wallet_crud(session: AsyncSession):
    wallet = await wallet_repo.create_wallet(session, user_id=1, balance=100)
    assert wallet.balance == 100

    fetched = await wallet_repo.get_wallet(session, wallet.user_id)
    assert fetched.user_id == 1

    updated = await wallet_repo.update_wallet(session, wallet.user_id, balance=150)
    assert updated.balance == 150

    await wallet_repo.delete_wallet(session, wallet.user_id)
    assert await wallet_repo.get_wallet(session, wallet.user_id) is None
