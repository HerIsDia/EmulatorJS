#!/usr/bin/env python3
"""Run ra-proxy against a fake retroachievements.org, for end-to-end tests without a real account.

The fake server (ra-proxy/tests/conftest.py) knows one game (id 1) whose achievement 101 triggers on
`0xH0010=1`; --hash maps a ROM hash to that game (see make-test-rom.py).

Usage: fake-ra-proxy.py --hash MD5 [--port 8081] [--data-dir DIR] [--origin http://localhost:8000]
"""

import argparse
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "ra-proxy"
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

import conftest  # noqa: E402
import httpx  # noqa: E402
import uvicorn  # noqa: E402

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hash", required=True)
    parser.add_argument("--port", type=int, default=8081)
    parser.add_argument("--data-dir", default="")
    parser.add_argument("--origin", default="http://localhost:8000")
    args = parser.parse_args()

    conftest.GAME_HASH = args.hash.lower()
    fake = conftest.FakeRetroAchievements()
    settings = Settings(
        data_dir=Path(args.data_dir or tempfile.mkdtemp(prefix="ra-proxy-")),
        ra_username="uploader",
        ra_password="ra-password",
        local_users={"alice": "local-pass"},
        secret_key="e2e",
        upstream_min_interval=0,
        allowed_origins=[args.origin],
    )
    uvicorn.run(create_app(settings, httpx.MockTransport(fake.handler)), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
