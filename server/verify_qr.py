#!/usr/bin/env python3
"""Decode a QR PNG offline with OpenCV and compare to expected text.
Tries OpenCV's default detector, then its Aruco-based detector (the default one has
known misses on some versions, e.g. v10, even for unbranded codes).
Usage: python verify_qr.py code.png "https://example.com/"   Requires: opencv-python-headless"""
import sys, cv2
img = cv2.imread(sys.argv[1])
text = ""
for det in (cv2.QRCodeDetector(), cv2.QRCodeDetectorAruco()):
    text = det.detectAndDecode(img)[0]
    if text == sys.argv[2]:
        break
print("Decoded:", repr(text))
ok = text == sys.argv[2]
print("MATCH" if ok else "MISMATCH")
sys.exit(0 if ok else 1)
