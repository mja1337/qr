#!/usr/bin/env python3
"""Offline QR code service + CLI (reusable template). Plain black-on-white codes.
No network calls, no external services, no CDNs.

  python app.py serve                          # HTTP UI + API (HOST/PORT, default 0.0.0.0:8080)
  python app.py gen "https://x" -o x.png       # one-off file (PNG, or SVG by extension)
  python app.py export -o codes.json [--images]   # dump the local DB to JSON

Env: QR_DB_ENABLED (default true), QR_DB_PATH (default ./data/qrcodes.db), HOST, PORT, QR_MAX_DATA.
"""
import argparse, base64, html, io, json, os, re, sys, uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import qrcode
import qrcode.image.svg
from qrcode.constants import ERROR_CORRECT_L, ERROR_CORRECT_M, ERROR_CORRECT_Q, ERROR_CORRECT_H

EC = {"L": ERROR_CORRECT_L, "M": ERROR_CORRECT_M, "Q": ERROR_CORRECT_Q, "H": ERROR_CORRECT_H}
MIME = {"png": "image/png", "svg": "image/svg+xml"}
MAX_DATA = int(os.getenv("QR_MAX_DATA", "2048"))


def env_bool(name, default):
    v = os.getenv(name)
    if v is None or v.strip() == "":
        return default
    return v.strip().lower() not in ("0", "false", "no", "off")


def truthy(v):
    return str(v).lower() in ("1", "true", "yes", "on")


# ---------------- content payloads (WiFi / vCard) ----------------
_WIFI_ESCAPE = re.compile(r'([\\;,:"])')


def escape_wifi(s):
    return _WIFI_ESCAPE.sub(r"\\\1", str(s))


def build_wifi(ssid, password="", auth="WPA", hidden=False):
    s = (ssid or "").strip()
    if not s:
        raise ValueError("Enter a WiFi network name (SSID)")
    t = auth if auth in ("WPA", "WEP", "nopass") else "WPA"
    p = "" if t == "nopass" else (password or "")
    h = "true" if hidden else "false"
    return f"WIFI:T:{t};S:{escape_wifi(s)};P:{escape_wifi(p)};H:{h};;"


def escape_vcard(s):
    return str(s).replace("\\", "\\\\").replace("\n", "\\n").replace(",", "\\,").replace(";", "\\;")


def build_vcard(name="", phone="", email=""):
    fn = (name or "").strip()
    tel = (phone or "").strip()
    mail = (email or "").strip()
    if not fn and not tel and not mail:
        raise ValueError("Enter a name, phone, or email for the contact")
    lines = ["BEGIN:VCARD", "VERSION:3.0"]
    if fn:
        lines.append("FN:" + escape_vcard(fn))
    if tel:
        lines.append("TEL:" + escape_vcard(tel))
    if mail:
        lines.append("EMAIL:" + escape_vcard(mail))
    lines.append("END:VCARD")
    return "\n".join(lines)


def payload_from_params(p):
    """Assemble QR payload from form/API params. kind=wifi|contact builds structured data; else url/data/text."""
    kind = (p.get("kind") or "text").strip().lower()
    if kind == "wifi":
        auth_raw = (p.get("auth") or "WPA").strip()
        auth = "nopass" if auth_raw.lower() == "nopass" else auth_raw.upper()
        return build_wifi(
            p.get("ssid", ""),
            password=p.get("password", ""),
            auth=auth,
            hidden=truthy(p.get("hidden", "")),
        )
    if kind == "contact":
        return build_vcard(name=p.get("name", ""), phone=p.get("phone", ""), email=p.get("email", ""))
    return p.get("url") or p.get("data") or p.get("text")


