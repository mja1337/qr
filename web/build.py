#!/usr/bin/env python3
"""Inline the MIT QR encoder into app.src.html and write ../index.html.

The built page is the GitHub Pages entry point (repository root).
"""
import os

here = os.path.dirname(os.path.abspath(__file__))
src = open(os.path.join(here, "app.src.html"), encoding="utf-8").read()
lib = open(os.path.join(here, "vendor", "qrcode.js"), encoding="utf-8").read()
lib = lib.replace("</script", "<\\/script")
if "/*__QRCODE_LIB__*/" not in src:
    raise SystemExit("app.src.html is missing the /*__QRCODE_LIB__*/ placeholder")
out = src.replace("/*__QRCODE_LIB__*/", lib)
if "Kazuhiko Arase" not in out:
    raise SystemExit("attribution for qrcode-generator was lost")
dest = os.path.normpath(os.path.join(here, "..", "index.html"))
with open(dest, "w", encoding="utf-8") as fh:
    fh.write(out)
print("wrote", dest, len(out.encode()), "bytes")
