"""chat-api: persists trader messages to a Redis Stream and fans them out over WebSockets."""

import asyncio
import json
import os
import socket
import time
from collections import defaultdict
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException, Response, WebSocket, WebSocketDisconnect
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from pydantic import BaseModel

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
# Artificial delay to simulate a bad release (step 9). 0 = healthy.
FAULT_LATENCY_MS = int(os.getenv("FAULT_LATENCY_MS", "0"))
STREAM = "messages"
POD = socket.gethostname()  # the pod name in Kubernetes: tells which replica received a message

MESSAGES_RECEIVED = Counter(
    "chat_messages_received_total",
    "Messages accepted (persisted to the stream); compared with archived messages for compliance",
)
DELIVERY_SECONDS = Histogram(
    "chat_message_delivery_seconds",
    "Time from reception to fan-out to WebSocket clients, through the Redis Stream",
    # 0.2 must be a bucket boundary: the SLO counts messages delivered in < 200 ms.
    # 0.3 to 1.0 show how bad a regression is (the injected fault is 300 ms).
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.2, 0.3, 0.5, 1.0),
)
WS_CONNECTIONS = Gauge("chat_websocket_connections", "Open WebSocket connections on this pod")
ERRORS = Counter("chat_errors_total", "Errors by reason", ["reason"])

# room name -> WebSocket clients connected to THIS pod
rooms: dict[str, set[WebSocket]] = defaultdict(set)


class Message(BaseModel):
    sender: str
    room: str
    text: str
    client_ts: float  # set by the client, lets it measure end-to-end latency


async def broadcast(room: str, payload: str) -> None:
    for ws in list(rooms[room]):
        try:
            await ws.send_text(payload)
        except Exception:  # noqa: BLE001 - one broken client must never stop fan-out to others
            ERRORS.labels(reason="ws_send").inc()
            rooms[room].discard(ws)


async def fan_out(r: aioredis.Redis) -> None:
    """Tail the stream and push each message to the WebSocket clients of this pod.

    Plain XREAD (no consumer group): every replica must see every message, so a client
    connected to pod A also receives messages posted to pod B.
    """
    last_id = "$"  # start with new messages only, then resume from the last one seen
    while True:
        try:
            batches = await r.xread({STREAM: last_id}, block=1000)
        except aioredis.RedisError:
            ERRORS.labels(reason="redis_read").inc()
            await asyncio.sleep(1)  # Redis is down: retry without spinning the CPU
            continue
        for _stream, entries in batches:
            for msg_id, fields in entries:
                last_id = msg_id
                await broadcast(fields["room"], json.dumps({"id": msg_id, **fields}))
                if fields["pod"] == POD:  # observe once per message, on the receiving pod
                    DELIVERY_SECONDS.observe(time.time() - float(fields["received_ts"]))


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    task = asyncio.create_task(fan_out(app.state.redis))
    yield
    task.cancel()
    await app.state.redis.aclose()


app = FastAPI(title="chat-api", lifespan=lifespan)


@app.get("/healthz")
async def healthz():
    # Liveness: the process answers. It never checks Redis, otherwise a Redis outage
    # would make Kubernetes restart every chat-api pod, which fixes nothing.
    return {"status": "ok"}


@app.get("/readyz")
async def readyz():
    # Readiness: without Redis we can't persist messages, so take this pod out of traffic.
    try:
        await app.state.redis.ping()
    except aioredis.RedisError:
        raise HTTPException(503, "redis unreachable")
    return {"status": "ready"}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/messages", status_code=202)  # 202: persisted, fan-out happens asynchronously
async def post_message(msg: Message):
    received_ts = time.time()
    if FAULT_LATENCY_MS:
        await asyncio.sleep(FAULT_LATENCY_MS / 1000)
    fields = {**msg.model_dump(), "received_ts": received_ts, "pod": POD}
    try:
        # No MAXLEN: trimming could drop messages the archiver has not persisted yet.
        msg_id = await app.state.redis.xadd(STREAM, fields)
    except aioredis.RedisError:
        ERRORS.labels(reason="redis_publish").inc()
        raise HTTPException(503, "message not persisted, please retry")
    # Counted only once persisted, so "received - archived" is a real compliance gap.
    MESSAGES_RECEIVED.inc()
    return {"id": msg_id}


@app.websocket("/ws/{room}")
async def ws_room(ws: WebSocket, room: str):
    await ws.accept()
    rooms[room].add(ws)
    WS_CONNECTIONS.inc()
    try:
        while True:
            await ws.receive_text()  # clients only listen; this detects disconnection
    except WebSocketDisconnect:
        pass
    finally:
        rooms[room].discard(ws)
        WS_CONNECTIONS.dec()
