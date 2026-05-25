"""Regression tests for the Celery async-DB helper (app/tasks/async_db.py).

Celery tasks run sync; to call async DB code they spin up an event loop.
The bug this guards against: reusing the module-global ``app.database.async_session``
(whose asyncpg connection pool is bound to whatever loop first used it) across a
fresh per-task loop raises ``RuntimeError: got Future attached to a different loop``
/ ``Event loop is closed`` on every task after the worker's first.

``task_event_loop()`` must therefore give each task BOTH a fresh event loop AND a
task-local engine that does not share connections across loops (NullPool).
"""
import asyncio

from sqlalchemy.pool import NullPool

import app.database as appdb
from app.tasks.async_db import task_event_loop


def test_each_invocation_gets_a_fresh_loop_closed_after():
    with task_event_loop() as (loop1, _Session1):
        assert isinstance(loop1, asyncio.AbstractEventLoop)
        assert not loop1.is_closed()
        first = loop1
    assert first.is_closed()  # cleaned up on exit

    with task_event_loop() as (loop2, _Session2):
        assert loop2 is not first  # a brand-new loop, not the reused/closed one
        assert not loop2.is_closed()


def test_session_bind_is_task_local_not_global_engine():
    with task_event_loop() as (_loop, Session):
        bind = Session.kw["bind"]
        assert bind is not None
        # MUST NOT be the module-global engine (reusing it is the bug)
        assert bind is not appdb.engine


def test_engine_uses_nullpool_no_cross_loop_pooling():
    with task_event_loop() as (_loop, Session):
        bind = Session.kw["bind"]
        pool = bind.sync_engine.pool
        assert isinstance(pool, NullPool)
