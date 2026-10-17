import asyncio
import json

import fakeredis
import pytest

import main


@pytest.fixture(autouse=True)
def archive_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "ARCHIVE_DIR", tmp_path)
    return tmp_path


def archived_ids(archive_dir):
    lines = [line for f in archive_dir.glob("*.jsonl") for line in f.read_text().splitlines()]
    return [json.loads(line)["id"] for line in lines]


async def pending(r):
    return (await r.xpending(main.STREAM, main.GROUP))["pending"]


def test_archives_messages_published_before_start(archive_dir):
    async def scenario():
        r = fakeredis.FakeAsyncRedis(decode_responses=True)
        ids = [await r.xadd(main.STREAM, {"text": f"m{i}"}) for i in range(3)]
        await main.ensure_group(r)
        await main.archive_once(r)
        return ids, await pending(r)

    ids, still_pending = asyncio.run(scenario())
    assert archived_ids(archive_dir) == ids
    assert still_pending == 0


def test_takes_over_messages_left_unacked_by_a_crashed_archiver(archive_dir, monkeypatch):
    monkeypatch.setattr(main, "CLAIM_IDLE_MS", 0)  # don't wait 30 s in a unit test

    async def scenario():
        r = fakeredis.FakeAsyncRedis(decode_responses=True)
        await main.ensure_group(r)
        msg_id = await r.xadd(main.STREAM, {"text": "hi"})
        # Another archiver reads the message, then crashes before XACK.
        await r.xreadgroup(main.GROUP, "crashed-pod", {main.STREAM: ">"})
        await main.archive_once(r)
        return msg_id, await pending(r)

    msg_id, still_pending = asyncio.run(scenario())
    assert archived_ids(archive_dir) == [msg_id]
    assert still_pending == 0


def test_no_ack_when_write_fails(monkeypatch):
    def disk_full(entries):
        raise OSError("No space left on device")

    monkeypatch.setattr(main, "write", disk_full)

    async def scenario():
        r = fakeredis.FakeAsyncRedis(decode_responses=True)
        await main.ensure_group(r)
        await r.xadd(main.STREAM, {"text": "hi"})
        with pytest.raises(OSError):
            await main.archive_once(r)
        return await pending(r)

    assert asyncio.run(scenario()) == 1  # still pending: it will be retried, not lost