# ---------------- generator (same logic as qrgen/make_qr.py) ----------------
def make_qr(data, fmt="png", box=10, border=4, ec="M"):
    """Return (bytes, qr_version, options). Black modules on white."""
    if fmt not in MIME:
        raise ValueError("format must be png or svg")
    if ec not in EC:
        raise ValueError("ec must be L, M, Q or H")
    if not (1 <= box <= 50) or not (4 <= border <= 20):
        raise ValueError("box must be 1-50, border 4-20 (quiet zone kept >= 4 modules)")
    if not data or len(data) > MAX_DATA:
        raise ValueError(f"data must be 1-{MAX_DATA} chars")
    qr = qrcode.QRCode(error_correction=EC[ec], box_size=box, border=border)
    qr.add_data(data)
    qr.make(fit=True)
    if fmt == "svg":
        img = qr.make_image(image_factory=qrcode.image.svg.SvgPathImage)
    else:
        img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue(), qr.version, {"box": box, "border": border, "ec": ec}


# ---------------- local SQLite store (imported only when enabled) ----------------
def db_enabled():
    return env_bool("QR_DB_ENABLED", True)


def db_path():
    return os.getenv("QR_DB_PATH", os.path.join(".", "data", "qrcodes.db"))


def read_favicon(name):
    """Bytes for the shared favicon. Beside this file in Docker, or the repo root locally."""
    if name not in ("favicon.svg", "favicon.png"):
        return None
    here = os.path.dirname(os.path.abspath(__file__))
    for base in (here, os.path.abspath(os.path.join(here, ".."))):
        fp = os.path.join(base, name)
        if os.path.isfile(fp):
            with open(fp, "rb") as fh:
                return fh.read()
    return None


