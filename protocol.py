"""Shared message format, framing, password check and config loading.

Both pi_client.py and server.py import this file, so copy the SAME version of it
to both machines. If you change it on one side, change it on the other.

Wire format
-----------
Every message, in both directions, is:

    4 bytes   header length H   (unsigned int, big-endian)
    4 bytes   payload length P  (unsigned int, big-endian)
    H bytes   header: a UTF-8 JSON object that always has a "type" field
    P bytes   payload: raw bytes (the image, for "frame" messages; empty otherwise)

TCP delivers a stream of bytes with no message boundaries, so the two length
fields are how the receiver knows where one message ends and the next begins.

Message types
-------------
    server -> Pi   {"type": "challenge", "version": 1, "nonce": "<hex>"}
    Pi -> server   {"type": "auth", "version": 1, "mac": "<hex>"}
    server -> Pi   {"type": "auth_ok"}
    Pi -> server   {"type": "frame", "seq": n, ...image metadata...}  + image bytes
    server -> Pi   {"type": "command", "seq": n, "label": ..., "score": ...,
                    "motors": {"front_left": x, "front_right": x,
                               "rear_left": x, "rear_right": x}}
    either way     {"type": "error", "reason": "<code>", "detail": "<text>"}

Password check
--------------
The server sends a random one-time challenge. The Pi answers with an HMAC of
that challenge keyed with the shared password, which proves it knows the
password without ever sending it. This keeps strangers on the network from
driving the car. It does NOT encrypt anything: images and commands travel in
the clear.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import logging
import os
import secrets
import socket
import struct
from pathlib import Path

PROTOCOL_VERSION = 1

# The four motors, in a fixed order. Commands always carry all four.
MOTOR_NAMES = ("front_left", "front_right", "rear_left", "rear_right")

# Image formats the Pi can send. "jpeg" is the default.
IMAGE_FORMATS = ("jpeg", "png", "raw")
FILE_EXTENSIONS = {"jpeg": ".jpg", "png": ".png", "raw": ".raw"}

DEFAULT_PORT = 5005
PASSWORD_ENV_VAR = "CARLINK_PASSWORD"
PLACEHOLDER_PASSWORD = "change-me"

_PREFIX = struct.Struct("!II")
MAX_HEADER_BYTES = 64 * 1024
DEFAULT_MAX_PAYLOAD_BYTES = 8 * 1024 * 1024

log = logging.getLogger("protocol")


class ProtocolError(Exception):
    """The other side sent something that doesn't follow the protocol."""


class AuthError(ProtocolError):
    """The password check failed. Retrying with the same password won't help."""


