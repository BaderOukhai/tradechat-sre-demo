import json

import pytest

import server


@pytest.fixture(autouse=True)
def audit_log(tmp_path, monkeypatch):
    path = tmp_path / "audit.log"
    monkeypatch.setattr(server, "AUDIT_LOG", path)
    return path


def audit_entries(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_get_runbook_returns_the_markdown(audit_log):
    text = server.get_runbook("ComplianceArchiveGap")
    assert "# Runbook" in text
    assert audit_entries(audit_log)[-1]["outcome"] == "ok"


@pytest.mark.parametrize("name", ["../../etc/passwd", "ComplianceArchiveGap.md", "/etc/hosts"])
def test_get_runbook_rejects_anything_but_a_plain_name(name, audit_log):
    with pytest.raises(server.ToolError):
        server.get_runbook(name)
    entry = audit_entries(audit_log)[-1]
    assert entry["args"] == {"alert_name": name}
    assert entry["outcome"].startswith("error")  # refused calls are audited too


def test_slo_status_breached_when_budget_is_spent(monkeypatch):
    # 95% of messages under 200 ms over 5 min, 2% bad over 1 h, p99 at 300 ms.
    def fake_query(promql):
        if "[5m]" in promql:
            return 0.95
        if "[1h]" in promql:
            return 0.02
        return 0.3

    monkeypatch.setattr(server, "query", fake_query)
    status = server.get_slo_status()
    assert status["status"] == "BREACHED"
    assert status["error_budget_remaining_1h"] == pytest.approx(1 - 0.02 / 0.001)


def test_compliance_gap_ok_when_zero(monkeypatch, audit_log):
    monkeypatch.setattr(server, "query", lambda promql: 0.0)
    assert server.get_compliance_gap()["status"] == "OK"
    assert audit_entries(audit_log)[-1]["tool"] == "get_compliance_gap"


def test_every_tool_is_declared_read_only():
    import asyncio

    tools = asyncio.run(server.mcp.list_tools())
    assert {t.name for t in tools} == {
        "get_slo_status",
        "get_active_alerts",
        "get_runbook",
        "get_compliance_gap",
    }
    assert all(t.annotations.read_only_hint for t in tools)
