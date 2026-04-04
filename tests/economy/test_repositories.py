import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from db_base import Base
from economy import repositories as wallet_repo
import economy.models  # noqa: F401

GUILD_ID = 1


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
    wallet = await wallet_repo.create_wallet(
        session, user_id=1, guild_id=GUILD_ID, balance=100
    )
    assert wallet.balance == 100

    fetched = await wallet_repo.get_wallet(session, wallet.user_id, GUILD_ID)
    assert fetched.user_id == 1

    updated = await wallet_repo.update_wallet(
        session, wallet.user_id, GUILD_ID, balance=150
    )
    assert updated.balance == 150

    await wallet_repo.delete_wallet(session, wallet.user_id, GUILD_ID)
    assert await wallet_repo.get_wallet(session, wallet.user_id, GUILD_ID) is None


@pytest.mark.asyncio
async def test_wallet_guild_isolation(session: AsyncSession):
    """Wallets in different guilds are independent."""
    w1 = await wallet_repo.create_wallet(
        session, user_id=1, guild_id=100, balance=50
    )
    w2 = await wallet_repo.create_wallet(
        session, user_id=1, guild_id=200, balance=75
    )

    assert (await wallet_repo.get_wallet(session, 1, 100)).balance == 50
    assert (await wallet_repo.get_wallet(session, 1, 200)).balance == 75

    await wallet_repo.update_wallet(session, 1, 100, balance=999)
    assert (await wallet_repo.get_wallet(session, 1, 200)).balance == 75
