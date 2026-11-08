import sys

import cv2
import requests

SERVER = "https://<cnn-api-host>/detect"


def classify(frame):
    """frame: an OpenCV image (e.g. from cv2.VideoCapture().read()).
    Returns (class_name, confidence) of the most confident sign in this frame, or (None, 0.0) if no sign."""
    ok, jpeg = cv2.imencode(".jpg", frame)
    response = requests.post(SERVER, files={"image": ("frame.jpg", jpeg.tobytes(), "image/jpeg")}, timeout=5)
    signs = response.json()["signs"]  # this frame only, most confident first
    if not signs:
        return None, 0.0
    return signs[0]["class"], signs[0]["confidence"]


if __name__ == "__main__":
    print(classify(cv2.imread(sys.argv[1])))
