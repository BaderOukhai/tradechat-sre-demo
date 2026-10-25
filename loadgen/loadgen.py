# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx>=0.28", "websockets>=13"]
# ///
"""loadgen: simulated traders that post messages and listen on WebSockets.

Client-side latency = time from the POST to the moment the sender sees its own message
come back on its WebSocket (network + persist to Redis + fan-out). This is what a trader feels.
"""

import argparse
import asyncio
import json
import random
import statistics
import time
from collections import Counter

import httpx
import websockets

counts: Counter[str] = Counter()  # "sent" = POST accepted (202)
errors: Counter[str] = Counter()
latencies: list[float] = []  # seconds, one per message received back by its sender


async def listen(ws_url: str, room: str, me: str, connected: asyncio.Event) -> None:
    while True:  # reconnect forever: a chat-api pod may die during a resilience demo
        try:
            async with websockets.connect(f"{ws_url}/ws/{room}") as ws:
                connected.set()
                async for raw in ws:
                    msg = json.loads(raw)
                    if msg["sender"] == me:  # same machine, so the same clock as client_ts
                        latencies.append(time.time() - float(msg["client_ts"]))
        # WebSocketException covers closed connections and failed handshakes (e.g. reconnecting
        # to a pod that is shutting down during a rolling update).
        except (OSError, websockets.WebSocketException):
            errors["ws_disconnect"] += 1
            await asyncio.sleep(1)  # retry without hammering a recovering server


async def trade(http: httpx.AsyncClient, room: str, me: str, interval: float, deadline: float):
    await asyncio.sleep(random.uniform(0, interval))  # spread traders: no synchronized bursts
    next_send = time.monotonic()
    while time.monotonic() < deadline:
        body = {
            "sender": me,
            "room": room,
            "text": f"quote {random.randint(1, 999)}",
            "client_ts": time.time(),
        }
        try:
            resp = await http.post("/messages", json=body)
            if resp.status_code == 202:
                counts["sent"] += 1
            else:
                errors[f"http_{resp.status_code}"] += 1
        except httpx.HTTPError:
            errors["http_error"] += 1
        next_send += interval  # fixed schedule: a slow request doesn't shift later sends
        await asyncio.sleep(max(0, next_send - time.monotonic()))


async def report(start: float) -> None:
    while True:
        await asyncio.sleep(10)
        print(
            f"[{time.monotonic() - start:4.0f}s] sent={counts['sent']} "
            f"received={len(latencies)} errors={dict(errors)}"
        )


def summary(duration: float) -> None:
    sent, received = counts["sent"], len(latencies)
    print(f"\n--- summary ({duration:.0f}s) ---")
    print(
        f"sent={sent} ({sent / duration:.1f} msg/s)  received={received}  "
        f"missing={sent - received}  errors={dict(errors)}"
    )
    if received >= 2:
        q = statistics.quantiles(latencies, n=100)  # q[49] = p50, q[94] = p95, q[98] = p99
        print(
            f"latency ms: p50={q[49] * 1000:.1f}  p95={q[94] * 1000:.1f}  "
            f"p99={q[98] * 1000:.1f}  max={max(latencies) * 1000:.1f}"
        )


async def main(args: argparse.Namespace) -> None:
    ws_url = args.url.replace("http", "ws", 1)
    traders = [(f"trader-{i}", f"desk-{i % args.rooms}") for i in range(args.traders)]
    ready = [asyncio.Event() for _ in traders]
    listeners = [
        asyncio.create_task(listen(ws_url, room, me, ev)) for (me, room), ev in zip(traders, ready)
    ]
    # Every trader must be listening before anyone sends, or first messages look "missing".
    await asyncio.wait_for(asyncio.gather(*(ev.wait() for ev in ready)), timeout=10)

    start = time.monotonic()
    reporter = asyncio.create_task(report(start))
    interval = args.traders / args.rate  # each trader's pause so the total equals --rate
    async with httpx.AsyncClient(base_url=args.url, timeout=5) as http:
        await asyncio.gather(
            *(trade(http, room, me, interval, start + args.duration) for me, room in traders)
        )
    await asyncio.sleep(2)  # let in-flight messages come back before counting
    for task in [*listeners, reporter]:
        task.cancel()
    summary(args.duration)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Simulated traders for chat-api")
    p.add_argument("--url", default="http://localhost:8000")
    # 20 traders in 4 desks: realistic small trading floor, light enough for a laptop.
    p.add_argument("--traders", type=int, default=20)
    p.add_argument("--rooms", type=int, default=4)
    # 20 msg/s total (1 msg/s per trader): fills the latency histogram fast, keeps CPU low.
    p.add_argument("--rate", type=float, default=20)
    # 120 s: several 15 s Prometheus scrapes, enough for rate() over 1 min windows.
    p.add_argument("--duration", type=float, default=120)
    asyncio.run(main(p.parse_args()))
