"""The entry points' event loop: a selector loop on Windows, where psycopg's
async connections cannot use the default proactor loop."""
from __future__ import annotations

import asyncio

from intel import aio


def test_windows_gets_a_selector_loop(monkeypatch):
    seen = {}

    def fake_run(main, **kw):
        seen.update(kw)
        main.close()

    monkeypatch.setattr(aio.sys, "platform", "win32")
    monkeypatch.setattr(aio.asyncio, "run", fake_run)

    async def main():
        return None

    aio.run(main())
    assert seen == {"loop_factory": asyncio.SelectorEventLoop}


def test_it_returns_what_the_coroutine_returns():
    async def main():
        return 42

    assert aio.run(main()) == 42
