from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List


@dataclass(frozen=True)
class Settings:

    role: str = "leader"
    host: str = "0.0.0.0"
    port: int = 8000
    follower_urls: List[str] = field(default_factory=list)
    write_quorum: int = 1
    min_delay_ms: int = 0
    max_delay_ms: int = 0
    replication_timeout_ms: int = 3000

    @property
    def is_leader(self) -> bool:
        return self.role.lower() == "leader"

    @classmethod
    def from_env(cls) -> "Settings":
        role = os.getenv("ROLE", "leader").strip().lower()
        host = os.getenv("HOST", "0.0.0.0").strip()
        port = int(os.getenv("PORT", "8000"))
        rep_timeout = int(os.getenv("REPLICATION_TIMEOUT_MS", "3000"))
        min_delay = int(os.getenv("MIN_DELAY_MS", "0"))
        max_delay = int(os.getenv("MAX_DELAY_MS", str(min_delay)))

        follower_urls: List[str] = []
        write_quorum = int(os.getenv("WRITE_QUORUM", "1"))

        if role == "leader":
            urls_raw = os.getenv("FOLLOWER_URLS", "")
            follower_urls = [url.strip() for url in urls_raw.split(",") if url.strip()]
            if not follower_urls:
                raise ValueError("Leader role requires at least one follower URL")
            max_quorum = len(follower_urls)
            if write_quorum < 0 or write_quorum > max_quorum:
                raise ValueError(
                    f"WRITE_QUORUM must be between 0 and {max_quorum}, got {write_quorum}"
                )
        else:
            follower_urls = []
            write_quorum = 0

        if min_delay < 0 or max_delay < 0:
            raise ValueError("Delay bounds must be non-negative")
        if min_delay > max_delay:
            raise ValueError("MIN_DELAY_MS cannot exceed MAX_DELAY_MS")

        return cls(
            role=role,
            host=host,
            port=port,
            follower_urls=follower_urls,
            write_quorum=write_quorum,
            min_delay_ms=min_delay,
            max_delay_ms=max_delay,
            replication_timeout_ms=rep_timeout,
        )
