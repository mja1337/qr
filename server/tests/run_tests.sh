#!/usr/bin/env bash
# End-to-end offline test. Usage: PY=path/to/python bash tests/run_tests.sh  (needs opencv-python-headless)
set -u
cd "$(dirname "$0")/.."
PY=${PY:-python3}; U="https://mja1337.github.io/timechain/"; T=$(mktemp -d); export QR_DB_PATH=$T/data/qrcodes.db
pass=0; fail=0; ok(){ echo "PASS $*"; pass=$((pass+1)); }; ko(){ echo "FAIL $*"; fail=$((fail+1)); }
chk(){ if eval "$2"; then ok "$1"; else ko "$1"; fi; }
start(){ $PY app.py serve --host 127.0.0.1 --port $1 >$T/srv$1.log 2>&1 & SP=$!; for i in $(seq 50); do curl -sf 127.0.0.1:$1/health >/dev/null && return; sleep 0.1; done; }
stop(){ kill $SP 2>/dev/null; wait $SP 2>/dev/null; }

echo "== DB OFF =="
QR_DB_ENABLED=false start 8091
chk "health says disabled" '[[ $(curl -s 127.0.0.1:8091/health) == *disabled* ]]'
chk "GET /qr png 200" '[[ $(curl -s -o $T/off.png -w %{http_code} "127.0.0.1:8091/qr?url=$U&format=png") == 200 ]]'
chk "POST /qr svg 200" '[[ $(curl -s -o $T/off.svg -w %{http_code} -H "Content-Type: application/json" -d "{\"url\":\"$U\",\"format\":\"svg\"}" 127.0.0.1:8091/qr) == 200 ]]'
chk "png decodes" '$PY verify_qr.py $T/off.png "$U" >/dev/null'
chk "png is pure black/white" '$PY -c "from PIL import Image;import sys;c={p for _,p in Image.open(sys.argv[1]).convert(\"RGB\").getcolors(1<<16)};sys.exit(0 if c<={(0,0,0),(255,255,255)} else 1)" $T/off.png'
chk "svg has no text element" '! grep -qi "<text" $T/off.svg'
chk "/codes 404 when off" '[[ $(curl -s -o /dev/null -w %{http_code} 127.0.0.1:8091/codes) == 404 ]]'
chk "/export.json 404 when off" '[[ $(curl -s -o /dev/null -w %{http_code} 127.0.0.1:8091/export.json) == 404 ]]'
chk "bad format 400" '[[ $(curl -s -o /dev/null -w %{http_code} "127.0.0.1:8091/qr?url=x&format=gif") == 400 ]]'
stop
chk "no DB file created when off" '[[ ! -e $QR_DB_PATH ]]'
QR_DB_ENABLED=false $PY app.py export -o $T/x.json 2>/dev/null; chk "CLI export refuses when off" '[[ $? -ne 0 ]]'

echo "== DB ON (default) =="
start 8092
chk "health says sqlite" '[[ $(curl -s 127.0.0.1:8092/health) == *sqlite* ]]'
ID=$(curl -s -D - -o $T/on.png "127.0.0.1:8092/qr?url=$U&format=png" | awk -F': ' 'tolower($1)=="x-qr-id"{print $2}' | tr -d '\r')
chk "png stored, got id ($ID)" '[[ -n "$ID" ]]'
curl -s -o $T/on.svg "127.0.0.1:8092/qr?url=$U&format=svg"
curl -s -o $T/plain.png "127.0.0.1:8092/qr?url=$U&ec=H&box=4"
curl -s -o /dev/null -d "data=hello world&box=5" 127.0.0.1:8092/qr
curl -s -o /dev/null "127.0.0.1:8092/qr?url=$U&nostore=1"
chk "/codes lists 4" '[[ $(curl -s 127.0.0.1:8092/codes | $PY -c "import json,sys;print(len(json.load(sys.stdin)))") == 4 ]]'
chk "/codes html table" '[[ $(curl -s -H "Accept: text/html" 127.0.0.1:8092/codes) == *"Saved codes (4)"* ]]'
chk "index html does not reference /assets/" '! curl -s 127.0.0.1:8092/ | grep -q "/assets/"'
chk "/assets/ returns 404" '[[ $(curl -s -o /dev/null -w %{http_code} 127.0.0.1:8092/assets/) == 404 ]]'
curl -s -o $T/stored.png 127.0.0.1:8092/codes/$ID
chk "stored image identical" 'cmp -s $T/on.png $T/stored.png'
chk "stored image decodes" '$PY verify_qr.py $T/stored.png "$U" >/dev/null'
chk "EC-H small box decodes" '$PY verify_qr.py $T/plain.png "$U" >/dev/null'
chk "unknown id 404" '[[ $(curl -s -o /dev/null -w %{http_code} 127.0.0.1:8092/codes/nope) == 404 ]]'
curl -s -o $T/http-export.json "127.0.0.1:8092/export.json?images=1"
stop
echo "== restart persistence =="
start 8093; chk "rows persist across restart" '[[ $(curl -s 127.0.0.1:8093/codes | $PY -c "import json,sys;print(len(json.load(sys.stdin)))") == 4 ]]'; stop
echo "== CLI =="
$PY app.py gen "$U" -o $T/cli.png >/dev/null && chk "CLI png decodes" '$PY verify_qr.py $T/cli.png "$U" >/dev/null'
$PY app.py gen "$U" -o $T/cli.svg >/dev/null
$PY app.py gen "$U" -o $T/nostore.png --no-store >/dev/null; chk "CLI --no-store decodes" '$PY verify_qr.py $T/nostore.png "$U" >/dev/null'
$PY app.py export -o $T/cli-export.json --images 2>/dev/null
echo "== export round-trip =="
$PY - "$QR_DB_PATH" "$T/http-export.json" "$T/cli-export.json" <<'PY'
import sys, json, base64, sqlite3, cv2, numpy as np
db, http, cli = sys.argv[1:]
rows = {r[0]: r[1:] for r in sqlite3.connect(db).execute("select id,data,format,image from codes")}
print(f"SQLite rows: {len(rows)}")
ok = True
for name, path, expect in (("HTTP", http, 4), ("CLI", cli, len(rows))):
    d = json.load(open(path)); n = len(d["codes"])
    good = d["count"] == n == expect
    dec = 0
    for c in d["codes"]:
        raw = base64.b64decode(c["image_base64"]); assert raw == rows[c["id"]][2]
        if c["format"] == "png":
            t, *_ = cv2.QRCodeDetector().detectAndDecode(cv2.imdecode(np.frombuffer(raw, np.uint8), 1))
            good &= t == c["data"]; dec += 1
    print(f"{'PASS' if good else 'FAIL'} {name} export: count={d['count']} codes={n} expected={expect}, base64==DB bytes, {dec} PNGs decoded to their data")
    ok &= good
sys.exit(0 if ok else 1)
PY
chk "export round-trip" '[[ $? -eq 0 ]]'
echo "RESULT: $pass passed, $fail failed  (tmp: $T)"
