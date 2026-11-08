## Instructions for AV system on this laptop & R pi over the hotspot


* Step 1: Open 3 Konsole tabs

* Step 2: Start hotspot, and connect laptop to it

* Step 4: Power on Pi via the UPS HAT (E) power switch. It will take some time to boot

* Step 3: Start server on laptop

python3 server.py

* Step 4: In new terminal tab, run command to get our IP address

ip -4 addr

* Step 5: In new, new terminal tab, ssh into the pi

ssh <pi-user>@<pi-hostname>.local

* Step 6: In the pi session, cd into the AV directory

cd AV_Scripts/

* Step 7: In the pi session, edit the config and set the ip address to the laptop's (found in Step 4)

nano pi_config.json

* Step 8: In the pi session, start client       NOTE: May need to update this command to have access to adafruit dependencies?

python client.py --dry-run

