# Traffic-sign recognition that runs locally: load the models once, then call predict() on every camera frame.
#
#   frame -> YOLO detector finds sign boxes -> crop each box -> 64x64 -> CNN -> class + confidence
#
# Files needed in this folder:  sign_cnn.py, sign_detector.pt, lisa_cnn.pt
# Install:  pip install torch torchvision ultralytics opencv-python
#
# Use in your code:
#     from sign_cnn import SignCNN
#     model = SignCNN()                       # slow, do it once at startup
#     name, confidence = model.predict(frame) # fast, call it on every frame (OpenCV image)
#
# Test from the terminal:
#     python sign_cnn.py photo.jpg            # one image
#     python sign_cnn.py                      # live webcam 0, prints every frame

import sys
import time
from pathlib import Path

import cv2
import torch
from PIL import Image
from torch import nn
from torchvision.transforms.functional import pil_to_tensor
from ultralytics import YOLO

HERE = Path(__file__).parent
DETECTOR_SIZE = 640      # the detector was trained on 640x640 frames
CROP_SIZE = 64           # the classifier input size
DETECTOR_THRESHOLD = 0.42  # boxes below this detector confidence are ignored


class TrafficCNN(nn.Module):
    """The classifier: [N, 3, 64, 64] in [0, 1] -> [N, num_classes] logits. Same as scripts/model.py."""

    def __init__(self, mean=(0.0, 0.0, 0.0), std=(1.0, 1.0, 1.0), num_classes=43):
        super().__init__()
        self.register_buffer("mean", torch.tensor(mean).view(1, 3, 1, 1))
        self.register_buffer("std", torch.tensor(std).view(1, 3, 1, 1))
        self.layers = nn.Sequential(
            nn.Conv2d(3, 32, kernel_size=3, padding=1), nn.ReLU(), nn.MaxPool2d(2),
            nn.Conv2d(32, 64, kernel_size=3, padding=1), nn.ReLU(), nn.MaxPool2d(2),
            nn.Conv2d(64, 128, kernel_size=3, padding=1), nn.ReLU(), nn.MaxPool2d(2),
            nn.Flatten(),
            nn.Linear(128 * 8 * 8, 256), nn.ReLU(),
            nn.Linear(256, num_classes),
        )

    def forward(self, x):
        return self.layers((x - self.mean) / self.std)


class SignCNN:
    def __init__(self):
        self.detector = YOLO(str(HERE / "sign_detector.pt"))
        saved = torch.load(HERE / "lisa_cnn.pt", map_location="cpu")
        self.names = saved["names"]
        self.classifier = TrafficCNN(num_classes=len(self.names))
        self.classifier.load_state_dict(saved["weights"])  # includes the normalization mean/std
        self.classifier.eval()

    def predict(self, frame):
        """frame: OpenCV image (BGR numpy array). Returns (class_name, confidence) of the most confident
        sign in this frame, or (None, 0.0) if there is no sign."""
        image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        W, H = image.size

        # 1. Detector. Stretch the frame to 640x640 like the training frames; scale boxes back afterwards.
        result = self.detector.predict(image.resize((DETECTOR_SIZE, DETECTOR_SIZE)), imgsz=DETECTOR_SIZE,
                                       conf=DETECTOR_THRESHOLD, verbose=False)[0]
        if len(result.boxes) == 0:
            return None, 0.0
        best = result.boxes.conf.argmax()
        left, top, right, bottom = result.boxes.xyxy[best].tolist()
        box = (left * W / DETECTOR_SIZE, top * H / DETECTOR_SIZE,
               right * W / DETECTOR_SIZE, bottom * H / DETECTOR_SIZE)

        # 2. Classifier on the crop, cut from the full-resolution frame.
        crop = image.crop(box).resize((CROP_SIZE, CROP_SIZE), Image.BILINEAR)
        x = pil_to_tensor(crop).float().unsqueeze(0) / 255  # [1, 3, 64, 64] in [0, 1]
        with torch.no_grad():
            probs = self.classifier(x).softmax(dim=1)[0]
        confidence, class_id = probs.max(dim=0)
        return self.names[class_id], confidence.item()


if __name__ == "__main__":
    model = SignCNN()
    if len(sys.argv) > 1:
        print(model.predict(cv2.imread(sys.argv[1])))
    else:
        camera = cv2.VideoCapture(0)
        while True:
            ok, frame = camera.read()
            if not ok:
                continue
            start = time.time()
            name, confidence = model.predict(frame)
            print(f"{name}  {confidence:.0%}  ({time.time() - start:.2f} s)")
