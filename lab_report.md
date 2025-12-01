# Key-Value Store with Single-Leader Replication — Lab Report

## Summary

This report documents the implementation of a **distributed key-value store with single-leader replication**, developed as part of PR Lab 4. The project demonstrates advanced distributed systems principles including leader-follower replication, semi-synchronous writes, configurable write quorums, and simulated network latency.

The implementation is built using **Python** with **FastAPI**, featuring a leader node that accepts writes and replicates them to multiple follower nodes. The system runs as Docker containers orchestrated via Docker Compose, with 1 leader and 5 followers communicating over a shared network using JSON-based HTTP APIs.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [System Architecture](#2-system-architecture)
3. [Technical Implementation](#3-technical-implementation)
4. [Replication and Consistency](#4-replication-and-consistency)
5. [Concurrency Model](#5-concurrency-model)
6. [Testing and Verification](#6-testing-and-verification)
7. [Performance Analysis](#7-performance-analysis)
8. [Deployment and Containerization](#8-deployment-and-containerization)
9. [Usage and Commands](#9-usage-and-commands)

---

## 1. Project Overview

### 1.1 Project Purpose

This project implements a distributed key-value store following the **single-leader replication** pattern described in *Designing Data-Intensive Applications* by Martin Kleppmann. The system serves as an educational exercise in:

- **Distributed systems design** with leader-follower topology
- **Semi-synchronous replication** with configurable write quorums
- **Concurrent request handling** using async/await patterns
- **Network lag simulation** for realistic distributed behavior
- **Containerized deployment** with Docker Compose

### 1.2 Key Features

- **Single-Leader Architecture**: Only the leader accepts writes; followers receive updates via replication
- **Semi-Synchronous Replication**: Leader waits for a configurable quorum of acknowledgements before confirming writes
- **Concurrent Execution**: Both leader and followers handle requests concurrently using asyncio
- **Simulated Network Latency**: Configurable random delays (0-1000ms) before replication requests
- **RESTful JSON API**: Clean HTTP endpoints for all operations
- **Monotonic Versioning**: Each write receives a globally increasing version number
- **Eventual Consistency**: All followers eventually converge to the leader's state

### 1.3 Requirements Fulfilled

| Requirement | Status |
|-------------|--------|
| Single-leader replication (leader accepts writes) | ✅ Implemented |
| Replication to all followers | ✅ Implemented |
| Concurrent request execution (leader & followers) | ✅ Implemented |
| 1 leader + 5 followers in Docker containers | ✅ Implemented |
| Configuration via environment variables | ✅ Implemented |
| Web API with JSON communication | ✅ Implemented |
| Semi-synchronous replication | ✅ Implemented |
| Configurable write quorum | ✅ Implemented |
| Simulated network delay [MIN_DELAY, MAX_DELAY] | ✅ Implemented |
| Concurrent replication with differing delays | ✅ Implemented |
| Integration tests | ✅ Implemented |
| Performance analysis (~100 writes, 10 concurrent, 10 keys) | ✅ Implemented |
| Quorum vs. latency plot | ✅ Implemented |
| Replica parity verification | ✅ Implemented |

---

## 2. System Architecture

### 2.1 High-Level Architecture

The system follows a **single-leader replication** topology where the client sends write requests to the leader (port 9000), which then replicates the data to all 5 followers (ports 9001-9005) concurrently using POST `/replicate` requests.

### 2.2 Module Structure

The project is organized into the following key modules:

#### **Core Modules**

| Module | File | Responsibility |
|--------|------|----------------|
| Server | `kv_store/app/server.py` | FastAPI HTTP endpoints for leader and follower modes |
| Store | `kv_store/app/store.py` | Thread-safe in-memory key-value storage with versioning |
| Replicator | `kv_store/app/replication.py` | Concurrent replication with delays and quorum enforcement |
| Config | `kv_store/app/config.py` | Environment variable parsing and validation |
| Main | `kv_store/app/main.py` | Application entry point |

#### **Supporting Files**

| File | Purpose |
|------|---------|
| `docker-compose.yml` | Container orchestration for leader + 5 followers |
| `Dockerfile` | Container image definition |
| `requirements.txt` | Python dependencies |
| `kv_store/tests/test_integration.py` | End-to-end integration tests |
| `scripts/perf_analyzer.py` | Performance analysis and plotting |
| `artifacts/perf_summary.json` | Performance test results |
| `artifacts/quorum_latency.png` | Latency vs. quorum plot |

### 2.3 Technology Stack

| Component | Technology | Version |
|-----------|------------|---------|
| Language | Python | 3.11 |
| Web Framework | FastAPI | 0.115.2 |
| ASGI Server | Uvicorn | 0.30.6 |
| HTTP Client | httpx | 0.27.2 |
| Validation | Pydantic | 2.9.2 |
| Testing | pytest | 8.3.3 |
| Plotting | matplotlib | 3.9.2 |
| Data Analysis | pandas | 2.2.3 |
| Containerization | Docker + Compose | Latest |

---

## 3. Technical Implementation

### 3.1 The KeyValueStore Class

The `KeyValueStore` class (`kv_store/app/store.py`) is the core data structure, providing a thread-safe in-memory store with monotonically increasing versions:

```python
class KeyValueStore:
    """Coroutine-safe in-memory key-value store with monotonically increasing versions."""

    def __init__(self) -> None:
        self._data: Dict[str, Dict[str, int | str]] = {}
        self._version = 0
        self._lock = asyncio.Lock()
```

#### **Key Methods**

| Method | Description |
|--------|-------------|
| `write(key, value)` | Leader-only write that assigns the next global version |
| `apply_replica(key, value, version)` | Applies a replicated write, preserving version ordering |
| `read(key)` | Returns the current value and version for a key |
| `dump()` | Returns the entire store contents (for verification) |
| `reset()` | Clears all data (for testing) |

#### **Version Handling**

The store maintains a global version counter. Each write increments this counter, ensuring:
- Total ordering of all writes
- Conflict resolution (higher version wins)
- Detection of stale replicas

```python
async def apply_replica(self, key: str, value: str, version: int) -> int:
    """Apply a replicated write, preserving monotonically increasing versions."""
    async with self._lock:
        current = self._data.get(key)
        if current and current["version"] >= version:
            return current["version"]  # Reject stale update
        self._data[key] = {"value": value, "version": version}
        if version > self._version:
            self._version = version
        return version
```

### 3.2 The Replicator Class

The `Replicator` class (`kv_store/app/replication.py`) handles concurrent replication with simulated network delays:

```python
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
```

#### **Replication Flow**

1. **Task Creation**: Creates an async task for each follower
2. **Random Delay**: Each task sleeps for a random duration in `[MIN_DELAY, MAX_DELAY]`
3. **HTTP POST**: Sends the replicate request to the follower
4. **Quorum Tracking**: Counts successful acknowledgements
5. **Early Return**: Returns success as soon as quorum is met
6. **Background Completion**: Remaining tasks continue in background

```python
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
```

### 3.3 HTTP API Endpoints

The FastAPI server (`kv_store/app/server.py`) exposes the following RESTful endpoints:

| Method | Path | Role | Description |
|--------|------|------|-------------|
| `GET` | `/health` | All | Liveness probe with role information |
| `GET` | `/kv/{key}` | All | Read a specific key's value and version |
| `GET` | `/kv` | All | Dump entire store (for verification) |
| `PUT` | `/kv/{key}` | Leader only | Write a key-value pair, triggers replication |
| `POST` | `/replicate` | Followers only | Apply a replicated write from the leader |
| `POST` | `/reset` | All | Clear in-memory store (for testing) |

#### **Write Endpoint (Leader Only)**

```python
@app.put("/kv/{key}")
async def write_key(
    key: str,
    payload: WriteRequest,
    store: KeyValueStore = Depends(get_store),
    settings: Settings = Depends(get_settings),
    replicator: Replicator = Depends(get_replicator),
) -> JSONResponse:
    if not settings.is_leader:
        raise HTTPException(status_code=405, detail="Leader only")
    
    value = payload.value
    version = await store.write(key, value)
    result = await replicator.replicate(key, value, version)

    if not result.quorum_met:
        raise HTTPException(
            status_code=503,
            detail={"message": "Write quorum not met", ...}
        )

    return JSONResponse(
        status_code=200,
        content={"key": key, "value": value, "version": version, "acks": result.acknowledgements}
    )
```

### 3.4 Configuration System

All configuration is parsed from environment variables via the `Settings` class:

```python
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
```

#### **Environment Variables**

| Variable | Description | Default |
|----------|-------------|---------|
| `ROLE` | Node role: `leader` or `follower` | `leader` |
| `HOST` | Listen address | `0.0.0.0` |
| `PORT` | Listen port | `8000` |
| `FOLLOWER_URLS` | Comma-separated follower URLs (leader only) | Required |
| `WRITE_QUORUM` | Acknowledgements required for success | `1` |
| `MIN_DELAY_MS` | Minimum replication delay | `0` |
| `MAX_DELAY_MS` | Maximum replication delay | `1000` |
| `REPLICATION_TIMEOUT_MS` | Timeout for replication requests | `3000` |

---

## 4. Replication and Consistency

### 4.1 Single-Leader Replication

The system implements **single-leader replication** as described in DDIA Chapter 5:

- **Leader**: The only node that accepts client writes
- **Followers**: Receive updates exclusively via replication from the leader
- **Reads**: Can be served by any node (leader or followers)

This topology provides:
- **No write conflicts**: All writes go through a single point
- **Simple consistency**: Version ordering is determined by the leader
- **Read scalability**: Followers can serve read traffic

### 4.2 Semi-Synchronous Replication

The implementation uses **semi-synchronous replication** (also called "quorum-based replication"):

1. Client sends a write request to the leader
2. Leader writes locally and assigns a version number
3. Leader dispatches concurrent replication requests to all 5 followers (each with a random delay [0ms, 1000ms])
4. Leader waits until `WRITE_QUORUM` acknowledgements are received
5. Leader returns success to the client
6. Remaining replications continue in the background for eventual consistency

#### **Key Properties**

1. **Durability**: Write is confirmed only after `WRITE_QUORUM` followers persist it
2. **Low Latency**: Leader doesn't wait for all followers, only the fastest `QUORUM`
3. **Eventual Consistency**: All followers eventually receive all writes
4. **Fault Tolerance**: System continues operating if up to `5 - QUORUM` followers fail

### 4.3 Quorum Configuration

The write quorum is configurable via the `WRITE_QUORUM` environment variable:

| Quorum | Behavior | Durability | Latency |
|--------|----------|------------|---------|
| 1 | Wait for 1 ACK | Low (1 replica) | Lowest |
| 2 | Wait for 2 ACKs | Medium | Low |
| 3 | Wait for 3 ACKs | Good | Medium |
| 4 | Wait for 4 ACKs | High | Higher |
| 5 | Wait for all ACKs | Maximum | Highest |

### 4.4 Version Conflict Resolution

When followers receive out-of-order or duplicate updates, the store uses **version-based conflict resolution**:

```python
if current and current["version"] >= version:
    return current["version"]  # Reject stale update
```

This ensures:
- **Last-writer-wins semantics** based on version
- **Idempotent replication** (safe to retry)
- **Monotonic reads** (version never decreases)

---

## 5. Concurrency Model

### 5.1 Async/Await Architecture

Both leader and followers use Python's `asyncio` for concurrent request handling:

```python
# All endpoints are async
@app.put("/kv/{key}")
async def write_key(...):
    version = await store.write(key, value)       # Async store access
    result = await replicator.replicate(...)       # Async replication
    return JSONResponse(...)
```

### 5.2 Concurrent Replication

Replication requests are dispatched concurrently using `asyncio.create_task()`:

```python
tasks = [
    asyncio.create_task(self._replicate_to(url, payload))
    for url in self._follower_urls
]
```

Each task:
1. Sleeps for a random delay (simulating network latency)
2. Sends HTTP POST to the follower
3. Returns success/failure

The leader uses `asyncio.wait()` with `return_when=FIRST_COMPLETED` to process acknowledgements as they arrive:

```python
while pending:
    done, pending = await asyncio.wait(
        pending,
        timeout=self._timeout,
        return_when=asyncio.FIRST_COMPLETED,
    )
    for task in done:
        if task.result():
            acknowledgements += 1
            if acknowledgements >= self._write_quorum:
                quorum_met = True
                break
    if quorum_met:
        break
```

### 5.3 Thread Safety

The `KeyValueStore` uses an `asyncio.Lock` to ensure thread-safe access:

```python
async def write(self, key: str, value: str) -> int:
    async with self._lock:
        self._version += 1
        version = self._version
        self._data[key] = {"value": value, "version": version}
        return version
```

This prevents race conditions when:
- Multiple concurrent writes access the version counter
- Replication and local reads occur simultaneously
- Multiple replication requests update the same key

---

## 6. Testing and Verification

### 6.1 Integration Test Suite

The integration tests (`kv_store/tests/test_integration.py`) verify end-to-end system behavior:

#### **Test: Leader Replication Reaches All Followers**

```python
def test_leader_replication_reaches_all_followers() -> None:
    key = "integration-key"
    value = "value-1"

    # Write via leader
    response = requests.put(f"{LEADER_URL}/kv/{key}", json={"value": value}, timeout=10)
    assert response.status_code == 200

    time.sleep(2)  # Allow replication to complete

    # Verify leader state
    leader_data = requests.get(f"{LEADER_URL}/kv/{key}", timeout=5).json()
    assert leader_data["value"] == value

    # Verify all followers have the same data
    for port in FOLLOWER_PORTS:
        follower_data = requests.get(f"http://localhost:{port}/kv/{key}", timeout=5).json()
        assert follower_data == leader_data
```

#### **Test Fixture: Docker Compose Stack**

```python
@pytest.fixture(scope="session", autouse=True)
def stack() -> Generator[None, None, None]:
    env = os.environ.copy()
    env.setdefault("WRITE_QUORUM", "3")
    
    _run_compose("down", "-v")
    subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d", "--build"],
        cwd=PROJECT_ROOT, check=True, env=env
    )
    
    _wait_for_health(LEADER_URL)
    for port in FOLLOWER_PORTS:
        _wait_for_health(f"http://localhost:{port}")
    
    yield
    
    _run_compose("down", "-v")
```

### 6.2 Running Tests

```powershell
# Run integration tests
pytest kv_store\tests\test_integration.py

# Run with verbose output
pytest -s kv_store\tests\test_integration.py
```

### 6.3 Test Results

```
============================= test session starts =============================
collected 1 item

kv_store\tests\test_integration.py .                                     [100%]

============================== 1 passed in 15.23s =============================
```

---

## 7. Performance Analysis

### 7.1 Experiment Design

The performance analyzer (`scripts/perf_analyzer.py`) measures write latency under varying quorum requirements:

| Parameter | Value |
|-----------|-------|
| Total writes per run | 100 |
| Concurrent workers | 10 |
| Number of keys | 10 |
| Quorum values tested | 1, 2, 3, 4, 5 |
| Network delay range | [0ms, 1000ms] |

### 7.2 Methodology

For each quorum value (1-5):

1. **Start fresh cluster** with specified `WRITE_QUORUM`
2. **Issue 100 writes** with 10 concurrent workers
3. **Measure latency** for each write operation
4. **Verify parity** between leader and all followers
5. **Record results** (avg, min, max latency)

```python
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
```

### 7.3 Results

| Quorum | Writes | Avg Latency (ms) | Min Latency (ms) | Max Latency (ms) | Parity OK |
|--------|--------|------------------|------------------|------------------|-----------|
| 1 | 100 | 209.79 | 8.41 | 594.09 | ✅ |
| 2 | 100 | 324.73 | 61.26 | 728.81 | ✅ |
| 3 | 100 | 493.97 | 111.08 | 923.59 | ✅ |
| 4 | 100 | 677.69 | 174.25 | 1018.61 | ✅ |
| 5 | 100 | 855.07 | 323.60 | 1024.86 | ✅ |

### 7.4 Latency vs. Quorum Plot

![Write Quorum vs Average Latency](screenshots/quorum_latency.png)

### 7.5 Results Analysis

#### **Why Latency Increases with Quorum**

The observed latency increase follows the mathematical properties of **order statistics**:

When replicating to 5 followers with random delays uniformly distributed in [0, 1000ms]:

- **Quorum = 1**: Wait for the **1st (fastest)** acknowledgement
  - Expected delay ≈ 1000ms × (1/6) ≈ 167ms
  
- **Quorum = 3**: Wait for the **3rd fastest** acknowledgement
  - Expected delay ≈ 1000ms × (3/6) = 500ms
  
- **Quorum = 5**: Wait for the **5th (slowest)** acknowledgement
  - Expected delay ≈ 1000ms × (5/6) ≈ 833ms

The actual measurements closely match these theoretical predictions:

| Quorum | Theoretical (ms) | Actual (ms) | Difference |
|--------|------------------|-------------|------------|
| 1 | ~167 | 210 | +43ms |
| 3 | ~500 | 494 | -6ms |
| 5 | ~833 | 855 | +22ms |

The small differences account for:
- HTTP overhead
- Server processing time
- Network stack latency

#### **Replica Parity Verification**

All runs show `parity_ok: true`, confirming:

1. **Eventual consistency is achieved**: Despite random delays and early quorum returns, all followers eventually receive all writes
2. **No data loss**: Background replication completes successfully
3. **Version ordering is preserved**: All replicas converge to the same final state

This validates the semi-synchronous design: fast acknowledgement for clients while maintaining eventual consistency across all replicas.

### 7.6 Trade-off Analysis

| Quorum | Durability | Availability | Latency |
|--------|------------|--------------|---------|
| 1 | Low (1 copy) | High (tolerates 4 failures) | ~210ms |
| 2 | Medium | High (tolerates 3 failures) | ~325ms |
| 3 | Good (majority) | Medium (tolerates 2 failures) | ~494ms |
| 4 | High | Low (tolerates 1 failure) | ~678ms |
| 5 | Maximum | Lowest (requires all nodes) | ~855ms |

**Recommendation**: Quorum = 3 provides a good balance:
- Majority replication (data survives minority failures)
- Acceptable latency (~500ms)
- Tolerates up to 2 follower failures

---

## 8. Deployment and Containerization

### 8.1 Dockerfile

The project uses a simple Python container image:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY kv_store /app/kv_store

ENV PYTHONPATH=/app

CMD ["python", "-m", "kv_store.app.main"]
```

### 8.2 Docker Compose Configuration

The `docker-compose.yml` defines the complete cluster:

```yaml
version: "3.9"

services:
  leader:
    build: .
    container_name: kv_leader
    environment:
      ROLE: leader
      HOST: 0.0.0.0
      PORT: 8000
      FOLLOWER_URLS: http://follower1:8000,http://follower2:8000,...
      WRITE_QUORUM: ${WRITE_QUORUM:-3}
      MIN_DELAY_MS: ${MIN_DELAY_MS:-0}
      MAX_DELAY_MS: ${MAX_DELAY_MS:-1000}
      REPLICATION_TIMEOUT_MS: ${REPLICATION_TIMEOUT_MS:-4000}
    ports:
      - "9000:8000"
    depends_on:
      - follower1
      - follower2
      - follower3
      - follower4
      - follower5

  follower1:
    build: .
    container_name: kv_follower1
    environment:
      ROLE: follower
      HOST: 0.0.0.0
      PORT: 8000
    ports:
      - "9001:8000"

  # follower2 through follower5 follow the same pattern...
```

### 8.3 Network Topology

Docker Compose creates a shared network where:
- Leader communicates with followers via service names (`follower1`, `follower2`, etc.)
- External clients access services via mapped ports (`9000` for leader, `9001-9005` for followers)

---

## 9. Usage and Commands

### 9.1 Starting the Cluster

```powershell
# Build and start all containers
docker compose up --build

# Start in detached mode
docker compose up -d --build

# Start with custom quorum
$env:WRITE_QUORUM=2; docker compose up --build
```

### 9.2 Stopping the Cluster

```powershell
# Stop and remove containers
docker compose down

# Stop and remove volumes
docker compose down -v
```

### 9.3 API Usage Examples

#### **Write a key (leader only)**
```powershell
curl -X PUT http://localhost:9000/kv/mykey -H "Content-Type: application/json" -d '{"value": "hello"}'
```

**Response:**
```json
{"key": "mykey", "value": "hello", "version": 1, "acks": 3}
```

#### **Read a key (any node)**
```powershell
# From leader
curl http://localhost:9000/kv/mykey

# From follower
curl http://localhost:9001/kv/mykey
```

**Response:**
```json
{"value": "hello", "version": 1}
```

#### **Dump all data**
```powershell
curl http://localhost:9000/kv
```

#### **Health check**
```powershell
curl http://localhost:9000/health
```

**Response:**
```json
{"status": "ok", "role": "leader"}
```

### 9.4 Running Tests

```powershell
# Run integration tests
pytest kv_store\tests\test_integration.py

# Run with verbose output
pytest -s kv_store\tests\test_integration.py
```

### 9.5 Running Performance Analysis

```powershell
python scripts\perf_analyzer.py
```

This will:
1. Test quorum values 1 through 5
2. Run 100 writes per quorum setting
3. Generate `artifacts/quorum_latency.png`
4. Save results to `artifacts/perf_summary.json`

---

## Conclusion

This project successfully demonstrates the implementation of a distributed key-value store with single-leader replication. Key achievements include:

1. **Correct single-leader architecture** with write restriction to leader only
2. **Semi-synchronous replication** with configurable write quorum
3. **Concurrent request handling** using Python asyncio
4. **Simulated network conditions** with random delays
5. **Comprehensive testing** including integration tests and performance analysis
6. **Clear latency-quorum trade-off** demonstrated through empirical measurements
7. **Eventual consistency** verified across all replicas

The performance analysis confirms the theoretical relationship between quorum size and write latency, providing practical insights into distributed systems design trade-offs.

---

## References

- Kleppmann, M. (2017). *Designing Data-Intensive Applications*. O'Reilly Media. Chapter 5: Replication.
- FastAPI Documentation: https://fastapi.tiangolo.com/
- Docker Compose Documentation: https://docs.docker.com/compose/
- Python asyncio Documentation: https://docs.python.org/3/library/asyncio.html
