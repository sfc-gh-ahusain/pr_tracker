# FrostDB RateKeeper Throttling Incident Runbook

## Overview
This runbook helps investigate RateKeeper (RK) throttling incidents on FrostDB clusters.

## Quick Reference: Throttle Limit Reasons

| Limit Reason | Meaning | Primary Cause |
|--------------|---------|---------------|
| `log_server_mvcc_write_bandwidth` | TLog write rate exceeds MVCC window capacity | TLog can't drain fast enough |
| `log_server_write_queue` | TLog queue depth too high | TLog disk I/O bottleneck |
| `log_server_min_free_space` | TLog running low on disk | Disk capacity issue |
| `storage_server_write_queue_size` | SS write queue too deep | SS can't keep up with mutations |
| `storage_server_durability_lag` | SS durability lag too high | SS disk I/O bottleneck |

---

## Phase 1: Initial Triage (5 min)

### 1.1 Confirm Throttle Event
```
Grafana Query: RateKeeper Throttling panel
- Check if throttle = TRUE
- Note the time window
- Identify the limit_reason
```

### 1.2 Record Key Details
- **Cluster**: _______________
- **Time Window**: _______________ UTC
- **Limit Reason**: _______________
- **Duration**: _______________ minutes

---

## Phase 2: Identify Bottleneck Component

### For `log_server_mvcc_write_bandwidth`

**Root Cause**: TLog receiving writes faster than Storage Servers can drain within MVCC window (~7 sec)

**Check These Metrics**:
1. **TLog Queue Depth** - Which TLog has highest queue?
2. **Storage Server Durability Lag** - Are SS nodes falling behind?
3. **Disk IOPS by Host** - Any host showing 0 or low IOPS?
4. **Disk Latency** - Any latency spikes on TLog/SS nodes?

**Code Reference**: `RKRateUpdater.cpp:632-656`
```cpp
// Throttle triggers when:
// inputRate > (logTargetBytes - logSpringBytes) / mvccWindowSeconds()
double x = getMaxTLogRate() / inputRate;
if (lim < tpsLimit) {
    limitReason = limitReason_t::log_server_mvcc_write_bandwidth;
}
```

### For `log_server_write_queue`

**Root Cause**: TLog disk write queue depth exceeded threshold

**Check These Metrics**:
1. **TLog Queue Bytes** - Per TLog node
2. **Disk Write Latency** - On TLog nodes
3. **Disk IOPS** - TLog node disk saturation

### For `storage_server_durability_lag`

**Root Cause**: Storage Server falling behind durable version

**Check These Metrics**:
1. **SS Durability Lag** - Per SS node (in versions or seconds)
2. **SS Disk Write Latency** - Any nodes with high latency?
3. **SS CPU Usage** - Any nodes at 100%?

---

## Phase 3: Host-Level Investigation

### 3.1 Identify Problematic Host
Look for hosts with anomalous behavior:
- **0 IOPS** - Host may be stuck/failed
- **High CPU** - Compute bottleneck
- **High Disk Latency** - I/O bottleneck
- **Network Errors** - Connectivity issues

### 3.2 Check Host Role
Determine what FDB process runs on the problematic host:
- TLog
- Storage Server
- Commit Proxy
- GRV Proxy

### 3.3 Check for Recovery Events
```
FDB Status JSON: Check for recent recoveries
- recovery_state
- recovery_time
- excluded_servers
```

---

## Phase 4: Common Patterns & Solutions

### Pattern A: Single Slow TLog
**Symptoms**:
- One TLog with high queue depth
- Other TLogs healthy
- `log_server_mvcc_write_bandwidth` or `log_server_write_queue`

**Actions**:
1. Check disk health on that host
2. Check for noisy neighbors (if shared infrastructure)
3. Consider excluding the TLog

### Pattern B: Storage Server Falling Behind
**Symptoms**:
- High SS durability lag
- TLog queues backing up
- `storage_server_durability_lag` or cascading to `log_server_*`

**Actions**:
1. Check SS disk I/O
2. Check SS CPU usage
3. Check if SS is doing heavy reads (compaction?)

### Pattern C: Sudden Write Spike
**Symptoms**:
- All TLogs see increased input rate
- Short-duration throttle
- Correlates with customer workload

**Actions**:
1. Check transaction rate increase
2. Identify source of write spike
3. May be expected behavior during batch loads

### Pattern D: Hardware Failure
**Symptoms**:
- Host showing 0 IOPS or offline
- Recovery event in cluster
- Throttle during recovery

**Actions**:
1. Confirm hardware status
2. Check if host was excluded
3. Verify cluster recovered to healthy state

---

## Phase 5: Metrics Queries

### Grafana Dashboards
- **FrostDB Cluster Overview**: CPU, IOPS, Throughput
- **RateKeeper Native Metrics**: Throttle state, limit reason
- **TLog Metrics**: Queue depth, write rate
- **Storage Server Metrics**: Durability lag, fetch rate

### Snowhouse Queries (if available)
```sql
-- Example: Query FDB trace events
SELECT * FROM ENG_FDB.TRACE_EVENTS
WHERE cluster_name = 'va2fdb2'
  AND event_time BETWEEN '2026-01-24 11:00:00' AND '2026-01-25 12:00:00'
  AND event_type IN ('RkUpdate', 'StorageServerDurabilityLag', 'TLogQueueInfo')
ORDER BY event_time;
```

---

## Phase 6: Escalation Criteria

Escalate to FDB oncall if:
- [ ] Throttle duration > 30 minutes
- [ ] Multiple hosts showing issues
- [ ] Recovery loop detected
- [ ] Data unavailability reported
- [ ] Root cause unclear after investigation

---

## Appendix: FrostDB Write Path

```
Client Transaction
       │
       ▼
┌─────────────────┐
│  Commit Proxy   │  ← Receives commit, resolves conflicts
└────────┬────────┘
         │ TLogCommitRequest
         ▼
┌─────────────────┐
│     TLog        │  ← Persists mutations to disk
│                 │     (RK monitors this)
└────────┬────────┘
         │ Peek Cursor (pull)
         ▼
┌─────────────────┐
│ Storage Server  │  ← Applies mutations, persists
│                 │     (RK monitors durability lag)
└─────────────────┘
```

**Key RK Monitoring Points**:
1. TLog input rate vs drain rate
2. TLog queue depth
3. SS durability lag (version distance from TLog)

---

## Document Info
- **Created**: 2026-01-24
- **Cluster**: va2fdb2
- **Incident**: RK throttling 11:00-12:00 UTC
- **Limit Reason**: log_server_mvcc_write_bandwidth
