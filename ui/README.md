# Car UI

A web page to watch the car's camera, take pictures, record, and drive the
car by hand. `car_ui.py` runs on the laptop between the Pi and `server.py`;
no existing file is changed.

## How to run it

1. **Laptop:** start `server.py` as usual.
2. **Laptop, new tab:**
   ```bash
   cd ~/carlink/ui && python3 car_ui.py
   ```
3. **Pi:** in `pi_config.json`, set `"port": 5006` (and `host` = laptop IP,
   as always), then start `pi_client.py` as usual.
4. **Browser:** `http://localhost:8080` on the laptop, or
   `http://<laptop-ip>:8080` from a phone on the hotspot.

The order of steps 1–3 doesn't matter much, since everything reconnects by
itself. If the laptop's firewall is on, also allow ports 5006 and 8080.

No password setup is needed: `car_ui.py` reads it from
`server/server_config.json`. To stop using the UI, set the Pi's port back to
5005.
