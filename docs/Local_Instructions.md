## Instructions for AV system on this laptop & R pi over the hotspot

Assumes the repository is cloned as `carlink` in the home folder on both the
laptop and the Pi.

* Step 1: Open 3 Konsole tabs

* Step 2: Start hotspot, and connect laptop to it

* Step 3: Power on Pi via the UPS HAT (E) power switch. It will take some time to boot

* Step 4: Start server on laptop

```
cd ~/carlink/server
python3 server.py
```

* Step 5: In new terminal tab, run command to get our IP address

```
ip -4 addr
```

* Step 6: In new, new terminal tab, ssh into the pi

```
ssh <pi-user>@<pi-hostname>.local
```

* Step 7: In the pi session, cd into the Pi folder of the repo

```
cd ~/carlink/pi
```

* Step 8: In the pi session, edit the config and set `server.host` to the laptop's IP address (found in Step 5)

```
nano pi_config.json
```

* Step 9: In the pi session, start client. Use the venv's Python, which has the Adafruit motor library. `--dry-run` logs motor values instead of driving the motors; leave it off to drive.

```
~/carlink-venv/bin/python pi_client.py --dry-run
```
