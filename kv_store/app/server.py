from __future__ import annotations

import logging
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .config import Settings
from .replication import Replicator
from .store import KeyValueStore

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


app = FastAPI(title="Leader-Follower KV Store")

_settings: Optional[Settings] = None
_store: Optional[KeyValueStore] = None
_replicator: Optional[Replicator] = None


async def get_store() -> KeyValueStore:
    if _store is None:
        raise RuntimeError("Store not initialized")
    return _store


def get_settings() -> Settings:
    if _settings is None:
        raise RuntimeError("Settings not initialized")
    return _settings


def get_replicator() -> Replicator:
    if _replicator is None:
        raise RuntimeError("Replicator not initialized for leader")
    return _replicator


@app.on_event("startup")
async def on_startup() -> None:
    global _settings, _store, _replicator
    _settings = Settings.from_env()
    _store = KeyValueStore()
    if _settings.is_leader:
        _replicator = Replicator(
            follower_urls=_settings.follower_urls,
            write_quorum=_settings.write_quorum,
            min_delay_ms=_settings.min_delay_ms,
            max_delay_ms=_settings.max_delay_ms,
            timeout_ms=_settings.replication_timeout_ms,
        )
        logger.info(
            "Leader online with %s followers (quorum=%s)",
            len(_settings.follower_urls),
            _settings.write_quorum,
        )
    else:
        logger.info("Follower online")


@app.on_event("shutdown")
async def on_shutdown() -> None:
    global _replicator
    if _replicator is not None:
        await _replicator.close()
        _replicator = None


class WriteRequest(BaseModel):
    value: str


class ReplicateRequest(BaseModel):
    key: str
    value: str
    version: int


@app.get("/health")
async def health(settings: Settings = Depends(get_settings)) -> dict:
    return {"status": "ok", "role": settings.role}


@app.get("/kv/{key}")
async def read_key(key: str, store: KeyValueStore = Depends(get_store)) -> dict:
    value = await store.read(key)
    if value is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Key not found")
    return value


@app.get("/kv")
async def dump(store: KeyValueStore = Depends(get_store)) -> dict:
    return await store.dump()


@app.put("/kv/{key}")
async def write_key(
    key: str,
    payload: WriteRequest,
    store: KeyValueStore = Depends(get_store),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    if not settings.is_leader:
        raise HTTPException(status_code=status.HTTP_405_METHOD_NOT_ALLOWED, detail="Leader only")
    
    replicator = get_replicator()
    value = payload.value
    version = await store.write(key, value)
    result = await replicator.replicate(key, value, version)

    if not result.quorum_met:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "message": "Write quorum not met",
                "acks": result.acknowledgements,
                "required": settings.write_quorum,
            },
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "key": key,
            "value": value,
            "version": version,
            "acks": result.acknowledgements,
        },
    )


@app.post("/replicate")
async def replicate(
    payload: ReplicateRequest,
    store: KeyValueStore = Depends(get_store),
    settings: Settings = Depends(get_settings),
) -> dict:
    if settings.is_leader:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Leader cannot replicate to itself")
    key = payload.key
    value = payload.value
    version = payload.version
    applied_version = await store.apply_replica(key, value, version)
    return {"version": applied_version}


@app.post("/reset")
async def reset(store: KeyValueStore = Depends(get_store)) -> dict:
    await store.reset()
    return {"status": "cleared"}
