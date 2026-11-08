#!/usr/bin/env python3
"""Spin one or more motors on the Bonnet for a few seconds, then stop.

Standalone: needs only the Adafruit motor library, not the rest of the car link.

    python3 wheel_test.py 1                 # motor on terminal M1, half power, 5 s
    python3 wheel_test.py 1 2 3 4           # all four at once
    python3 wheel_test.py 3 --power 0.3     # gentler
    python3 wheel_test.py 2 --power -0.5    # backwards
    python3 wheel_test.py 1 --seconds 2
    python3 wheel_test.py 1 2 3 4 --reverse 2 3   # M2 and M3 run the opposite way

Lift the wheels off the ground first. Ctrl+C stops the motors immediately.
--power is the fraction of battery voltage: 0.5 on 4 x AA is about 3 V.
"""

import argparse
import signal
import sys
import time


def main() -> None:
    parser = argparse.ArgumentParser(description="Spin Bonnet motors briefly, then stop.")
    parser.add_argument("terminals", nargs="+", type=int, choices=[1, 2, 3, 4],
                        metavar="TERMINAL", help="Bonnet motor terminal(s): 1, 2, 3 and/or 4")
    parser.add_argument("--power", type=float, default=0.5,
                        help="-1.0 (full reverse) to 1.0 (full forward); default 0.5")
    parser.add_argument("--seconds", type=float, default=5.0, help="how long to run; default 5")
    parser.add_argument("--reverse", nargs="+", type=int, choices=[1, 2, 3, 4], default=[],
                        metavar="TERMINAL", help="terminal(s) whose direction to flip")
    parser.add_argument("--address", type=lambda s: int(s, 0), default=0x60,
                        help="Bonnet I2C address; default 0x60")
    args = parser.parse_args()
    if not -1.0 <= args.power <= 1.0:
        parser.error("--power must be between -1.0 and 1.0")

    try:
        from adafruit_motorkit import MotorKit
    except ImportError:
        sys.exit("adafruit-circuitpython-motorkit is not installed. "
                 "Run this with the venv's Python: ~/carlink-venv/bin/python wheel_test.py ...")
    try:
        kit = MotorKit(address=args.address)
    except (ValueError, OSError) as exc:
        sys.exit(f"Could not reach the Bonnet at I2C address 0x{args.address:02x}: {exc}\n"
                 "Check that I2C is enabled and that 'i2cdetect -y 1' shows 60.")

    # Make "kill" behave like Ctrl+C, so the motors still get stopped.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    terminals = sorted(set(args.terminals))
    motors = [getattr(kit, f"motor{t}") for t in terminals]
    names = ", ".join(f"M{t}" for t in terminals)
    flipped = set(args.reverse)
    try:
        for terminal, motor in zip(terminals, motors):
            power = -args.power if terminal in flipped else args.power
            motor.throttle = power
            print(f"M{terminal}: power {power:+.2f}" + (" (reversed)" if terminal in flipped else ""))
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
