"""
alert_engine.py — Threshold evaluation with hysteresis and edge-triggering.

State machine per node:
    NORMAL → WARNING → CRITICAL → RECOVERING → NORMAL
                                              → STALE  (injected by watchdog)

Hysteresis:  a state transition only fires after HYSTERESIS_COUNT consecutive
             samples agree, preventing flapping on borderline values.

Edge-trigger: alerts are emitted ONLY on a state change, not on every packet.
"""

import logging
import time
from dataclasses import dataclass, field
from protocol import STATUS_OK, STATUS_WARN, STATUS_CRIT, STATUS_STALE

log = logging.getLogger("alert_engine")

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------
THRESHOLDS = {
    "cpu_pct":  {"warn": 15.0, "crit": 25.0},
    "mem_pct":  {"warn": 85.0, "crit": 92.0},
    "disk_pct": {"warn": 43.0, "crit": 48.0},
}

# Number of consecutive breaching samples required before state promotion
HYSTERESIS_COUNT = 2

# Internal state names
STATE_NORMAL     = "NORMAL"
STATE_WARNING    = "WARNING"
STATE_CRITICAL   = "CRITICAL"
STATE_RECOVERING = "RECOVERING"
STATE_STALE      = "STALE"


# ---------------------------------------------------------------------------
# Per-node state
# ---------------------------------------------------------------------------
@dataclass
class NodeState:
    node_id:       int
    state:         str  = STATE_NORMAL
    breach_count:  int  = 0          # consecutive samples above threshold
    recover_count: int  = 0          # consecutive samples below threshold
    last_seen:     float = field(default_factory=time.time)
    last_seq:      int  = -1

    def status_str(self) -> str:
        """Map internal state to protocol status string."""
        return {
            STATE_NORMAL:     STATUS_OK,
            STATE_WARNING:    STATUS_WARN,
            STATE_CRITICAL:   STATUS_CRIT,
            STATE_RECOVERING: STATUS_WARN,   # still elevated while recovering
            STATE_STALE:      STATUS_STALE,
        }.get(self.state, STATUS_OK)


# ---------------------------------------------------------------------------
# Alert engine
# ---------------------------------------------------------------------------
class AlertEngine:
    def __init__(self, log_path: str = "alerts.log"):
        self._nodes: dict[int, NodeState] = {}
        self._log_path = log_path

    def get_or_create(self, node_id: int) -> NodeState:
        if node_id not in self._nodes:
            self._nodes[node_id] = NodeState(node_id=node_id)
        return self._nodes[node_id]

    def evaluate(self, node_id: int, seq_num: int, metrics: dict) -> str:
        """
        Process one telemetry sample.
        Returns the protocol status string for the ACK reply.
        Emits alert log entries only on state transitions (edge-trigger).
        """
        ns = self.get_or_create(node_id)
        ns.last_seen = time.time()

        # Detect dropped packets
        if ns.last_seq >= 0 and seq_num != ns.last_seq + 1:
            gap = seq_num - ns.last_seq - 1
            if gap > 0:
                log.warning("node=%d seq gap detected: expected %d got %d (%d dropped)",
                            node_id, ns.last_seq + 1, seq_num, gap)
        ns.last_seq = seq_num

        # Determine worst breach level across all metrics
        worst = self._worst_level(metrics)

        # --- Hysteresis + state machine ---
        prev_state = ns.state

        if worst == "crit":
            ns.breach_count  += 1
            ns.recover_count  = 0
            if ns.breach_count >= HYSTERESIS_COUNT:
                ns.state = STATE_CRITICAL
        elif worst == "warn":
            ns.breach_count  += 1
            ns.recover_count  = 0
            if ns.breach_count >= HYSTERESIS_COUNT:
                if ns.state != STATE_CRITICAL:
                    ns.state = STATE_WARNING
        else:
            ns.breach_count   = 0
            ns.recover_count += 1
            if ns.state in (STATE_WARNING, STATE_CRITICAL):
                ns.state = STATE_RECOVERING
            if ns.recover_count >= HYSTERESIS_COUNT and ns.state == STATE_RECOVERING:
                ns.state = STATE_NORMAL

        # Edge-trigger: only emit on transition
        if ns.state != prev_state:
            self._emit(node_id, prev_state, ns.state, metrics)

        return ns.status_str()

    def mark_stale(self, node_id: int):
        """Called by the watchdog when a node stops reporting."""
        ns = self.get_or_create(node_id)
        if ns.state != STATE_STALE:
            log.warning("[STALE] node=%d last_seen=%.1fs ago",
                        node_id, time.time() - ns.last_seen)
            self._emit(node_id, ns.state, STATE_STALE, {})
            ns.state = STATE_STALE

    def all_nodes(self) -> dict[int, NodeState]:
        return self._nodes

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------
    def _worst_level(self, metrics: dict) -> str:
        level = "ok"
        for key, limits in THRESHOLDS.items():
            val = metrics.get(key)
            if val is None:
                continue
            if val >= limits["crit"]:
                return "crit"          # no need to check further
            if val >= limits["warn"]:
                level = "warn"
        return level

    def _emit(self, node_id: int, prev: str, curr: str, metrics: dict):
        """Write one alert line to console + alerts.log."""
        line = (f"[ALERT] node={node_id} {prev}->{curr} "
                f"cpu={metrics.get('cpu_pct','?')} "
                f"mem={metrics.get('mem_pct','?')} "
                f"disk={metrics.get('disk_pct','?')}")
        if curr in (STATE_CRITICAL, STATE_STALE):
            log.critical(line)
        else:
            log.warning(line)
        with open(self._log_path, "a") as f:
            f.write(line + "\n")