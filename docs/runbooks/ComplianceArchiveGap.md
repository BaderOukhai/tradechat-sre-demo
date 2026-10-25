# Runbook — ComplianceArchiveGap

| | |
|---|---|
| **Severity** | **critical** — page immediately |
| **Fires when** | messages persisted in the Redis Stream are not archived for 1 minute (consumer group lag + pending > 0), or the gap can't be measured |
| **Dashboard** | Grafana → *Messaging SLO* → *Compliance gap* panels |

## What it means

Every trader message must be archived: banks have a legal obligation to retain all trader
communications. The gap counts messages accepted by `chat-api` (`202`) but not yet written to the
archive and acknowledged (`XACK`). They are **not lost yet**: they wait safely in the Redis Stream
(AOF on a persistent volume) and the archiver resumes from the consumer group bookmark.
The risk grows with time: if Redis is lost before the backlog drains, the messages are gone.

The gap is measured at the source (`redis_exporter`, stream `messages`, group `archivers`),
independently of the archiver, so a dead archiver can't hide its own failure.

## Customer impact

None visible to traders: chat keeps working. The impact is **regulatory** for the customer bank
(incomplete archive). That is why this alert is critical while the latency alert is a warning.

## Diagnosis

1. **Is the archiver running?**
   ```bash
   kubectl get deployment,pods -l app=archiver
   kubectl describe pod -l app=archiver | tail -20     # Events: OOMKilled, volume, scheduling
   kubectl logs deploy/archiver --tail=50
   ```
2. **Is the gap growing (stuck) or shrinking (catching up)?**
   ```promql
   sum(redis_stream_group_lag{group="archivers"}) + sum(redis_stream_group_messages_pending{group="archivers"})
   ```
3. **Consumer group state, from Redis itself:**
   ```bash
   kubectl exec deploy/redis -c redis -- redis-cli XINFO GROUPS messages
   ```
   `lag` = never delivered to an archiver; `pending` = delivered but not acknowledged (an archiver
   crashed mid-batch: taken over automatically after 30 s by `XAUTOCLAIM`).
4. **Disk:** the archive volume may be full.
   ```bash
   kubectl exec deploy/archiver -- df -h /data
   ```
5. **Alert fired with "absent"?** The exporter or the consumer group is gone:
   ```bash
   kubectl get pods -l app=redis      # 2/2 expected (redis + exporter)
   ```

## Mitigation

- **Archiver down / scaled to 0** → bring it back; it catches up from its bookmark:
  ```bash
  kubectl scale deployment archiver --replicas=1      # then fix the root cause in Git
  ```
- **Archiver crash looping** → see `PodCrashLooping.md`; roll back if a release caused it:
  `helm rollback tradechat`.
- **Disk full** → expand the PVC (`archiver.storage` in values.yaml) and redeploy.
- **Never** trim or delete the stream to "make the gap disappear": that destroys the only copy.

Recovery check: gap back to 0, alert resolved, and archive line count = stream length:
```bash
kubectl exec deploy/redis -c redis -- redis-cli XLEN messages
kubectl exec deploy/archiver -- sh -c 'cat /data/*.jsonl | wc -l'   # >= XLEN (duplicates possible)
```

## Escalation

- Gap still growing after 15 minutes → escalate to the archiver owner **and** notify the
  compliance officer: the customer may need to be informed.
- Redis at risk (memory limit, pod restarting) while a backlog exists → highest priority.
- Always write a postmortem.
