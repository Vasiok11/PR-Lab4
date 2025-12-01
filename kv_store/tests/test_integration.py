import os
import subprocess
import time
from pathlib import Path
from typing import Generator

import pytest
import requests

PROJECT_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = PROJECT_ROOT / "docker-compose.yml"
LEADER_URL = "http://localhost:9000"
FOLLOWER_PORTS = [9001, 9002, 9003, 9004, 9005]


def _run_compose(*args: str) -> None:
    cmd = ["docker", "compose", "-f", str(COMPOSE_FILE), *args]
    print(f"[compose] {' '.join(cmd[2:])}")
    subprocess.run(cmd, cwd=PROJECT_ROOT, check=True)


def _wait_for_health(url: str, retries: int = 60, delay: float = 1.0) -> None:
    for _ in range(retries):
        try:
            response = requests.get(f"{url}/health", timeout=2)
            if response.status_code == 200:
                return
        except requests.RequestException:
            pass
        time.sleep(delay)
    raise RuntimeError(f"Service at {url} failed to become healthy")


def _reset_cluster() -> None:
    requests.post(f"{LEADER_URL}/reset", timeout=5)
    for port in FOLLOWER_PORTS:
        requests.post(f"http://localhost:{port}/reset", timeout=5)


@pytest.fixture(scope="session", autouse=True)
def stack() -> Generator[None, None, None]:
    env = os.environ.copy()
    env.setdefault("WRITE_QUORUM", "3")
    print("[setup] Shutting down any previous stack...")
    _run_compose("down", "-v")
    print("[setup] Starting leader + followers (WRITE_QUORUM=", env["WRITE_QUORUM"], ")")
    subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d", "--build"],
        cwd=PROJECT_ROOT,
        check=True,
        env=env,
    )
    print("[setup] Waiting for leader to become healthy")
    _wait_for_health(LEADER_URL)
    for port in FOLLOWER_PORTS:
        print(f"[setup] Waiting for follower on port {port}")
        _wait_for_health(f"http://localhost:{port}")
    print("[setup] Resetting all replicas")
    _reset_cluster()
    yield
    print("[teardown] Bringing stack down")
    _run_compose("down", "-v")


@pytest.fixture(autouse=True)
def reset_before_test() -> None:
    """Reset cluster state before each test."""
    _reset_cluster()


def test_leader_accepts_writes() -> None:
    """Test that the leader accepts write requests and returns correct response."""
    print("[test] Writing to leader")
    response = requests.put(f"{LEADER_URL}/kv/test-key", json={"value": "test-value"}, timeout=10)
    
    assert response.status_code == 200
    data = response.json()
    assert data["key"] == "test-key"
    assert data["value"] == "test-value"
    assert data["version"] == 1
    assert data["acks"] >= 3  # WRITE_QUORUM is 3
    print(f"[test] Write successful: version={data['version']}, acks={data['acks']}")


def test_follower_rejects_writes() -> None:
    """Test that followers reject direct write requests (leader-only writes)."""
    print("[test] Attempting write to follower (should fail)")
    response = requests.put(
        f"http://localhost:{FOLLOWER_PORTS[0]}/kv/test-key",
        json={"value": "test-value"},
        timeout=10,
    )
    
    assert response.status_code == 405  # Method Not Allowed
    print("[test] Follower correctly rejected write request")


def test_replication_to_all_followers() -> None:
    """Test that writes are replicated to all 5 followers."""
    key = "replication-key"
    value = "replicated-value"

    print("[test] Writing value via leader")
    response = requests.put(f"{LEADER_URL}/kv/{key}", json={"value": value}, timeout=10)
    assert response.status_code == 200

    # Wait for background replication to complete
    time.sleep(2)

    print("[test] Verifying leader state")
    leader_data = requests.get(f"{LEADER_URL}/kv/{key}", timeout=5).json()
    assert leader_data["value"] == value

    print("[test] Checking all 5 followers have the data")
    for port in FOLLOWER_PORTS:
        follower_data = requests.get(f"http://localhost:{port}/kv/{key}", timeout=5).json()
        assert follower_data == leader_data, f"Follower on port {port} has different data"
        print(f"[test] Follower {port}: OK")


