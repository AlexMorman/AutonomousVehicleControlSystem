"""TEMPORARY: client for the remote sign-detection CNN API.

Used by sign_decider.py until the real image pipeline plugs into hookup.py.

The API's URL is not kept in the repository. Set it in server_config.json
under decider.options.cnn_api_url, or in the CNN_API_URL environment variable
(which wins).

Command line, to classify one image file:
    python3 cnn_api_client.py IMAGE [--url URL]
Without --url it uses CNN_API_URL, then server_config.json next to this file.
"""

import json
import os
import sys
from pathlib import Path

import cv2
import requests

URL_ENV_VAR = "CNN_API_URL"
_url = ""


def configure(url):
    """Set the API URL. The CNN_API_URL environment variable overrides it."""
    global _url
    _url = os.environ.get(URL_ENV_VAR) or url or ""
    if not _url:
        raise SystemExit("CNN API URL not set: put it in server_config.json under "
                         f"decider.options.cnn_api_url, or set {URL_ENV_VAR}")


def classify(frame):
    """frame: an OpenCV image (e.g. from cv2.VideoCapture().read()).
    Returns (class_name, confidence) of the most confident sign in this frame, or (None, 0.0) if no sign."""
    if not _url:
        raise RuntimeError("cnn_api_client.configure() has not been called")
    ok, jpeg = cv2.imencode(".jpg", frame)
    response = requests.post(_url, files={"image": ("frame.jpg", jpeg.tobytes(), "image/jpeg")}, timeout=5)
    signs = response.json()["signs"]  # this frame only, most confident first
    if not signs:
        return None, 0.0
    return signs[0]["class"], signs[0]["confidence"]


def _url_from_server_config():
    path = Path(__file__).with_name("server_config.json")
    try:
        return json.loads(path.read_text())["decider"]["options"]["cnn_api_url"]
    except (OSError, ValueError, KeyError, TypeError):
        return ""


if __name__ == "__main__":
    args = sys.argv[1:]
    url = ""
    if "--url" in args:
        i = args.index("--url")
        url = args[i + 1]
        del args[i:i + 2]
    if len(args) != 1:
        sys.exit(__doc__)
    configure(url or _url_from_server_config())
    print(classify(cv2.imread(args[0])))
