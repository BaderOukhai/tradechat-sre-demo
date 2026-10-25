"""tradechat-ops: a read-only MCP server that lets an AI assistant check the platform's health.

Guardrails (see README.md):
- read-only: tools only query Prometheus, Alertmanager and the runbooks; nothing changes the cluster;
- audited: every tool call is appended to an audit log (who asked what, when, with what outcome);
- no secrets: only metric values, alert texts and runbooks are returned.
"""

import json
import os
import re
import time
from collections.abc import Callable
from pathlib import Path

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://localhost:9090")
ALERTMANAGER_URL = os.getenv("ALERTMANAGER_URL", "http://localhost:9093")
RUNBOOK_DIR = Path(__file__).resolve().parent.parent / "docs" / "runbooks"
AUDIT_LOG = Path(os.getenv("AUDIT_LOG", Path(__file__).resolve().parent / "audit.log"))
SLO_TARGET = 0.999  # 99.9% of messages delivered in under 200 ms
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False)

GAP = (
    'sum(redis_stream_group_lag{stream="messages", group="archivers"})'
    ' + sum(redis_stream_group_messages_pending{stream="messages", group="archivers"})'
)

mcp = MCPServer(
    "tradechat-ops",
    instructions=(
        "Read-only operations assistant for the tradechat messaging platform. "
        "Check alerts first, then the SLO and the compliance gap, then propose the runbook. "
        "You cannot change anything: recommend commands, a human runs them."
    ),
)


def audited(tool: str, args: dict, compute: Callable[[], object]) -> object:
    """Run a tool and append one JSON line to the audit log, whatever the outcome."""
    started = time.time()
    outcome = "ok"
    try:
        return compute()
    except Exception as e:
        outcome = f"error: {type(e).__name__}: {e}"
        raise
    finally:
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(started)),
            "tool": tool,
            "args": args,
            "outcome": outcome,
            "duration_ms": round((time.time() - started) * 1000),
        }
        with AUDIT_LOG.open("a") as f:
            f.write(json.dumps(entry) + "\n")


def query(promql: str) -> float | None:
    """Instant PromQL query returning a single number (None if there is no data)."""
    resp = httpx.get(f"{PROMETHEUS_URL}/api/v1/query", params={"query": promql}, timeout=5)
    resp.raise_for_status()
    result = resp.json()["data"]["result"]
    return float(result[0]["value"][1]) if result else None


@mcp.tool(annotations=READ_ONLY)
def get_slo_status() -> dict:
    """Delivery SLO (99.9% of messages in under 200 ms): SLI over 5 min, error budget left
    over 1 h, current p99 latency, and an overall status."""

    def compute() -> dict:
        sli = query(
            'sum(rate(chat_message_delivery_seconds_bucket{le="0.2"}[5m]))'
            " / sum(rate(chat_message_delivery_seconds_count[5m]))"
        )
        bad_ratio_1h = query(
            '1 - sum(increase(chat_message_delivery_seconds_bucket{le="0.2"}[1h]))'
            " / sum(increase(chat_message_delivery_seconds_count[1h]))"
        )
        p99 = query(
            "histogram_quantile(0.99, sum by (le) (rate(chat_message_delivery_seconds_bucket[1m])))"
        )
        budget = None if bad_ratio_1h is None else 1 - bad_ratio_1h / (1 - SLO_TARGET)
        if sli is None:
            status = "NO DATA (no traffic?)"
        elif sli >= SLO_TARGET:
            status = "OK"
        else:
            status = "BREACHED" if budget is not None and budget < 0 else "AT RISK"
        return {
            "status": status,
            "slo_target": SLO_TARGET,
            "sli_5m": sli,
            "error_budget_remaining_1h": budget,
            "p99_latency_seconds": p99,
        }

    return audited("get_slo_status", {}, compute)


@mcp.tool(annotations=READ_ONLY)
def get_active_alerts() -> list[dict]:
    """Alerts currently firing in Alertmanager, with severity, description and runbook name."""

    def compute() -> list[dict]:
        resp = httpx.get(
            f"{ALERTMANAGER_URL}/api/v2/alerts",
            params={"active": "true", "silenced": "false", "inhibited": "false"},
            timeout=5,
        )
        resp.raise_for_status()
        return [
            {  # only selected fields: never forward arbitrary labels or annotations
                "alert": a["labels"].get("alertname"),
                "severity": a["labels"].get("severity"),
                "since": a.get("startsAt"),
                "summary": a["annotations"].get("summary"),
                "description": a["annotations"].get("description"),
                "runbook": a["labels"].get("alertname"),
            }
            for a in resp.json()
        ]

    return audited("get_active_alerts", {}, compute)


@mcp.tool(annotations=READ_ONLY)
def get_runbook(alert_name: str) -> str:
    """The runbook (diagnosis and mitigation steps) for an alert, e.g. ComplianceArchiveGap."""

    def compute() -> str:
        # Only plain alert names: blocks path traversal such as "../../etc/passwd".
        if not re.fullmatch(r"[A-Za-z0-9]+", alert_name):
            # ToolError: the message is sent back to the assistant (other exceptions are hidden).
            raise ToolError("alert_name must be a plain alert name, e.g. HighMessageLatencyP99")
        path = RUNBOOK_DIR / f"{alert_name}.md"
        if not path.is_file():
            available = sorted(p.stem for p in RUNBOOK_DIR.glob("*.md"))
            raise ToolError(f"no runbook for {alert_name!r}; available: {available}")
        return path.read_text()

    return audited("get_runbook", {"alert_name": alert_name}, compute)


@mcp.tool(annotations=READ_ONLY)
def get_compliance_gap() -> dict:
    """Messages accepted but not yet archived (must be 0), and received vs archived rates."""

    def compute() -> dict:
        gap = query(GAP)
        return {
            "status": "UNKNOWN" if gap is None else ("OK" if gap == 0 else "GAP"),
            "messages_not_archived": gap,
            "received_per_second": query("sum(rate(chat_messages_received_total[1m]))"),
            "archived_per_second": query("sum(rate(archiver_messages_archived_total[1m]))"),
        }

    return audited("get_compliance_gap", {}, compute)


if __name__ == "__main__":
    mcp.run()  # stdio transport: the MCP client (Claude Code / Desktop) starts this process
