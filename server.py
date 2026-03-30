"""
server.py — Central aggregation server
  - SSL/TLS over TCP (no UDP, no DTLS)
  - One thread per connected client
  - Alert engine with hysteresis + edge-triggering
  - Stale watchdog runs as a daemon thread (5-second tick)

Usage:
    python server.py
"""

import logging
import socket
import ssl
import threading
import time

from alert_engine import AlertEngine
from protocol import read_message, build_ack, MSG_TELEMETRY

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
HOST            = "0.0.0.0"
PORT            = 9999
CERTFILE        = "certs/server.crt"
KEYFILE         = "certs/server.key"
CAFILE          = "certs/ca.crt"
STALE_TIMEOUT   = 10.0   # seconds without a packet → STALE
WATCHDOG_TICK   = 5.0    # watchdog wakes every N seconds

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("server")

# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------
engine = AlertEngine(log_path="alerts.log")
engine_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Client handler (one thread per connection)
# ---------------------------------------------------------------------------
def handle_client(conn: ssl.SSLSocket, addr: tuple):
    node_label = str(addr)
    log.info("Connection from %s", addr)
    try:
        while True:
            msg = read_message(conn)
            if msg is None:
                log.info("Client %s disconnected", addr)
                break

            if msg["type"] != MSG_TELEMETRY:
                log.warning("Unexpected message type from %s: %s", addr, msg["type"])
                continue

            node_id = msg["node_id"]
            seq_num = msg["seq_num"]
            metrics = msg.get("metrics", {})
            node_label = f"node={node_id}"

            log.info("%-10s seq=%-6d cpu=%-5.1f mem=%-5.1f disk=%-5.1f",
                     node_label, seq_num,
                     metrics.get("cpu_pct", 0),
                     metrics.get("mem_pct", 0),
                     metrics.get("disk_pct", 0))

            with engine_lock:
                status = engine.evaluate(node_id, seq_num, metrics)

            ack = build_ack(node_id, seq_num, status)
            conn.sendall(ack)

    except ValueError as exc:
        log.error("Protocol error from %s: %s", addr, exc)
    except (ConnectionResetError, BrokenPipeError, OSError) as exc:
        log.warning("Connection lost %s: %s", addr, exc)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Stale watchdog
# ---------------------------------------------------------------------------
def watchdog():
    log.info("Watchdog started (tick=%.0fs, timeout=%.0fs)",
             WATCHDOG_TICK, STALE_TIMEOUT)
    while True:
        time.sleep(WATCHDOG_TICK)
        now = time.time()
        with engine_lock:
            for node_id, ns in engine.all_nodes().items():
                if now - ns.last_seen > STALE_TIMEOUT:
                    engine.mark_stale(node_id)


# ---------------------------------------------------------------------------
# SSL context
# ---------------------------------------------------------------------------
def make_ssl_context() -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=CERTFILE, keyfile=KEYFILE)
    ctx.load_verify_locations(cafile=CAFILE)
    # For demo: require client cert (mutual TLS)
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    ctx = make_ssl_context()

    # Start watchdog as a background daemon thread
    t = threading.Thread(target=watchdog, daemon=True, name="watchdog")
    t.start()

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as raw_sock:
        raw_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        raw_sock.bind((HOST, PORT))
        raw_sock.listen(20)
        log.info("Listening on %s:%d (TLS)", HOST, PORT)

        with ctx.wrap_socket(raw_sock, server_side=True) as tls_sock:
            while True:
                try:
                    conn, addr = tls_sock.accept()
                    t = threading.Thread(
                        target=handle_client,
                        args=(conn, addr),
                        daemon=True,
                        name=f"client-{addr[1]}",
                    )
                    t.start()
                except ssl.SSLError as exc:
                    log.warning("TLS handshake failed: %s", exc)
                except KeyboardInterrupt:
                    log.info("Server shutting down.")
                    break


if __name__ == "__main__":
    main()