"""Run async DB work inside a (sync) Celery task without the cross-loop bug.

asyncpg connections are bound to the event loop that created them. The
module-global ``app.database.async_session`` pools its connections; a Celery
task that spins up a fresh event loop per invocation and reuses that global
pool hits ``RuntimeError: got Future attached to a different loop`` /
``Event loop is closed`` on every task after the worker's first (the pooled
connection belongs to a previous, now-closed loop).

``task_event_loop()`` gives each task its OWN fresh event loop AND its OWN
async engine with ``NullPool`` (no connection pooling, so nothing is ever
reused across loops). The engine is disposed and the loop closed on exit.
"""
import asyncio
from contextlib import contextmanager

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings


def new_task_loop_session():
    """Create a fresh event loop + task-local NullPool async sessionmaker.

    Returns ``(loop, Session, engine)``. The caller MUST clean up in a finally::

        loop.run_until_complete(engine.dispose())
        loop.close()

    Prefer the ``task_event_loop()`` context manager for new code; this lower-level
    form exists for tasks whose body is awkward to nest under a ``with``.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    return loop, Session, engine


@contextmanager
def task_event_loop():
    """Yield ``(loop, Session)`` for a Celery task.

    ``loop`` is a fresh event loop (use it for ``loop.run_until_complete(...)``).
    ``Session`` is an ``async_sessionmaker`` bound to a task-local NullPool
    async engine. Both are torn down when the ``with`` block exits.
    """
    loop, Session, engine = new_task_loop_session()
    try:
        yield loop, Session
    finally:
        try:
            loop.run_until_complete(engine.dispose())
        finally:
            loop.close()
            asyncio.set_event_loop(None)
