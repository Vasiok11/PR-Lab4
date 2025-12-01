"""Measure write latency under varying quorum requirements and verify replica parity."""
from __future__ import annotations

import asyncio
import json
import os
import random
import subprocess
import time
from pathlib import Path
from typing import List

import httpx
import matplotlib.pyplot as plt
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = PROJECT_ROOT / "docker-compose.yml"
ARTIFACT_DIR = PROJECT_ROOT / "artifacts"
ARTIFACT_DIR.mkdir(exist_ok=True)

LEADER_URL = "http://localhost:9000"
FOLLOWER_PORTS = [9001, 9002, 9003, 9004, 9005]
TOTAL_WRITES = 100
CONCURRENCY = 10
KEYS = [f"perf-key-{i}" for i in range(10)]


def run_compose(args: List[str], env: dict | None = None) -> None:
    subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), *args],
        cwd=PROJECT_ROOT,
        check=True,
        env=env,
    )


def wait_for(url: str, retries: int = 60, delay: float = 1.0) -> None:
    for _ in range(retries):
        try:
            response = httpx.get(f"{url}/health", timeout=2.0)
            if response.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(delay)
    raise RuntimeError(f"Service at {url} failed to become healthy")


def reset_cluster() -> None:
    httpx.post(f"{LEADER_URL}/reset", timeout=5)
    for port in FOLLOWER_PORTS:
        httpx.post(f"http://localhost:{port}/reset", timeout=5)


async def issue_write(client: httpx.AsyncClient, key: str, value: str) -> float:
    start = time.perf_counter()
    response = await client.put(f"{LEADER_URL}/kv/{key}", json={"value": value})
    response.raise_for_status()
    return (time.perf_counter() - start) * 1000


async def perform_writes() -> List[float]:
    latencies: List[float] = []
    sem = asyncio.Semaphore(CONCURRENCY)

    async def worker(key: str, value: str) -> None:
        async with sem:
            latency = await issue_write(client, key, value)
            latencies.append(latency)

    async with httpx.AsyncClient(timeout=10.0) as client:
        tasks = []
        for _ in range(TOTAL_WRITES):
            key = random.choice(KEYS)
            value = f"v-{random.randint(0, 1_000_000)}-{time.time_ns()}"
            tasks.append(asyncio.create_task(worker(key, value)))
        await asyncio.gather(*tasks)
    return latencies


def fetch_store(url: str) -> dict:
    response = httpx.get(f"{url}/kv", timeout=5)
    response.raise_for_status()
    return response.json()


def ensure_parity(max_attempts: int = 10, delay: float = 0.5) -> None:
    """Poll until all followers match the leader or raise after timeout."""

    for attempt in range(1, max_attempts + 1):
        leader_data = fetch_store(LEADER_URL)
        mismatches = []
        for port in FOLLOWER_PORTS:
            follower = fetch_store(f"http://localhost:{port}")
            if follower != leader_data:
                mismatches.append(port)
        if not mismatches:
            print("[parity] Followers match leader state")
            return
        if attempt < max_attempts:
            time.sleep(delay)
    raise AssertionError(f"Followers {mismatches} diverged from leader after waiting")


def main() -> None:
    results = []
    for quorum in range(1, len(FOLLOWER_PORTS) + 1):
        env = os.environ.copy()
        env["WRITE_QUORUM"] = str(quorum)
        run_compose(["down", "-v"], env=env)
        run_compose(["up", "-d", "--build"], env=env)
        wait_for(LEADER_URL)
        for port in FOLLOWER_PORTS:
            wait_for(f"http://localhost:{port}")
        reset_cluster()
        print(f"Running workload with quorum={quorum}")
        latencies = asyncio.run(perform_writes())
        avg_latency = sum(latencies) / len(latencies)
        ensure_parity()
        result_entry = {
            "quorum": quorum,
            "writes": len(latencies),
            "avg_latency_ms": avg_latency,
            "min_latency_ms": min(latencies),
            "max_latency_ms": max(latencies),
            "parity_ok": True,
        }
        results.append(result_entry)

    df = pd.DataFrame(results)
    plt.figure(figsize=(8, 4))
    plt.plot(df["quorum"], df["avg_latency_ms"], marker="o")
    plt.title("Write Quorum vs Average Latency")
    plt.xlabel("Write Quorum")
    plt.ylabel("Average Latency (ms)")
    plt.grid(True, linestyle="--", alpha=0.4)
    plot_path = ARTIFACT_DIR / "quorum_latency.png"
    plt.savefig(plot_path, bbox_inches="tight")
    summary = {
        "total_writes_per_run": TOTAL_WRITES,
        "concurrency": CONCURRENCY,
        "key_count": len(KEYS),
        "results": results,
    }
    summary_path = ARTIFACT_DIR / "perf_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2))
    print(f"Saved latency plot to {plot_path}")
    print(f"Saved JSON summary to {summary_path}")
    run_compose(["down", "-v"])


if __name__ == "__main__":
    main()
