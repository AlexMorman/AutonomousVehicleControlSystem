# Changelog

## 2026-11-07: Repository created

Merged from two folders, both called `AV_Scripts`: one from the Pi and one
from the laptop. Each entry below is one git commit, oldest first, so
`git log` shows the same steps. Code behavior was not changed in any commit;
the only code edits are the import-path lines and message text noted under
"Restructure".

### Baseline: code exactly as it ran on 2026-11-06

Files were taken as they were, with these choices:

| File | Taken from | Why |
|---|---|---|
| `pi_client.py` | Pi | The laptop's copy is from 10/27 and has `safety_timeout_s` 1.0 in its defaults. The Pi's copy (11/6) has 10.0 and is the one that ran. |
| `pi_config.json` → `pi_config.example.json` | Pi | The laptop's copy is the original template (placeholder host and password, no reverse flags). |
| `protocol.py`, `wheel_test.py` | Pi | Identical on both machines. |
| `server.py`, `hookup.py`, `sign_decider.py`, `cnn_api_client.py` | Laptop | Only exist there. `cnn_api_client.py` is a teammate's file, included unmodified. |
| `server_config.json` → `server_config.example.json` | Laptop | |
| `README.md`, `Local_Instructions.md` | Laptop | Only exist there. The README is the original version from 10/27. |

- **Password removed.** In both example configs, the real password was
  replaced with the placeholder `change-me`. Nothing else in the configs
  changed: the host, timeouts, wiring and decider are as they ran.
- **Real configs ignored.** `.gitignore` excludes `pi_config.json` and
  `server_config.json`, so the password can't be committed by accident. It
  also excludes `recordings/`, `camera_test.*`, `__pycache__/` and venv
  folders.
- **Not included:**
  - The laptop's outdated `pi_client.py` and `pi_config.json` (see table).
  - Both `__pycache__/` folders (compiled files).
  - `recordings/`, `recordings/all recordings/` and an image archive: all
    runtime output (frames and logs from test drives).
- **Not found on either machine:** `pattern_decider.py` and the newer README,
  both of which the original design notes describe.

### Restructure into pi/, server/ and shared/

- Moved:
  - `pi_client.py`, `wheel_test.py`, `pi_config.example.json` → `pi/`
  - `server.py`, `hookup.py`, `sign_decider.py`, `cnn_api_client.py`,
    `server_config.example.json` → `server/`
  - `protocol.py` → `shared/`, the single copy for both sides
  - `README.md` → `docs/README_original.md`, content unchanged
  - `Local_Instructions.md` → `docs/Local_Instructions.md`, content
    unchanged in this commit
- `pi/pi_client.py`: added one line before `import protocol` that puts
  `../shared` on the import path.
- `server/server.py`: added `import sys` and the same line before
  `import hookup`. `hookup.py` and `sign_decider.py` are unchanged, since they
  are imported through `server.py`.
- `shared/protocol.py`: the docstring and two version-mismatch error messages
  said to copy `protocol.py` to both machines by hand. They now say to keep
  both machines on the same repo version.
- Config files are still looked for next to each script, so they now go in
  `pi/` and `server/`.
- Verified before and after with a full loop on one computer (test-pattern
  camera, dry-run motors, dummy decider at 0.3, recording on). The baseline
  recorded 72 frames and the restructured code 73 in about 5 s, with the
  motors receiving commands and stopping on disconnect. Also checked that
  `sign_decider:decide` still imports through `hookup.configure`, without
  calling the CNN.

### Fix Local_Instructions for the new layout

`docs/Local_Instructions.md`:
- Renumbered the steps 1–9. The original had two Step 4s, and Step 3
  came after one of them. The order of the steps is unchanged.
- Server step: `cd ~/carlink/server` before `python3 server.py`.
- Pi step: `cd ~/carlink/pi` instead of `cd AV_Scripts/`.
- Client command: `~/carlink-venv/bin/python pi_client.py --dry-run` instead
  of `python client.py --dry-run`. The script is named `pi_client.py`, and
  the venv's Python is what has the Adafruit library, which answers the
  original note.
- Added a line saying it assumes the repo is cloned as `~/carlink`, and code
  formatting for the commands.

### Add requirements.txt for the Pi and the server

- `pi/requirements.txt`: `adafruit-circuitpython-motorkit`, with a note
  that OpenCV comes from apt.
- `server/requirements.txt`: `requests`, `numpy`, `opencv-python-headless`,
  with the apt alternative noted.
- Versions are unpinned because the installed versions are unknown.

### Add known issues list

- `docs/KNOWN_ISSUES.md`: 17 items found while checking the code and
  recordings, including CNN latency and reconnect loops, timeouts that don't
  line up, the 10 s safety timeout vs the docs, unmapped CNN classes, false
  positives, and the local-CNN plan. Nothing was fixed.

### Add new README for the repository

- `README.md`: new. Covers the layout, setup from a git clone, creating
  configs from the templates, running, bring-up tests, testing with no
  hardware, current configuration values and hardware. The original stays at
  `docs/README_original.md`.

### Add this changelog

- `CHANGELOG.md`: this file.
