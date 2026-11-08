#!/usr/bin/env python3
"""Manual test: spin each wheel in turn at full power, then stop.

Uses the wiring and reverse flags from pi/pi_config.json, so every wheel
should turn FORWARD. Lift the wheels off the ground first.

    cd ~/carlink/pi
    ~/carlink-venv/bin/python tests/wheels_full_speed.py
    ~/carlink-venv/bin/python tests/wheels_full_speed.py --seconds 3

"Full power" means a throttle of 1.0 on the Bonnet (full battery voltage).
It deliberately ignores max_throttle, which caps normal driving. Ctrl+C
stops the motors immediately.
"""

import argparse
import signal
import sys
import time
from pathlib import Path

PI_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PI_DIR))
from wheel_test import P, load_motor_config  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Spin each wheel in turn at full power.")
    parser.add_argument("--seconds", type=float, default=2.0, help="per wheel; default 2")
    parser.add_argument("--config", default=str(PI_DIR / "pi_config.json"))
    args = parser.parse_args()

    motors_cfg = load_motor_config(args.config, use_config=True)
    wiring = motors_cfg["wiring"]
    address = int(motors_cfg["bonnet_i2c_address"])

    try:
        from adafruit_motorkit import MotorKit
    except ImportError:
        sys.exit("adafruit-circuitpython-motorkit is not installed. Run this with the "
                 "venv's Python: ~/carlink-venv/bin/python tests/wheels_full_speed.py")
    try:
        kit = MotorKit(address=address)
    except (ValueError, OSError) as exc:
        sys.exit(f"Could not reach the Bonnet at I2C address 0x{address:02x}: {exc}")

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    all_motors = [getattr(kit, f"motor{t}") for t in (1, 2, 3, 4)]

    print("Wheels off the ground! Starting in 3 s (Ctrl+C to cancel).")
    try:
        time.sleep(3)
        for name in P.MOTOR_NAMES:
            terminal = int(wiring[name]["terminal"])
            reverse = bool(wiring[name].get("reverse"))
            throttle = -1.0 if reverse else 1.0
            print(f"{name:12s} M{terminal}: 100% power forward"
                  + (" (terminal reversed in config)" if reverse else ""), flush=True)
            motor = getattr(kit, f"motor{terminal}")
            motor.throttle = throttle
            time.sleep(args.seconds)
            motor.throttle = 0
            time.sleep(0.5)
        print("Done. Each wheel should have turned forward, in the order above.")
    except KeyboardInterrupt:
        print("stopped early")
    finally:
        for motor in all_motors:
            motor.throttle = 0


if __name__ == "__main__":
    main()
