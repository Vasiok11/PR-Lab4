from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from typing import Iterable, List

import httpx

logger = logging.getLogger(__name__)


@dataclass
class ReplicationResult:
    quorum_met: bool
    acknowledgements: int
    follower_count: int


class Replicator:
    """Dispatches replication requests concurrently and enforces the write quorum."""

    def __init__(
        self,
        follower_urls: Iterable[str],
        write_quorum: int,
        min_delay_ms: int,
        max_delay_ms: int,
        timeout_ms: int,
    ) -> None:
        self._follower_urls: List[str] = list(follower_urls)
        self._write_quorum = write_quorum
        self._min_delay = min_delay_ms / 1000
        self._max_delay = max_delay_ms / 1000
        self._timeout = timeout_ms / 1000
        self._client = httpx.AsyncClient()

    async def close(self) -> None:
        await self._client.aclose()

    async def replicate(self, key: str, value: str, version: int) -> ReplicationResult:
        payload = {"key": key, "value": value, "version": version}
        tasks = [
            asyncio.create_task(self._replicate_to(url, payload))
            for url in self._follower_urls
        ]
        if not tasks or self._write_quorum == 0:
            return ReplicationResult(True, 0, len(self._follower_urls))

        acknowledgements = 0
        quorum_met = False
        pending = set(tasks)
        errors = []

        async def _drain(pending_tasks: set[asyncio.Task[bool]]) -> None:
            results = await asyncio.gather(*pending_tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    logger.warning("Replication background error: %s", result)

        try:
            while pending:
                done, pending = await asyncio.wait(
                    pending,
                    timeout=self._timeout,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not done:
                    logger.warning(
                        "Replication timed out after %.2fs while waiting for quorum",
                        self._timeout,
                    )
                    break
                for task in done:
                    try:
                        success = task.result()
                    except Exception as exc:  # noqa: PERF203
                        errors.append(exc)
                        logger.warning("Replication request failed: %s", exc)
                        success = False
                    if success:
                        acknowledgements += 1
                        if acknowledgements >= self._write_quorum:
                            quorum_met = True
                            break
                if quorum_met:
                    break
        finally:
            if pending:
                asyncio.create_task(_drain(pending))

        if not quorum_met:
            logger.error(
                "Failed to reach quorum %s (acks=%s, errors=%s)",
                self._write_quorum,
                acknowledgements,
                len(errors),
            )
        return ReplicationResult(
            quorum_met=quorum_met,
            acknowledgements=acknowledgements,
            follower_count=len(self._follower_urls),
        )

    async def _replicate_to(self, follower_url: str, payload: dict) -> bool:
        await asyncio.sleep(random.uniform(self._min_delay, self._max_delay))
        try:
            response = await self._client.post(
                f"{follower_url}/replicate",
                json=payload,
                timeout=self._timeout,
            )
            response.raise_for_status()
            return True
        except httpx.HTTPError as exc:
            logger.warning("Replication to %s failed: %s", follower_url, exc)
            return False
