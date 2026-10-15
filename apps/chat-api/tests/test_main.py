import time

import fakeredis
import pytest
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

import main

MSG = {"sender": "alice", "room": "fx-desk", "text": "EURUSD 1.0850 bid", "client_ts": 0.0}


@pytest.fixture
def client(monkeypatch):
    fake = fakeredis.FakeAsyncRedis(decode_responses=True)
    monkeypatch.setattr(main.aioredis, "from_url", lambda *args, **kwargs: fake)
    with TestClient(main.app) as c:
        yield c


def test_healthz(client):
    assert client.get("/healthz").status_code == 200


def test_readyz_fails_when_redis_is_down(client, monkeypatch):
    async def down():
        raise main.aioredis.ConnectionError("down")

    assert client.get("/readyz").status_code == 200
    monkeypatch.setattr(main.app.state.redis, "ping", down)
    assert client.get("/readyz").status_code == 503


def test_post_message_is_counted(client):
    before = REGISTRY.get_sample_value("chat_messages_received_total")
    assert client.post("/messages", json=MSG).status_code == 202
    assert REGISTRY.get_sample_value("chat_messages_received_total") == before + 1


def test_publish_failure_returns_503_and_is_not_counted(client, monkeypatch):
    async def down(*args, **kwargs):
        raise main.aioredis.ConnectionError("down")

    monkeypatch.setattr(main.app.state.redis, "xadd", down)
    before = REGISTRY.get_sample_value("chat_messages_received_total")
    assert client.post("/messages", json=MSG).status_code == 503
    assert REGISTRY.get_sample_value("chat_messages_received_total") == before
    assert REGISTRY.get_sample_value("chat_errors_total", {"reason": "redis_publish"}) >= 1


def test_message_is_fanned_out_to_websocket(client):
    with client.websocket_connect("/ws/fx-desk") as ws:
        client.post("/messages", json=MSG)
        got = ws.receive_json()
    assert got["text"] == MSG["text"]
    assert got["pod"] == main.POD


def test_fault_latency_slows_down_requests(client, monkeypatch):
    monkeypatch.setattr(main, "FAULT_LATENCY_MS", 300)
    start = time.perf_counter()
    client.post("/messages", json=MSG)
    assert time.perf_counter() - start >= 0.3
