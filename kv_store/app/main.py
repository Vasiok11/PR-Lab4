from __future__ import annotations

import uvicorn

from .config import Settings
from .server import app


if __name__ == "__main__":
    settings = Settings.from_env()
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")
