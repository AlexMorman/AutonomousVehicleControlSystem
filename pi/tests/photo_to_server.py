#!/usr/bin/env python3
"""Manual test: take one photo and send it to the server for classification.

Uses the same camera settings, connection and password check as
pi_client.py, sends a single frame, and prints the server's answer. The
motors are never touched and nothing is written to the Pi's disk. The
server handles it like any other frame: it runs the configured decider and
records the frame.

    # on the laptop:  cd ~/carlink/server && ~/carlink-server-venv/bin/python server.py --verbose
    cd ~/carlink/pi
    ~/carlink-venv/bin/python tests/photo_to_server.py

Stop pi_client.py first: the server accepts only one car at a time.
"""

import argparse
import json
import logging
import socket
import sys
import time
from pathlib import Path

PI_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PI_DIR))
import pi_client as C  # noqa: E402  (also puts shared/ on the import path)

P = C.P
WARMUP_FRAMES = 10  # let the webcam settle its exposure before the photo


def take_photo(cfg: dict):
    camera = C.Camera(cfg["camera"]).start()
    try:
        seq, captured, frame = camera.latest(newer_than=0, timeout=5.0)
        while seq < WARMUP_FRAMES:
            seq, captured, frame = camera.latest(newer_than=seq, timeout=5.0)
    finally:
        camera.close()
    data, meta = C.encode_frame(frame, cfg["camera"])
    return data, meta, captured


def ask_server(cfg: dict, password: str, data: bytes, meta: dict, captured: float,
               timeout: float) -> tuple[dict, float]:
    srv = cfg["server"]
    sock = socket.create_connection((srv["host"], int(srv["port"])),
                                    timeout=float(srv["connect_timeout_s"]))
    try:
        P.configure_socket(sock)
        sock.settimeout(timeout)
        P.client_handshake(sock, password)
        sent = time.monotonic()
        P.send_message(sock, {"type": "frame", "seq": 1, "capture_time": captured,
                              "last_rtt_ms": None, **meta}, data)
        while True:
            reply, _ = P.recv_message(sock, max_payload=0)
            if reply.get("type") == "command" and reply.get("seq") == 1:
                return reply, time.monotonic() - sent
    finally:
        sock.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Send one photo to the server.")
    parser.add_argument("--config", default=str(PI_DIR / "pi_config.json"))
    parser.add_argument("--timeout", type=float, default=15.0,
                        help="seconds to wait for the answer; default 15")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if not Path(args.config).exists():
        sys.exit(f"{args.config} not found; copy pi_config.example.json and fill it in")
    cfg = P.load_config(args.config, C.DEFAULTS)
    password = P.resolve_password(cfg["server"]["password"])

    print("taking photo ...", flush=True)
    try:
        data, meta, captured = take_photo(cfg)
    except C.CameraError as exc:
        sys.exit(f"camera problem: {exc}")
    print(f"photo: {meta['width']}x{meta['height']} {meta['format']}, {len(data) / 1024:.1f} KB")

    host, port = cfg["server"]["host"], cfg["server"]["port"]
    print(f"sending to {host}:{port} ...", flush=True)
    try:
        reply, rtt = ask_server(cfg, password, data, meta, captured, args.timeout)
    except P.AuthError:
        sys.exit("the server rejected the password; check both configs")
    except P.PeerError as exc:
        sys.exit("the server is busy with another car; stop pi_client.py first"
                 if exc.reason == "busy" else f"server error: {exc}")
    except socket.timeout:
        sys.exit(f"no answer within {args.timeout:g} s")
    except (OSError, P.ProtocolError) as exc:
        sys.exit(f"connection problem: {exc}")

    score = reply.get("score") or 0.0
    print(f"label:  {reply.get('label')!r}  (score {score:.2f})")
    print(f"motors: {json.dumps(reply.get('motors'))}  (not applied)")
    print(f"answer took {rtt * 1000:.0f} ms")


if __name__ == "__main__":
    main()