def test_version_increments() -> None:
    """Test that each write increments the version number."""
    print("[test] Writing multiple values to same key")
    
    for i in range(1, 4):
        response = requests.put(
            f"{LEADER_URL}/kv/version-key",
            json={"value": f"value-{i}"},
            timeout=10,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["version"] == i, f"Expected version {i}, got {data['version']}"
        print(f"[test] Write {i}: version={data['version']}")


def test_read_from_any_node() -> None:
    """Test that reads can be performed from both leader and followers."""
    key = "read-test-key"
    value = "read-test-value"

    print("[test] Writing value via leader")
    requests.put(f"{LEADER_URL}/kv/{key}", json={"value": value}, timeout=10)
    time.sleep(2)

    print("[test] Reading from leader")
    leader_data = requests.get(f"{LEADER_URL}/kv/{key}", timeout=5).json()
    assert leader_data["value"] == value

    print("[test] Reading from each follower")
    for port in FOLLOWER_PORTS:
        follower_data = requests.get(f"http://localhost:{port}/kv/{key}", timeout=5).json()
        assert follower_data["value"] == value
        print(f"[test] Follower {port}: read successful")


def test_multiple_keys() -> None:
    """Test that multiple different keys can be stored and retrieved."""
    keys_values = {
        "key-1": "value-1",
        "key-2": "value-2",
        "key-3": "value-3",
    }

    print("[test] Writing multiple keys")
    for key, value in keys_values.items():
        response = requests.put(f"{LEADER_URL}/kv/{key}", json={"value": value}, timeout=10)
        assert response.status_code == 200

    time.sleep(2)

    print("[test] Verifying all keys on leader")
    for key, expected_value in keys_values.items():
        data = requests.get(f"{LEADER_URL}/kv/{key}", timeout=5).json()
        assert data["value"] == expected_value

    print("[test] Verifying all keys on a follower")
    for key, expected_value in keys_values.items():
        data = requests.get(f"http://localhost:{FOLLOWER_PORTS[0]}/kv/{key}", timeout=5).json()
        assert data["value"] == expected_value


def test_concurrent_writes() -> None:
    """Test that concurrent writes are handled correctly."""
    import concurrent.futures

    print("[test] Issuing 10 concurrent writes")
    
    def write_key(i: int) -> requests.Response:
        return requests.put(
            f"{LEADER_URL}/kv/concurrent-key-{i}",
            json={"value": f"concurrent-value-{i}"},
            timeout=10,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(write_key, i) for i in range(10)]
        responses = [f.result() for f in concurrent.futures.as_completed(futures)]

    # All writes should succeed
    success_count = sum(1 for r in responses if r.status_code == 200)
    print(f"[test] {success_count}/10 concurrent writes succeeded")
    assert success_count == 10

    time.sleep(2)

    # Verify all keys exist on leader and followers
    print("[test] Verifying concurrent writes replicated")
    leader_dump = requests.get(f"{LEADER_URL}/kv", timeout=5).json()
    follower_dump = requests.get(f"http://localhost:{FOLLOWER_PORTS[0]}/kv", timeout=5).json()
    
    for i in range(10):
        key = f"concurrent-key-{i}"
        assert key in leader_dump, f"Key {key} missing from leader"
        assert key in follower_dump, f"Key {key} missing from follower"


def test_health_endpoints() -> None:
    """Test that health endpoints return correct role information."""
    print("[test] Checking leader health")
    leader_health = requests.get(f"{LEADER_URL}/health", timeout=5).json()
    assert leader_health["status"] == "ok"
    assert leader_health["role"] == "leader"

    print("[test] Checking follower health endpoints")
    for port in FOLLOWER_PORTS:
        follower_health = requests.get(f"http://localhost:{port}/health", timeout=5).json()
        assert follower_health["status"] == "ok"
        assert follower_health["role"] == "follower"
        print(f"[test] Follower {port}: role=follower, status=ok")
