"""One way to start the event loop, for every command-line entry point.

psycopg's async connections cannot run on the ProactorEventLoop that
asyncio uses by default on Windows; they need a selector loop. Linux and
macOS already default to one, so the choice only changes anything there.
"""
from __future__ import annotations

import asyncio
import sys
from collections.abc import Coroutine
from typing import Any


def run(main: Coroutine[Any, Any, Any]) -> Any:
    if sys.platform == "win32":
        return asyncio.run(main, loop_factory=asyncio.SelectorEventLoop)
    return asyncio.run(main)
