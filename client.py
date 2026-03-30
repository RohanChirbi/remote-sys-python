"""
client.py — Remote monitoring agent
  - Collects real CPU / memory / disk / load metrics via psutil
  - Connects to server over TLS (mutual auth)
  - Sends TELEMETRY every INTERVAL seconds
  - Retransmits up to MAX_RETRIES times if no ACK received

Usage:
    python client.py --node-id 1
    python client.py --node-id 2 --host 192.168.1.10
"""

import argparse
import logging
import socket
import ssl
import time

import psutil

from protocol import (
    build_telemetry, read_message,
    MSG_ACK, STATUS_WARN, STATUS_CRIT, STATUS_STALE,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DEFAULT_HOST    = "127.0.0.1"
DEFAULT_PORT    = 9999
INTERVAL        = 2.0          # seconds between telemetry sends
TIMEOUT         = 3.0          # socket timeout waiting for ACK
MAX_RETRIES     = 3            # retransmit attempts before giving up
CERTFILE        = "certs/client.crt"
KEYFILE         = "certs/client.key"
CAFILE          = "certs/ca.crt"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-8s] node=%(node)s %(message)s",
    datefmt="%H:%M:%S",
)


class NodeAdapter(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        return msg, {**kwargs, "extra": {**self.extra, **(kwargs.get("extra") or {})}}


# ---------------------------------------------------------------------------
# Metric collection
# ---------------------------------------------------------------------------
def collect_metrics() -> dict:
    load1, _, _ = psutil.getloadavg()
    return {
        "cpu_pct":  round(psutil.cpu_percent(interval=0.5), 2),
        "mem_pct":  round(psutil.virtual_memory().percent, 2),
        "disk_pct": round(psutil.disk_usage("/").percent, 2),
        "load_avg": round(load1, 3),
    }


# ---------------------------------------------------------------------------
# SSL context
# ---------------------------------------------------------------------------
def make_ssl_context() -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.load_cert_chain(certfile=CERTFILE, keyfile=KEYFILE)
    ctx.load_verify_locations(cafile=CAFILE)
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


# ---------------------------------------------------------------------------
# Main agent loop
# ---------------------------------------------------------------------------
def run_agent(node_id: int, host: str, port: int):
    log = logging.getLogger("client")
    extra = {"node": node_id}

    ctx = make_ssl_context()
    seq_num = 0

    log.info("Starting — connecting to %s:%d", host, port, extra=extra)

    while True:
        try:
            raw = socket.create_connection((host, port), timeout=TIMEOUT)
            with ctx.wrap_socket(raw, server_hostname=host) as tls:
                log.info("TLS connection established", extra=extra)
                tls.settimeout(TIMEOUT)

                while True:
                    metrics = collect_metrics()
                    frame   = build_telemetry(node_id, seq_num, metrics)

                    ack = None
                    for attempt in range(1, MAX_RETRIES + 1):
                        try:
                            tls.sendall(frame)
                            ack = read_message(tls)
                            break
                        except (TimeoutError, socket.timeout):
                            log.warning("ACK timeout (attempt %d/%d)",
                                        attempt, MAX_RETRIES, extra=extra)

                    if ack is None:
                        log.error("No ACK after %d attempts — reconnecting",
                                  MAX_RETRIES, extra=extra)
                        break  # exit inner loop → reconnect

                    if ack.get("type") == MSG_ACK:
                        status = ack.get("status", "?")
                        rtt_ms = round((time.time() - ack["server_ts"]) * 1000, 1)

                        if status == STATUS_CRIT:
                            log.critical("Server status=CRIT rtt=%.1fms metrics=%s",
                                         rtt_ms, metrics, extra=extra)
                        elif status == STATUS_WARN:
                            log.warning("Server status=WARN rtt=%.1fms metrics=%s",
                                        rtt_ms, metrics, extra=extra)
                        elif status == STATUS_STALE:
                            log.error("Server reports STALE for this node",
                                      extra=extra)
                        else:
                            log.info("ACK status=OK rtt=%.1fms", rtt_ms, extra=extra)

                    seq_num += 1
                    time.sleep(INTERVAL)

        except ssl.SSLError as exc:
            log.error("TLS error: %s — retrying in 5s", exc, extra=extra)
            time.sleep(5)
        except (ConnectionRefusedError, OSError) as exc:
            log.error("Connection failed: %s — retrying in 5s", exc, extra=extra)
            time.sleep(5)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Health monitoring agent")
    parser.add_argument("--node-id", type=int, required=True,
                        help="Unique node ID (1–15)")
    parser.add_argument("--host", default=DEFAULT_HOST,
                        help="Server hostname or IP")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help="Server port")
    args = parser.parse_args()

    run_agent(args.node_id, args.host, args.port)