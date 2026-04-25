"""Tests for DerbyScheduler._spawn_background — fire-and-forget bookkeeping."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest

from config import Settings
from derby.scheduler import DerbyScheduler


class _RecordingLogger:
    """Captures log calls for assertions."""

    def __init__(self) -> None:
        self.errors: list[tuple[str, tuple, dict]] = []

    def error(self, msg, *args, **kwargs) -> None:  # noqa: D401
        self.errors.append((msg, args, kwargs))

    def info(self, *args, **kwargs) -> None:
        pass

    def warning(self, *args, **kwargs) -> None:
        pass

    def debug(self, *args, **kwargs) -> None:
        pass

    def exception(self, *args, **kwargs) -> None:
        pass


class _DummyBot:
    def __init__(self, settings: Settings, logger=None) -> None:
        self.settings = settings
        self.logger = logger or logging.getLogger("test")


def _make_scheduler(tmp_path: Path, logger=None) -> DerbyScheduler:
    bot = _DummyBot(Settings(race_times=["12:00"]), logger=logger)
    return DerbyScheduler(bot, db_path=str(tmp_path / "db.sqlite"))


@pytest.mark.asyncio
async def test_spawn_background_completes_and_unregisters(tmp_path: Path) -> None:
    scheduler = _make_scheduler(tmp_path)

    async def _ok():
        return 42

    task = scheduler._spawn_background(_ok())
    assert task in scheduler._background_tasks
    await task
    # done_callback runs on next loop iteration
    await asyncio.sleep(0)
    assert task not in scheduler._background_tasks


@pytest.mark.asyncio
async def test_spawn_background_logs_uncaught_exception(tmp_path: Path) -> None:
    log = _RecordingLogger()
    scheduler = _make_scheduler(tmp_path, logger=log)

    async def _explode():
        raise RuntimeError("boom")

    task = scheduler._spawn_background(_explode(), name="exploder")
    with pytest.raises(RuntimeError):
        await task
    await asyncio.sleep(0)

    assert task not in scheduler._background_tasks
    assert len(log.errors) == 1
    msg, args, kwargs = log.errors[0]
    assert "Background task" in msg
    assert "exploder" in args
    assert isinstance(kwargs.get("exc_info"), RuntimeError)


@pytest.mark.asyncio
async def test_spawn_background_silent_on_cancel(tmp_path: Path) -> None:
    log = _RecordingLogger()
    scheduler = _make_scheduler(tmp_path, logger=log)

    async def _sleeps_forever():
        await asyncio.sleep(60)

    task = scheduler._spawn_background(_sleeps_forever())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)

    assert task not in scheduler._background_tasks
    assert log.errors == []  # cancellation should not log


@pytest.mark.asyncio
async def test_close_cancels_background_tasks(tmp_path: Path) -> None:
    scheduler = _make_scheduler(tmp_path)

    async def _sleeps_forever():
        await asyncio.sleep(60)

    task = scheduler._spawn_background(_sleeps_forever())
    assert not task.done()

    await scheduler.close()
    # close() schedules cancellation but doesn't await the task; let the
    # event loop drain so the cancellation actually lands.
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()
