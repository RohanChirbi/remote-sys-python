"""
protocol.py — Custom application-layer telemetry protocol
Wire layout: [1B magic] [2B length, big-endian] [N bytes JSON UTF-8]
"""

import json
import struct
import time

MAGIC = 0xAB
HEADER_FMT = "!BH"          # magic (1B) + length (2B) = 3 bytes
HEADER_SIZE = struct.calcsize(HEADER_FMT)  # 3

MSG_TELEMETRY = "TELEMETRY"
MSG_ACK       = "ACK"

STATUS_OK       = "OK"
STATUS_WARN     = "WARN"
STATUS_CRIT     = "CRIT"
STATUS_STALE    = "STALE"


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------

def build_telemetry(node_id: int, seq_num: int, metrics: dict) -> bytes:
    """Construct a TELEMETRY message ready to send over the socket."""
    payload = {
        "type":      MSG_TELEMETRY,
        "node_id":   node_id,
        "seq_num":   seq_num,
        "timestamp": round(time.time(), 3),
        "metrics":   metrics,
    }
    return _frame(payload)


def build_ack(node_id: int, seq_num: int, status: str) -> bytes:
    """Construct an ACK message for the server to return."""
    payload = {
        "type":      MSG_ACK,
        "node_id":   node_id,
        "seq_num":   seq_num,
        "status":    status,
        "server_ts": round(time.time(), 3),
    }
    return _frame(payload)


def _frame(payload: dict) -> bytes:
    """Encode dict → JSON → prefix with [magic][length]."""
    body = json.dumps(payload).encode("utf-8")
    if len(body) > 0xFFFF:
        raise ValueError("Payload exceeds maximum frame size (65535 bytes)")
    header = struct.pack(HEADER_FMT, MAGIC, len(body))
    return header + body


# ---------------------------------------------------------------------------
# Deserialisation
# ---------------------------------------------------------------------------

def read_message(sock) -> dict | None:
    """
    Blocking read of exactly one framed message from a connected socket.
    Returns parsed dict or None if the connection closed cleanly.
    Raises ValueError on protocol violations (bad magic, truncated frame).
    """
    header = _recv_exact(sock, HEADER_SIZE)
    if header is None:
        return None  # peer closed connection

    magic, length = struct.unpack(HEADER_FMT, header)
    if magic != MAGIC:
        raise ValueError(f"Bad magic byte: 0x{magic:02X} (expected 0x{MAGIC:02X})")
    if length == 0:
        raise ValueError("Zero-length payload is not valid")

    body = _recv_exact(sock, length)
    if body is None:
        raise ValueError("Connection closed mid-frame (truncated payload)")

    try:
        msg = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON parse error: {exc}") from exc

    _validate(msg)
    return msg


def _recv_exact(sock, n: int) -> bytes | None:
    """Read exactly n bytes from sock. Returns None on clean close."""
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def _validate(msg: dict):
    """Minimal schema check — raises ValueError on missing required keys."""
    required = {"type", "node_id", "seq_num"}
    missing = required - msg.keys()
    if missing:
        raise ValueError(f"Message missing required fields: {missing}")
    if msg["type"] not in (MSG_TELEMETRY, MSG_ACK):
        raise ValueError(f"Unknown message type: {msg['type']!r}")