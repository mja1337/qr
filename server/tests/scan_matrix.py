#!/usr/bin/env python3
"""Scannability matrix: decode generated codes with OpenCV across URL lengths, box sizes and EC levels.
PNG always; SVG too if cairosvg is installed (vector, rasterised at 400 px since its size is in mm); ZXing too if zxing-cpp is installed (both optional, test-only)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cv2, numpy as np
from app import make_qr
URLS = ["https://example.com",
        "https://mja1337.github.io/timechain/",
        "https://example.com/products/coffee/bean-to-cup/?utm_source=qr&utm_medium=print&utm_campaign=2026-launch",
        "https://example.com/" + "x" * 150 + "?ref=long-url-test&id=1234567890"]
BOXES, ECS = [3, 4, 6, 10], ["M", "H"]
try:
    import cairosvg
except ImportError:
    cairosvg = None
try:
    import zxingcpp
except ImportError:
    zxingcpp = None
DEC = {"cv": lambda i: cv2.QRCodeDetector().detectAndDecode(i)[0],
       "cv-aruco": lambda i: cv2.QRCodeDetectorAruco().detectAndDecode(i)[0]}
if zxingcpp:
    DEC["zxing"] = lambda i: next((r.text for r in zxingcpp.read_barcodes(i)), "")
res = {}
def run(kind, png, u):
    i = cv2.imdecode(np.frombuffer(png, np.uint8), 1)
    r = {k: f(i) == u for k, f in DEC.items()}
    for k, v in list(r.items()) + [("any", any(r.values()))]:
        t = res.setdefault((kind, k), [0, 0]); t[0] += v; t[1] += 1
    return " ".join(f"{k}={'OK' if v else 'x'}" for k, v in r.items())
for u in URLS:
    for b in BOXES:
        for ec in ECS:
            png, ver, _ = make_qr(u, "png", box=b, ec=ec)
            line = f"len={len(u):3d} v{ver:<2} box={b:<2} ec={ec} png {run('png', png, u)}"
            if cairosvg:
                svg, *_ = make_qr(u, "svg", box=b, ec=ec)
                line += " | svg " + run("svg", cairosvg.svg2png(bytestring=svg, background_color="white", output_width=400), u)
            print(line)
for (kind, k), (p, n) in res.items():
    print(f"{kind.upper():3} {k:9}: {p}/{n} = {100*p/n:.0f}%")
