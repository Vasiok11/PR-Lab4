# Leader-Follower Key-Value Store

FastAPI-based key-value service demonstrating single-leader replication with semi-synchronous write acknowledgements, packaged for local development with Docker Compose.

## Prerequisites
- Docker & Docker Compose
- Python 3.11+ (for running tests/scripts locally)

## Setup
Install dependencies if you want to run components directly:

```bash
pip install -r requirements.txt
```

## Running the Cluster
```
WRITE_QUORUM=3 docker compose up -d --build
```

Services:
- Leader available at `http://localhost:9000`
- Followers at `http://localhost:9001-9005`

## API Quickstart
```
# Write via leader
curl -X PUT http://localhost:9000/kv/foo -H "Content-Type: application/json" -d '{"value":"bar"}'

# Read from any replica
curl http://localhost:9002/kv/foo
```

## Integration Test
Runs docker compose under the hood and validates that followers converge after a write:

```bash
pytest kv_store/tests/test_integration.py
```

## Performance Analysis
Generates ~100 writes for each quorum (1..5), records latency, plots results, and verifies data parity:

```bash
python scripts/perf_analyzer.py
```

Artifacts are written to `artifacts/quorum_latency.csv` and `artifacts/quorum_latency.png`.

## Cleanup
```
docker compose down -v
```