class Store:
    def __init__(self, path):
        import sqlite3  # lazy: nothing touched when the DB is disabled
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        self.path, self.sqlite3 = path, sqlite3
        with self._conn() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS codes (
                id TEXT PRIMARY KEY, data TEXT NOT NULL, format TEXT NOT NULL,
                options TEXT NOT NULL, version INTEGER, created_at TEXT NOT NULL,
                image BLOB NOT NULL)""")

    def _conn(self):
        return self.sqlite3.connect(self.path)

    def add(self, data, fmt, options, version, image):
        cid = uuid.uuid4().hex[:12]
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._conn() as c:
            c.execute("INSERT INTO codes VALUES (?,?,?,?,?,?,?)",
                      (cid, data, fmt, json.dumps(options), version, ts, image))
        return cid

    def list(self, include_images=False, limit=None):
        q = "SELECT id,data,format,options,version,created_at,image FROM codes ORDER BY created_at, rowid"
        args = ()
        if limit:
            q += " LIMIT ?"
            args = (int(limit),)
        out = []
        with self._conn() as c:
            for r in c.execute(q, args):
                row = {"id": r[0], "data": r[1], "format": r[2], "options": json.loads(r[3]),
                       "version": r[4], "created_at": r[5], "bytes": len(r[6])}
                if include_images:
                    row["image_base64"] = base64.b64encode(r[6]).decode()
                out.append(row)
        return out

    def get(self, cid):
        with self._conn() as c:
            return c.execute("SELECT format,image FROM codes WHERE id=?", (cid,)).fetchone()


def export_doc(store, include_images):
    codes = store.list(include_images=include_images)
    return {"exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "count": len(codes), "include_images": include_images, "codes": codes}


# ---------------- HTML ----------------
PAGE_CSS = """
:root{--bg:#f4f0ea;--ink:#241f33;--muted:#534e60;--card:#fffdfb;--line:#e6dcea;--accent:#5c4a84;--soft:#f3eaf8;--soft-ink:#3d2d58;--focus:#6d4ea3}
*{box-sizing:border-box}
html{color-scheme:light}
body{margin:0;min-height:100vh;color:var(--ink);font-family:system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;font-size:16px;line-height:1.5;
background:radial-gradient(900px 420px at -10% -10%,#efe4f7 0%,transparent 55%),radial-gradient(800px 380px at 110% -8%,#e5f3ea 0%,transparent 50%),var(--bg)}
.wrap{max-width:880px;margin:0 auto;padding:1.35rem 1.1rem 3rem}
.brand{display:flex;align-items:center;gap:.9rem}
.logo{width:56px;height:56px;flex:0 0 auto}
.eyebrow{display:inline-block;margin:0 0 .15rem;padding:.18rem .7rem;border-radius:999px;background:var(--soft);color:var(--soft-ink);font-size:.75rem;font-weight:700;letter-spacing:.06em;text-transform:uppercase}
h1{font-size:1.85rem;line-height:1.15;margin:0;letter-spacing:-.02em}
h2{font-size:1.15rem;margin:0 0 .75rem}
.lede,.muted,.fine{color:var(--muted)}
.lede{margin:.85rem 0 0;max-width:62ch}
.fine{font-size:.85rem}
.card{background:var(--card);border:1px solid #efe7f4;border-radius:28px;padding:1.3rem 1.35rem 1.4rem;margin:1rem 0;box-shadow:0 1px 1px rgba(70,50,90,.04),0 12px 32px rgba(70,50,90,.08)}
label,.field{font-weight:650;color:var(--soft-ink);font-size:.95rem}
.field{display:flex;flex-direction:column;gap:.4rem;min-width:0}
.stack{display:flex;flex-direction:column;gap:.4rem;margin-bottom:.85rem}
input[type=text],input[type=password],input[type=tel],input[type=email],select{font:inherit;color:var(--ink);background-color:#fff;border:1.5px solid var(--line);border-radius:999px;padding:.85rem 1.15rem;width:100%;box-shadow:0 8px 18px rgba(86,64,120,.08),inset 0 1px 0 #fff}
select{appearance:none;background-image:url("data:image/svg+xml;charset=utf-8,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='8' viewBox='0 0 12 8'%3E%3Cpath fill='%235c4a84' d='M1.2.8 6 5.6 10.8.8 12 2 6 8 0 2z'/%3E%3C/svg%3E");background-repeat:no-repeat;background-position:right 1rem center;padding-right:2.5rem}
input[type=checkbox]{width:1.15rem;height:1.15rem;accent-color:var(--accent);margin:0}
.fields{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:.75rem;margin:0 0 .95rem}
.fields-2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:.75rem;margin:0 0 .85rem}
.panel[hidden]{display:none!important}
.row{display:flex;flex-wrap:wrap;align-items:center;gap:.55rem .65rem;margin-bottom:.35rem}
.check{display:inline-flex;align-items:center;gap:.5rem;font-weight:650}
button{font:inherit;font-weight:650;color:#fff;background:linear-gradient(180deg,#6a5894 0%,#544278 100%);border:1px solid #4a386e;border-radius:999px;padding:.72rem 1.15rem;cursor:pointer;box-shadow:0 10px 18px rgba(72,48,112,.22),inset 0 1px 0 rgba(255,255,255,.28)}
:focus-visible{outline:3px solid var(--focus);outline-offset:3px}
input:focus-visible,select:focus-visible{border-color:var(--accent)}
.links,.navline{display:flex;flex-wrap:wrap;gap:.55rem .65rem;align-items:center}
a.pill{display:inline-flex;align-items:center;text-decoration:none;color:var(--soft-ink);background:var(--soft);border-radius:999px;padding:.5rem .95rem;font-weight:650;box-shadow:0 6px 14px rgba(80,55,120,.08)}
a.pill:hover{text-decoration:underline;text-underline-offset:.15em}
.table-wrap{overflow-x:auto;border-radius:18px}
table{width:100%;border-collapse:collapse;font-size:.92rem}
th{text-align:left;font-size:.75rem;letter-spacing:.04em;text-transform:uppercase;color:var(--muted)}
td,th{padding:.6rem .45rem;border-bottom:1px solid #f0e8f4;vertical-align:middle;word-break:break-word}
td img{width:64px;height:64px;background:#fff;border-radius:6px;box-shadow:0 4px 10px rgba(70,50,90,.08);image-rendering:pixelated}
@media(max-width:700px){.wrap{padding:.85rem .75rem 2.4rem}h1{font-size:1.6rem}.card{padding:1.05rem .95rem;border-radius:22px}.fields,.fields-2{grid-template-columns:1fr}button{width:100%}.logo{width:48px;height:48px}}
@media(forced-colors:active){button,input,select,.card,a.pill{border:1px solid ButtonText}}
"""

MARK = """<svg class="logo" viewBox="0 0 32 32" aria-hidden="true"><rect width="32" height="32" rx="9" fill="#5c4a84"/>
<g fill="#ffffff"><rect x="4.5" y="4.5" width="10" height="10" rx="2"/><rect x="17.5" y="4.5" width="10" height="10" rx="2"/><rect x="4.5" y="17.5" width="10" height="10" rx="2"/></g>
<g fill="#5c4a84"><rect x="7" y="7" width="5" height="5" rx="1"/><rect x="20" y="7" width="5" height="5" rx="1"/><rect x="7" y="20" width="5" height="5" rx="1"/></g>
<g fill="#f6f1e6"><rect x="18" y="18" width="3.4" height="3.4" rx="0.7"/><rect x="23.1" y="18" width="3.4" height="3.4" rx="0.7"/><rect x="18" y="23.1" width="3.4" height="3.4" rx="0.7"/><rect x="23.1" y="23.1" width="3.4" height="3.4" rx="0.7"/></g></svg>"""


def page(title, body):
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="description" content="Offline QR code generator. No network calls and no tracking.">'
        '<meta name="theme-color" content="#f4f0ea">'
        '<link rel="icon" href="/favicon.svg" type="image/svg+xml">'
        '<link rel="icon" href="/favicon.png" type="image/png" sizes="128x128">'
        f'<title>{html.escape(title)}</title><style>{PAGE_CSS}</style></head>'
        f'<body><div class="wrap">{body}</div></body></html>'
    )


def index_html(db_on):
    if db_on:
        extra = ('<a class="pill" href="/codes">Saved codes</a>'
                 '<a class="pill" href="/export.json">Export JSON</a>')
        note = "<p class=\"muted\">Saving is on. Codes stay in a local SQLite file on this machine.</p>"
    else:
        extra = ""
        note = "<p class=\"muted\">Saving is off. Nothing is written to disk.</p>"
    return page("QR code generator", f"""<header class="hero"><div class="brand">{MARK}<div>
<p class="eyebrow">Offline · no tracking</p><h1>QR code generator</h1></div></div>
<p class="lede">Runs on this machine and makes no network calls. Generate opens the PNG or SVG. Use the browser back button to return here.</p></header>
<section class="card"><h2>Make a code</h2>
<form id="qr-form" action="/qr" method="get">
<div class="stack"><label for="kind">Content type</label>
<select id="kind" name="kind">
<option value="text" selected>Text / URL</option>
<option value="wifi">WiFi</option>
<option value="contact">Contact</option>
</select></div>
<div id="panel-text" class="panel">
<div class="stack"><label for="url">URL or text</label>
<input id="url" type="text" name="url" placeholder="https://example.com/" autocomplete="off" spellcheck="false"></div>
</div>
<div id="panel-wifi" class="panel" hidden>
<div class="stack"><label for="ssid">Network name (SSID)</label>
<input id="ssid" type="text" name="ssid" placeholder="MyNetwork" autocomplete="off" spellcheck="false"></div>
<div class="fields-2">
<label class="field">Security <select id="auth" name="auth"><option value="WPA" selected>WPA / WPA2</option><option value="WEP">WEP</option><option value="nopass">None</option></select></label>
<label class="field" for="password">Password <input id="password" type="password" name="password" placeholder="Optional if open" autocomplete="off" spellcheck="false"></label>
</div>
<div class="row"><label class="check"><input type="checkbox" name="hidden" value="1" id="hidden"> Hidden network</label></div>
</div>
<div id="panel-contact" class="panel" hidden>
<div class="stack"><label for="name">Name</label>
<input id="name" type="text" name="name" placeholder="Jane Doe" autocomplete="name" spellcheck="false"></div>
<div class="fields-2">
<label class="field" for="phone">Phone <input id="phone" type="tel" name="phone" placeholder="+1 555 123 4567" autocomplete="tel"></label>
<label class="field" for="email">Email <input id="email" type="email" name="email" placeholder="jane@example.com" autocomplete="email" spellcheck="false"></label>
</div>
<p class="muted">At least one of name, phone, or email is required. Phones can save the contact from the QR.</p>
</div>
<div class="fields">
<label class="field">Format <select name="format"><option value="png">PNG</option><option value="svg">SVG</option></select></label>
<label class="field">Size <select name="box"><option value="6">6</option><option value="10" selected>10</option><option value="16">16</option></select></label>
<label class="field">Error correction <select name="ec"><option value="L">L</option><option value="M" selected>M</option><option value="Q">Q</option><option value="H">H</option></select></label>
</div>
<button type="submit">Generate</button></form>{note}</section>
<nav class="links">{extra}<a class="pill" href="/health">Health</a></nav>
<p class="fine">"QR Code" is a registered trademark of DENSO WAVE INCORPORATED.</p>
<script>
(function(){{
  var kind=document.getElementById("kind");
  function sync(){{
    var k=kind.value;
    document.getElementById("panel-text").hidden=k!=="text";
    document.getElementById("panel-wifi").hidden=k!=="wifi";
    document.getElementById("panel-contact").hidden=k!=="contact";
  }}
  kind.addEventListener("change",sync); sync();
}})();
</script>""")


def codes_html(rows):
    tr = "".join(
        f"<tr><td><a href='/codes/{html.escape(r['id'])}'><img src='/codes/{html.escape(r['id'])}' width='64' height='64' alt=''></a></td>"
        f"<td>{html.escape(r['data'])}</td><td>{html.escape(r['format'])}</td>"
        f"<td>{html.escape(json.dumps(r['options']))}</td><td>{html.escape(r['created_at'])}</td></tr>"
        for r in rows)
    return page("Saved codes", f"""<p class="navline"><a class="pill" href="/">Back</a></p>
<h1>Saved codes ({len(rows)})</h1>
<nav class="links"><a class="pill" href="/export.json">Export JSON</a>
<a class="pill" href="/export.json?images=1">Export JSON with images</a></nav>
<div class="table-wrap"><table><thead><tr><th></th><th>Data</th><th>Format</th><th>Options</th><th>Created (UTC)</th></tr></thead>
<tbody>{tr}</tbody></table></div>""")


# ---------------- HTTP ----------------
class Handler(BaseHTTPRequestHandler):
    store = None
    server_version = "qrgen-template/1.3"

    def _send(self, code, body, ctype="application/json", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, indent=2).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _params(self):
        u = urlparse(self.path)
        p = {k: v[0] for k, v in parse_qs(u.query).items()}
        if self.command == "POST":
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            if raw and "json" in self.headers.get("Content-Type", ""):
                p.update({k: str(v) for k, v in json.loads(raw).items()})
            elif raw:
                p.update({k: v[0] for k, v in parse_qs(raw.decode()).items()})
        return u.path.rstrip("/") or "/", p

    def _wants_html(self, p):
        return p.get("view") == "html" or "text/html" in self.headers.get("Accept", "")

    def _disabled(self):
        self._send(404, {"error": "database disabled (QR_DB_ENABLED=false)"})

    def _qr(self, p):
        fmt = p.get("format", "png").lower()
        try:
            data = payload_from_params(p)
            img, ver, opts = make_qr(data, fmt, box=int(p.get("box", 10)), border=int(p.get("border", 4)),
                                     ec=p.get("ec", "M").upper())
        except (ValueError, TypeError) as e:
            return self._send(400, {"error": str(e)})
        extra = {"X-QR-Version": str(ver), "X-QR-EC": opts["ec"]}
        if self.store and not truthy(p.get("nostore", "")):
            extra["X-QR-Id"] = self.store.add(data, fmt, opts, ver, img)
        self._send(200, img, MIME[fmt], extra)

    def do_POST(self):
        path, p = self._params()
        if path == "/qr":
            return self._qr(p)
        self._send(404, {"error": "not found"})

    def do_GET(self):
        path, p = self._params()
        if path in ("/health", "/healthz"):
            return self._send(200, {"status": "ok", "db": "sqlite" if self.store else "disabled"})
        if path == "/favicon.svg":
            body = read_favicon("favicon.svg")
            if body is None:
                return self._send(404, {"error": "not found"})
            return self._send(200, body, "image/svg+xml")
        if path == "/favicon.png":
            body = read_favicon("favicon.png")
            if body is None:
                return self._send(404, {"error": "not found"})
            return self._send(200, body, "image/png")
        if path == "/":
            return self._send(200, index_html(bool(self.store)), "text/html; charset=utf-8")
        if path == "/qr":
            return self._qr(p)
        if path in ("/codes", "/codes.json"):
            if not self.store:
                return self._disabled()
            rows = self.store.list(limit=p.get("limit"))
            if path == "/codes" and self._wants_html(p):
                return self._send(200, codes_html(rows), "text/html; charset=utf-8")
            return self._send(200, rows)
        if path.startswith("/codes/"):
            if not self.store:
                return self._disabled()
            row = self.store.get(path.split("/", 2)[2])
            if not row:
                return self._send(404, {"error": "no such code"})
            return self._send(200, row[1], MIME[row[0]])
        if path == "/export.json":
            if not self.store:
                return self._disabled()
            return self._send(200, export_doc(self.store, truthy(p.get("images", ""))),
                              extra={"Content-Disposition": 'inline; filename="codes.json"'})
        self._send(404, {"error": "not found"})


def main(argv=None):
    ap = argparse.ArgumentParser(description="Offline QR code service/CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--host", default=os.getenv("HOST", "0.0.0.0"))
    s.add_argument("--port", type=int, default=int(os.getenv("PORT", "8080")))
    g = sub.add_parser("gen")
    g.add_argument("data")
    g.add_argument("-o", "--out", default="qr.png")
    g.add_argument("--box", type=int, default=10)
    g.add_argument("--border", type=int, default=4)
    g.add_argument("--ec", default="M", choices=EC.keys())
    g.add_argument("--no-store", action="store_true", help="don't record in the local DB")
    e = sub.add_parser("export")
    e.add_argument("-o", "--out", default="-", help="output file (default stdout)")
    e.add_argument("--images", action="store_true", help="include image_base64")
    a = ap.parse_args(argv)

    if a.cmd == "serve":
        Handler.store = Store(db_path()) if db_enabled() else None
        print(f"qrgen-template on http://{a.host}:{a.port}  db={db_path() if Handler.store else 'disabled'}",
              flush=True)
        ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()
    elif a.cmd == "gen":
        fmt = "svg" if a.out.lower().endswith(".svg") else "png"
        img, ver, opts = make_qr(a.data, fmt, a.box, a.border, a.ec)
        with open(a.out, "wb") as f:
            f.write(img)
        cid = Store(db_path()).add(a.data, fmt, opts, ver, img) if db_enabled() and not a.no_store else None
        print(f"Wrote {a.out} (version {ver}, EC {opts['ec']})" + (f" id={cid}" if cid else ""))
    elif a.cmd == "export":
        if not db_enabled():
            sys.exit("database disabled (QR_DB_ENABLED=false); nothing to export")
        doc = export_doc(Store(db_path()), a.images)
        text = json.dumps(doc, indent=2)
        if a.out == "-":
            print(text)
        else:
            with open(a.out, "w") as f:
                f.write(text + "\n")
            print(f"Exported {doc['count']} codes to {a.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
