from __future__ import annotations

import os
from pathlib import Path

import uvicorn

from .api import create_app

DEFAULT_DB = Path("~/.hmn/control-plane.db").expanduser()
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def main() -> None:
    db = Path(os.environ.get("HMN_DB", DEFAULT_DB)).expanduser()
    host = os.environ.get("HMN_HOST", DEFAULT_HOST)
    port = int(os.environ.get("HMN_PORT", str(DEFAULT_PORT)))
    uvicorn.run(create_app(db), host=host, port=port)


if __name__ == "__main__":
    main()