class PeerError(ProtocolError):
    """The other side sent an explicit error message (for example "busy")."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


# --------------------------------------------------------------------------
# Sockets and framing
# --------------------------------------------------------------------------

def configure_socket(sock: socket.socket) -> None:
    """Send small messages immediately instead of batching them (no Nagle delay)."""
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)


def send_message(sock: socket.socket, header: dict, payload: bytes = b"") -> None:
    header_bytes = json.dumps(header, separators=(",", ":")).encode("utf-8")
    sock.sendall(_PREFIX.pack(len(header_bytes), len(payload)) + header_bytes + payload)


def send_error(sock: socket.socket, reason: str, detail: str = "") -> None:
    """Best-effort error notice before closing a connection. Never raises."""
    try:
        send_message(sock, {"type": "error", "reason": reason, "detail": detail})
    except OSError:
        pass


def _recv_exact(sock: socket.socket, count: int) -> bytes:
    buf = bytearray()
    while len(buf) < count:
        chunk = sock.recv(min(count - len(buf), 65536))
        if not chunk:
            raise ConnectionError("connection closed by the other side")
        buf += chunk
    return bytes(buf)


def recv_message(sock: socket.socket,
                 max_payload: int = DEFAULT_MAX_PAYLOAD_BYTES) -> tuple[dict, bytes]:
    """Read one complete message. Raises PeerError if it is an error message.

    Uses whatever timeout is set on the socket; socket.timeout is raised if it
    expires.
    """
    header_len, payload_len = _PREFIX.unpack(_recv_exact(sock, _PREFIX.size))
    if header_len > MAX_HEADER_BYTES:
        raise ProtocolError(f"header too large ({header_len} bytes)")
    if payload_len > max_payload:
        raise ProtocolError(f"payload too large ({payload_len} bytes, limit {max_payload})")
    try:
        header = json.loads(_recv_exact(sock, header_len).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"header is not valid JSON: {exc}") from None
    if not isinstance(header, dict) or "type" not in header:
        raise ProtocolError("header must be a JSON object with a 'type' field")
    payload = _recv_exact(sock, payload_len) if payload_len else b""
    if header["type"] == "error":
        raise PeerError(str(header.get("reason", "unknown")), str(header.get("detail", "")))
    return header, payload


# --------------------------------------------------------------------------
# Password check
# --------------------------------------------------------------------------

def _mac(password: str, nonce: str) -> str:
    return hmac.new(password.encode("utf-8"),
                    b"carlink-v1:" + nonce.encode("ascii"),
                    hashlib.sha256).hexdigest()


def server_handshake(sock: socket.socket, password: str) -> None:
    """Challenge the connecting Pi. Raises AuthError if it fails the check."""
    nonce = secrets.token_hex(16)
    send_message(sock, {"type": "challenge", "version": PROTOCOL_VERSION, "nonce": nonce})
    header, _ = recv_message(sock, max_payload=0)
    if header.get("type") != "auth":
        send_error(sock, "protocol", "expected an 'auth' message")
        raise ProtocolError(f"expected 'auth', got {header.get('type')!r}")
    if header.get("version") != PROTOCOL_VERSION:
        send_error(sock, "version_mismatch",
                   f"server speaks version {PROTOCOL_VERSION}, client {header.get('version')}")
        raise ProtocolError("protocol version mismatch; copy the same protocol.py to both machines")
    if not hmac.compare_digest(str(header.get("mac", "")), _mac(password, nonce)):
        send_error(sock, "bad_password", "password does not match the server's")
        raise AuthError("client failed the password check")
    send_message(sock, {"type": "auth_ok"})


def client_handshake(sock: socket.socket, password: str) -> None:
    """Answer the server's challenge. Raises AuthError on a wrong password."""
    header, _ = recv_message(sock, max_payload=0)  # PeerError("busy") can arrive here
    if header.get("type") != "challenge":
        raise ProtocolError(f"expected 'challenge', got {header.get('type')!r}")
    if header.get("version") != PROTOCOL_VERSION:
        raise ProtocolError(
            f"server speaks protocol version {header.get('version')}, this client "
            f"{PROTOCOL_VERSION}; copy the same protocol.py to both machines")
    send_message(sock, {"type": "auth", "version": PROTOCOL_VERSION,
                        "mac": _mac(password, str(header.get("nonce", "")))})
    try:
        header, _ = recv_message(sock, max_payload=0)
    except PeerError as exc:
        if exc.reason == "bad_password":
            raise AuthError("the server rejected the password") from None
        raise
    if header.get("type") != "auth_ok":
        raise ProtocolError(f"expected 'auth_ok', got {header.get('type')!r}")


# --------------------------------------------------------------------------
# Config files
# --------------------------------------------------------------------------

def _merge(defaults: dict, overrides: dict, path: str = "") -> dict:
    merged = copy.deepcopy(defaults)
    for key, value in overrides.items():
        if key.startswith("_"):  # "_comment" keys are notes for humans
            continue
        if key not in defaults:
            log.warning("config: unknown setting '%s%s' ignored", path, key)
            continue
        if isinstance(defaults[key], dict) and isinstance(value, dict):
            merged[key] = _merge(defaults[key], value, f"{path}{key}.")
        else:
            merged[key] = value
    return merged


def load_config(path: str | os.PathLike | None, defaults: dict) -> dict:
    """Load a JSON config file on top of built-in defaults.

    Missing settings fall back to the defaults. Keys starting with "_" are
    ignored, so you can leave "_comment" notes in the file. The environment
    variable CARLINK_PASSWORD, if set, overrides the password in the file.
    """
    config = copy.deepcopy(defaults)
    if path is not None:
        with open(Path(path), encoding="utf-8") as fh:
            try:
                data = json.load(fh)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"config file {path} is not valid JSON: {exc}") from None
        config = _merge(defaults, data)
    return config


def resolve_password(configured: str) -> str:
    password = os.environ.get(PASSWORD_ENV_VAR) or configured
    if not password:
        raise SystemExit("no password set: put one in the config file or "
                         f"in the {PASSWORD_ENV_VAR} environment variable")
    if password == PLACEHOLDER_PASSWORD:
        log.warning("using the placeholder password '%s'; change it on BOTH machines",
                    PLACEHOLDER_PASSWORD)
    return password
