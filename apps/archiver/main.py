"""archiver: consumes the message stream and writes every message to an append-only JSONL archive."""

import asyncio
import json
import os
import socket
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
ARCHIVE_DIR = Path(os.getenv("ARCHIVE_DIR", "data"))
STREAM = "messages"
GROUP = "archivers"
CONSUMER = socket.gethostname()  # the pod name in Kubernetes
# A message read but not acknowledged for 30 s is taken over by another archiver:
# far longer than a normal write + XACK (milliseconds), so we never steal from a live
# archiver, and shorter than the 1 min ComplianceArchiveGap alert.
CLAIM_IDLE_MS = 30_000
BATCH = 100  # messages per read: one fsync per batch instead of one per message

ARCHIVED = Counter(
    "archiver_messages_archived_total", "Messages written to the archive, then acknowledged"
)


async def ensure_group(r: aioredis.Redis) -> None:
    try:
        # id="0": also archive messages published before the archiver ever started.
        await r.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
    except aioredis.ResponseError as e:
        if "BUSYGROUP" not in str(e):  # BUSYGROUP = group already exists, normal on restart
            raise


def write(entries: list) -> None:
    path = ARCHIVE_DIR / f"messages-{datetime.now(UTC):%Y-%m-%d}.jsonl"  # one file per day
    with path.open("a") as f:
        for msg_id, fields in entries:
            f.write(json.dumps({"id": msg_id, **fields}) + "\n")
        f.flush()
        os.fsync(f.fileno())  # really on disk, not only in the OS cache, before we XACK


async def archive(r: aioredis.Redis, entries: list) -> None:
    if not entries:
        return
    write(entries)
    # XACK only AFTER the write: a crash in between means a re-delivery (duplicate), never a loss.
    await r.xack(STREAM, GROUP, *[msg_id for msg_id, _ in entries])
    ARCHIVED.inc(len(entries))


async def archive_once(r: aioredis.Redis) -> None:
    # 1. Take over messages that an archiver read but never acknowledged (it crashed).
    _next, claimed, _deleted = await r.xautoclaim(
        STREAM, GROUP, CONSUMER, CLAIM_IDLE_MS, count=BATCH
    )
    await archive(r, claimed)
    # 2. New messages, never delivered to any archiver (">").
    batches = await r.xreadgroup(GROUP, CONSUMER, {STREAM: ">"}, count=BATCH, block=1000)
    for _stream, entries in batches:
        await archive(r, entries)


async def run(r: aioredis.Redis) -> None:
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    group_ready = False
    while True:
        try:
            if not group_ready:
                await ensure_group(r)
                group_ready = True
            await archive_once(r)
        except aioredis.RedisError:
            group_ready = False  # Redis may have restarted empty: recreate the group
            await asyncio.sleep(1)  # retry without spinning the CPU
        # Any other error (e.g. disk full) stops the loop on purpose: see /healthz.


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    app.state.task = asyncio.create_task(run(app.state.redis))
    yield
    app.state.task.cancel()
    await app.state.redis.aclose()


app = FastAPI(title="archiver", lifespan=lifespan)


@app.get("/healthz")
async def healthz():
    # Liveness: the real work is the background loop. If it died (e.g. disk full), the
    # process is alive but useless, so fail and let Kubernetes restart it.
    # No /readyz: readiness only routes Service traffic, and nobody sends traffic here.
    if app.state.task.done():
        raise HTTPException(503, "archive loop stopped")
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
