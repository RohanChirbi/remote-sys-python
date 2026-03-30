# Remote System Health Monitoring Service

Academic demo — TCP with SSL/TLS, custom JSON telemetry protocol, threshold alerting.

---

## Files

| File | Purpose |
|---|---|
| `protocol.py` | Wire framing: magic byte + length prefix + JSON |
| `alert_engine.py` | Thresholds, hysteresis (3 samples), edge-triggered state machine |
| `server.py` | TLS server — one thread per client, stale watchdog |
| `client.py` | Real agent — collects live CPU/mem/disk via psutil |
| `load_test.py` | Simulates 15 concurrent agents (synthetic metrics) |
| `generate_certs.sh` | Generates self-signed CA, server cert, client cert |

---

## Setup (run once)

```bash
pip install psutil
chmod +x generate_certs.sh && ./generate_certs.sh
```

---

## Running the demo

**Terminal 1 — server (always start first)**
```bash
python server.py
```

**Terminal 2 — option A: single real agent (reads your machine's actual metrics)**
```bash
python client.py --node-id 1
```

**Terminal 2 — option B: full 15-node load test with CPU spike demo**
```bash
python load_test.py --spike
```

> `--spike` makes node 1 cycle NORMAL → WARNING → CRITICAL every 30 seconds.
> Run either `client.py` or `load_test.py`, not both, unless mixing real + synthetic agents intentionally.

---

## What to watch

| Log line | What it shows |
|---|---|
| `ACK status=OK rtt=Xms` | Normal round-trip |
| `[ALERT] NORMAL->WARNING` | Hysteresis crossed — 3 consecutive samples above threshold |
| `[ALERT] WARNING->CRITICAL` | Edge-triggered state promotion |
| `[ALERT] CRITICAL->RECOVERING` | Metrics dropped below threshold |
| `[STALE] node=N` | Watchdog: no packet for 10 seconds |
| `alerts.log` on disk | Persistent record of every state transition |

---

## Thresholds

| Metric | Warning | Critical |
|---|---|---|
| CPU | ≥ 75% | ≥ 90% |
| Memory | ≥ 80% | ≥ 92% |
| Disk | ≥ 85% | ≥ 95% |

Hysteresis: 3 consecutive breaching samples before state changes.
Stale timeout: 10 seconds of silence.

---

## Protocol

```
Frame:  [0xAB magic (1B)] [length (2B big-endian)] [JSON payload (UTF-8)]

TELEMETRY → server:  { type, node_id, seq_num, timestamp, metrics:{cpu_pct, mem_pct, disk_pct, load_avg} }
ACK       ← server:  { type, node_id, seq_num, status:OK|WARN|CRIT|STALE, server_ts }
```

No ACK within 3 seconds → agent retransmits up to 3 times, then reconnects.