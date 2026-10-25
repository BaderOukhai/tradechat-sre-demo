# ops-agent — a read-only MCP server for on-call

A small [Model Context Protocol](https://modelcontextprotocol.io) server (official Python SDK) that
gives an AI assistant (Claude Code, Claude Desktop…) a **read-only** view of the platform, so the
on-call engineer can ask *"Is the messaging platform healthy? What should I do?"* during an incident.

| Tool | Source | Returns |
|---|---|---|
| `get_active_alerts` | Alertmanager API | firing alerts: name, severity, since, description, runbook name |
| `get_slo_status` | Prometheus API | SLI (5 min), error budget left (1 h), p99, status OK / AT RISK / BREACHED |
| `get_compliance_gap` | Prometheus API | messages accepted but not archived (must be 0), received vs archived rates |
| `get_runbook(alert_name)` | `docs/runbooks/*.md` | the runbook for an alert |

## Guardrails (the part that matters for production)

1. **Read-only by construction.** No tool writes anything, runs `kubectl`/`helm` or calls a
   mutating API; tools are declared with `read_only_hint`. The assistant *recommends* commands
   (from the runbooks); a human runs them. Automating actions would need approvals, scoped
   credentials and a dry-run mode first.
2. **Audited.** Every call — including refused ones — is appended to `audit.log` as one JSON line:
   timestamp, tool, arguments, outcome, duration. For a bank: who asked what, when, and what the
   agent saw is reconstructible.
3. **No secrets, minimal data.** The server holds no credentials (local Prometheus/Alertmanager),
   returns metric values and selected alert fields only (never raw labels), and `get_runbook`
   only accepts a plain alert name (`[A-Za-z0-9]+`): no path traversal into the filesystem.
4. **Least privilege in production:** a dedicated read-only service account / API token, network
   access to the monitoring APIs only, rate limits, and the audit log shipped to the SIEM (Splunk).

## Use it

The stack must be running (`make cluster images monitoring deploy`).

- **Claude Code:** the repo's `.mcp.json` declares the server; start `claude` at the repo root and
  approve `tradechat-ops`.
- **Claude Desktop:** add to `claude_desktop_config.json`:
  ```json
  {
    "mcpServers": {
      "tradechat-ops": {
        "command": "uv",
        "args": ["run", "--directory", "/absolute/path/to/tradechat-sre-demo/ops-agent", "python", "server.py"]
      }
    }
  }
  ```

During the incident drill (`make incident`), ask: *"Is the messaging platform healthy? What
should I do?"* — the assistant lists the firing alert, checks the SLO and the compliance gap, and
quotes the runbook's mitigation (`helm rollback tradechat`). Then check `ops-agent/audit.log`.

## Develop

```bash
cd ops-agent
uv sync
uv run pytest -q        # guardrail tests: path traversal refused, every call audited, all tools read-only
```
