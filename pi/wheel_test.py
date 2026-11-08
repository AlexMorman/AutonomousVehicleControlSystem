#!/usr/bin/env python3
"""Spin one or more motors on the Bonnet for a few seconds, then stop.

Reads pi_config.json (next to this script) for the wiring, the reverse flags
and the Bonnet's I2C address, so "forward" means forward on the car.

    python3 wheel_test.py all                     # all four, half power, 5 s
    python3 wheel_test.py front_left              # by motor name
    python3 wheel_test.py 1 3                     # by Bonnet terminal
    python3 wheel_test.py all --power 0.3         # gentler
    python3 wheel_test.py rear_right --power -0.5 # backwards
    python3 wheel_test.py 2 --seconds 2
    python3 wheel_test.py 1 2 3 4 --reverse 2 3   # ignore the config's reverse flags,
                                                  # flip exactly M2 and M3 instead
    python3 wheel_test.py 1 2 3 4 --no-config     # raw terminals, nothing reversed

Lift the wheels off the ground first. Ctrl+C stops the motors immediately.
--power is the fraction of battery voltage (0.5 on 4 x AA is about 3 V). It
is applied directly; max_throttle and min_throttle from the config are not
used, so this tests the hardware, not the driving settings.
"""

import argparse
import signal
import sys
import time
from pathlib import Path

from pi_client import DEFAULTS, P  # P is shared/protocol.py


def load_motor_config(path: str, use_config: bool) -> dict:
    if not use_config:
        return DEFAULTS["motors"]
    if not Path(path).exists():
        print(f"warning: {path} not found; using built-in wiring with nothing reversed")
        return DEFAULTS["motors"]
    return P.load_config(path, DEFAULTS)["motors"]


def resolve_terminals(targets: list, wiring: dict) -> list:
    """Turn 'all', motor names and terminal numbers into a sorted list of terminals."""
    terminals = set()
    for target in targets:
        if target == "all":
            terminals.update((1, 2, 3, 4))
        elif target in wiring:
            terminals.add(int(wiring[target]["terminal"]))
        elif target in ("1", "2", "3", "4"):
            terminals.add(int(target))
        else:
            sys.exit(f"unknown motor {target!r}: use 1-4, all, or one of {', '.join(P.MOTOR_NAMES)}")
    return sorted(terminals)


def main() -> None:
    parser = argparse.ArgumentParser(description="Spin Bonnet motors briefly, then stop.")
    parser.add_argument("targets", nargs="+", metavar="MOTOR",
                        help="terminal 1-4, a motor name (front_left, ...), or all")
    parser.add_argument("--power", type=float, default=0.5,
                        help="-1.0 (full reverse) to 1.0 (full forward); default 0.5")
    parser.add_argument("--seconds", type=float, default=5.0, help="how long to run; default 5")
    parser.add_argument("--reverse", nargs="+", type=int, choices=[1, 2, 3, 4], default=None,
                        metavar="TERMINAL",
                        help="flip exactly these terminals, instead of the config's reverse flags")
    parser.add_argument("--address", type=lambda s: int(s, 0), default=None,
                        help="Bonnet I2C address; default from the config (0x60)")
    parser.add_argument("--config", default=str(Path(__file__).with_name("pi_config.json")))
    parser.add_argument("--no-config", action="store_true",
                        help="ignore pi_config.json: raw terminals, nothing reversed")
    args = parser.parse_args()
    if not -1.0 <= args.power <= 1.0:
        parser.error("--power must be between -1.0 and 1.0")

    motors_cfg = load_motor_config(args.config, not args.no_config)
    wiring = motors_cfg["wiring"]
    name_of = {int(w["terminal"]): name for name, w in wiring.items()}
    if args.reverse is not None:
        flipped, why = set(args.reverse), "--reverse"
    else:
        flipped = {int(w["terminal"]) for w in wiring.values() if w.get("reverse")}
        why = "config"
    address = args.address if args.address is not None else int(motors_cfg["bonnet_i2c_address"])
    terminals = resolve_terminals(args.targets, wiring)

    try:
        from adafruit_motorkit import MotorKit
    except ImportError:
        sys.exit("adafruit-circuitpython-motorkit is not installed. "
                 "Run this with the venv's Python: ~/carlink-venv/bin/python wheel_test.py ...")
    try:
        kit = MotorKit(address=address)
    except (ValueError, OSError) as exc:
        sys.exit(f"Could not reach the Bonnet at I2C address 0x{address:02x}: {exc}\n"
                 "Check that I2C is enabled and that 'i2cdetect -y 1' shows 60.")

    # Make "kill" behave like Ctrl+C, so the motors still get stopped.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    motors = [getattr(kit, f"motor{t}") for t in terminals]
    names = ", ".join(f"M{t}" for t in terminals)
    try:
        for terminal, motor in zip(terminals, motors):
            power = -args.power if terminal in flipped else args.power
            motor.throttle = power
            print(f"M{terminal} ({name_of.get(terminal, '?')}): power {power:+.2f}"
                  + (f" (reversed by {why})" if terminal in flipped else ""))
        print(f"running for {args.seconds:g} s (Ctrl+C to stop)")
        time.sleep(args.seconds)
    except KeyboardInterrupt:
        print("stopped early")
    finally:
        for motor in motors:
            motor.throttle = 0
        print(f"{names}: off")


if __name__ == "__main__":
    main()
