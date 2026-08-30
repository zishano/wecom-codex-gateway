#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.codex_client import CodexAppServerClient


async def run() -> None:
    binary = shutil.which("codex")
    if not binary:
        raise SystemExit("codex was not found on PATH")
    client = CodexAppServerClient(
        binary=binary,
        cwd=Path("/mnt/e/Project/LMK"),
        model=None,
        sandbox="read-only",
        timeout_seconds=180,
    )
    try:
        await client.start()
        thread_id = await client.start_thread("LMK gateway smoke test")
        result = await client.run_turn(
            thread_id,
            "This is a gateway connectivity test. Do not modify files. Reply exactly: GATEWAY_OK",
        )
        print("thread:", thread_id)
        print("status:", result.status)
        print("reply:", result.text)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(run())
