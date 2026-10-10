#!/usr/bin/env python3
"""Decode the PNGs/exports written by headless_test.cjs with OpenCV (+ZXing/cairosvg if installed)."""
import sys, os, json, base64
import cv2, numpy as np
OUT = sys.argv[1]
try: import zxingcpp
except ImportError: zxingcpp = None
try: import cairosvg
except ImportError: cairosvg = None
def dec(png):
    i = cv2.imdecode(np.frombuffer(png, np.uint8), 1)
    res = {"cv": cv2.QRCodeDetector().detectAndDecode(i)[0], "cv-aruco": cv2.QRCodeDetectorAruco().detectAndDecode(i)[0]}
    if zxingcpp: res["zxing"] = next((r.text for r in zxingcpp.read_barcodes(i)), "")
    return res
def img_bytes(fmt, raw):
    return cairosvg.svg2png(bytestring=raw, output_width=400) if fmt == "svg" else raw
p = f = 0
def check(name, raw, fmt, expect):
    global p, f
    if fmt == "svg" and not cairosvg: print(f"SKIP {name} (no cairosvg)"); return
    r = dec(img_bytes(fmt, raw)); good = all(v == expect for v in r.values())
    print(("PASS" if good else "FAIL"), name, {k: v == expect for k, v in r.items()}); p += good; f += not good
U1 = "https://mja1337.github.io/timechain/"
check("page PNG a1.png", open(f"{OUT}/a1.png", "rb").read(), "png", U1)
check("page SVG a2.svg", open(f"{OUT}/a2.svg", "rb").read(), "svg", "https://example.com/svg-test")
check("downloaded qr.png", open(f"{OUT}/qr.png", "rb").read(), "png", "https://example.com/products/coffee/bean-to-cup/?utm_source=qr&utm_medium=print&utm_campaign=2026-launch&x=" + "y" * 60)
check("page PNG b1.png (window.storage run)", open(f"{OUT}/b1.png", "rb").read(), "png", U1)
for fn in ("export.json", "b-export.json"):
    doc = json.load(open(f"{OUT}/{fn}"))
    for c in doc["codes"]:
        check(f"{fn} image_base64 {c['id']} ({c['format']}, v{c['version']}, ec {c['options']['ec']})", base64.b64decode(c["image_base64"]), c["format"], c["data"])
print(f"DECODE RESULT: {p} passed, {f} failed")
sys.exit(1 if f else 0)
