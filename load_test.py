"""
load_test.py — Simulates 15 concurrent agent nodes for demo / stress testing.
Each thread acts as a full agent but generates synthetic metrics instead of
reading real hardware sensors, so it can run on any single machine.

Usage:
    python load_test.py                     # all 15 nodes, localhost
    python load_test.py --host 192.168.1.5  # against remote server
    python load_test.py --nodes 5           # only 5 nodes
    python load_test.py --spike             # node 1 sends critical metrics
"""

import argparse
import logging
import math
import random
import socket
import ssl
import threading
import time

from protocol import build_telemetry, read_message, MSG_ACK

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DEFAULT_HOST  = "127.0.0.1"
DEFAULT_PORT  = 9999
INTERVAL      = 2.0
TIMEOUT       = 3.0
MAX_RETRIES   = 3
CERTFILE      = "certs/client.crt"
KEYFILE       = "certs/client.key"
CAFILE        = "certs/ca.crt"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-8s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("load_test")


# ---------------------------------------------------------------------------
# Synthetic metric generators
# ---------------------------------------------------------------------------
def normal_metrics(node_id: int, t: float) -> dict:
    """Gently oscillating metrics — stays within normal thresholds."""
    base_cpu  = 30 + 15 * math.sin(t / 10 + node_id)
    base_mem  = 45 + 10 * math.sin(t / 15 + node_id * 0.7)
    base_disk = 40 + 5  * math.sin(t / 60 + node_id * 0.3)
    return {
        "cpu_pct":  round(max(0, min(100, base_cpu  + random.gauss(0, 2))), 2),
        "mem_pct":  round(max(0, min(100, base_mem  + random.gauss(0, 1))), 2),
        "disk_pct": round(max(0, min(100, base_disk + random.gauss(0, 0.5))), 2),
        "load_avg": round(max(0, 0.8 + 0.4 * math.sin(t / 8) + random.gauss(0, 0.1)), 3),
    }


def spike_metrics(t: float) -> dict:
    """Periodic CPU spikes that cross warning then critical thresholds."""
    phase = (t % 30) / 30          # 0→1 over 30-second cycle
    if phase < 0.3:
        cpu = 60 + random.gauss(0, 3)   # normal
    elif phase < 0.6:
        cpu = 82 + random.gauss(0, 2)   # warning zone
    else:
        cpu = 94 + random.gauss(0, 1)   # critical zone
    return {
        "cpu_pct":  round(max(0, min(100, cpu)), 2),
        "mem_pct":  round(70 + random.gauss(0, 2), 2),
        "disk_pct": round(50 + random.gauss(0, 1), 2),
        "load_avg": round(max(0, 2.0 + random.gauss(0, 0.3)), 3),
    }


# ---------------------------------------------------------------------------
# Single simulated agent
# ---------------------------------------------------------------------------
def simulate_agent(node_id: int, host: str, port: int, use_spike: bool = False):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.load_cert_chain(certfile=CERTFILE, keyfile=KEYFILE)
    ctx.load_verify_locations(cafile=CAFILE)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2

    seq_num   = 0
    start     = time.time()

    while True:
        try:
            raw = socket.create_connection((host, port), timeout=TIMEOUT)
            with ctx.wrap_socket(raw, server_hostname=host) as tls:
                tls.settimeout(TIMEOUT)
                log.info("node=%d connected", node_id)

                while True:
                    t       = time.time() - start
                    metrics = (spike_metrics(t)
                               if use_spike and node_id == 1
                               else normal_metrics(node_id, t))
                    frame   = build_telemetry(node_id, seq_num, metrics)

                    ack = None
                    for attempt in range(1, MAX_RETRIES + 1):
                        try:
                            tls.sendall(frame)
                            ack = read_message(tls)
                            break
                        except (TimeoutError, socket.timeout):
                            log.warning("node=%d ACK timeout attempt %d/%d",
                                        node_id, attempt, MAX_RETRIES)

                    if ack is None:
                        log.error("node=%d lost connection — reconnecting", node_id)
                        break

                    if ack and ack.get("type") == MSG_ACK:
                        status = ack.get("status", "?")
                        if status != "OK":
                            log.warning("node=%d status=%s seq=%d",
                                        node_id, status, seq_num)

                    seq_num += 1
                    time.sleep(INTERVAL)

        except (ConnectionRefusedError, OSError, ssl.SSLError) as exc:
            log.error("node=%d error: %s — retry in 5s", node_id, exc)
            time.sleep(5)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Load test: simulate N agent nodes")
    parser.add_argument("--host",  default=DEFAULT_HOST)
    parser.add_argument("--port",  type=int, default=DEFAULT_PORT)
    parser.add_argument("--nodes", type=int, default=15,
                        help="Number of simulated nodes (default 15)")
    parser.add_argument("--spike", action="store_true",
                        help="Node 1 generates periodic critical CPU spikes")
    args = parser.parse_args()

    log.info("Starting %d simulated nodes → %s:%d (spike=%s)",
             args.nodes, args.host, args.port, args.spike)

    threads = []
    for node_id in range(1, args.nodes + 1):
        t = threading.Thread(
            target=simulate_agent,
            args=(node_id, args.host, args.port, args.spike),
            daemon=True,
            name=f"agent-{node_id}",
        )
        t.start()
        threads.append(t)
        time.sleep(0.1)   # stagger connections slightly

    log.info("All %d agents running. Ctrl+C to stop.", args.nodes)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Load test stopped.")


if __name__ == "__main__":
    main()