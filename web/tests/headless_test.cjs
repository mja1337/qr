// Headless Chromium test. Usage: NODE_PATH=<dir with puppeteer-core> CHROME=/usr/bin/google-chrome node headless_test.cjs <outdir>
// Writes decoded-image candidates and export JSON into <outdir>; decode_check.py then verifies them with OpenCV/ZXing.
const puppeteer = require("puppeteer-core");
const fs = require("fs"), path = require("path");
const OUT = process.argv[2] || "/tmp/qa-out"; fs.mkdirSync(OUT, { recursive: true });
const PAGE = "file://" + path.resolve(__dirname, "..", "..", "index.html");
const U1 = "https://mja1337.github.io/timechain/", U2 = "https://example.com/svg-test",
      U3 = "https://example.com/products/coffee/bean-to-cup/?utm_source=qr&utm_medium=print&utm_campaign=2026-launch&x=" + "y".repeat(60);
let pass = 0, fail = 0;
const ok = (name, cond, extra = "") => { console.log(`${cond ? "PASS" : "FAIL"} ${name}${extra ? "  " + extra : ""}`); cond ? pass++ : fail++; };
const save = (name, dataUrl) => fs.writeFileSync(path.join(OUT, name), Buffer.from(dataUrl.split(",")[1], "base64"));

async function gen(page, data, format, box, ec) {
  await page.$eval("#data", (el, v) => { el.value = v; }, data);
  await page.select("#format", format); await page.select("#box", String(box)); await page.select("#ec", ec);
  await page.click("#gen");
  await page.waitForFunction(() => document.querySelector("#genMsg").textContent.length > 0);
  const r = await page.evaluate(() => ({ src: document.querySelector("#qrImg")?.src, msg: document.querySelector("#genMsg").textContent }));
  await page.evaluate(() => { document.querySelector("#genMsg").textContent = ""; });
  return r;
}

(async () => {
  const browser = await puppeteer.launch({ executablePath: process.env.CHROME || "/usr/bin/google-chrome", headless: true,
    args: ["--no-sandbox", "--allow-file-access-from-files"] , userDataDir: fs.mkdtempSync("/tmp/qa-prof-") });
  const external = [];
  async function newPage(mockStorage) {
    const page = await browser.newPage();
    await page.setRequestInterception(true);
    page.on("request", req => { const u = req.url(); if (/^(file|data|blob):/.test(u)) req.continue(); else { external.push(u); req.abort(); } });
    page.on("pageerror", e => { console.log("PAGEERROR", e.message); fail++; });
    if (mockStorage) await page.evaluateOnNewDocument(() => {
      // Mock of the *reported* artifact API: get/set/delete/list(…, shared) -> {key,value,shared}; missing key throws.
      try { localStorage.removeItem("qrgen-codes-v1"); } catch (e) {}  // so we can prove B never writes it
      const db = { personal: {}, shared: {} }; window.__calls = [];
      const ns = s => (s ? db.shared : db.personal);
      window.storage = {
        async get(k, s) { window.__calls.push(["get", k, s]); if (!(k in ns(s))) throw new Error("not found"); return { key: k, value: ns(s)[k], shared: !!s }; },
        async set(k, v, s) { window.__calls.push(["set", k, s]); if (typeof v !== "string") throw new Error("text only"); ns(s)[k] = v; return { key: k, value: v, shared: !!s }; },
        async delete(k, s) { delete ns(s)[k]; return { key: k, deleted: true, shared: !!s }; },
        async list(p = "", s) { return { keys: Object.keys(ns(s)).filter(k => k.startsWith(p)), shared: !!s }; },
      };
      window.__db = db;
    });
    await page.goto(PAGE, { waitUntil: "load" });
    await page.waitForFunction(() => document.querySelector("#backend").textContent !== "?");
    return page;
  }

  // ---------- A: no artifact host -> localStorage fallback ----------
  let page = await newPage(false);
  ok("A backend = localStorage fallback", (await page.$eval("#backend", e => e.textContent)).includes("localStorage"));
  ok("A storage on by default (checkbox checked)", await page.$eval("#dbOn", e => e.checked));
  let r = await gen(page, U1, "png", 10, "M"); save("a1.png", r.src); ok("A PNG generated + saved", r.src.startsWith("data:image/png") && r.msg.includes("saved"), r.msg);
  r = await gen(page, U2, "svg", 10, "M"); save("a2.svg", r.src); ok("A SVG generated + saved", r.src.startsWith("data:image/svg+xml") && r.msg.includes("saved"), r.msg);
  r = await gen(page, U3, "png", 4, "H"); save("a3.png", r.src); ok("A long URL, box 4, EC H", r.msg.includes("saved"), r.msg);
  let stored = await page.evaluate(k => JSON.parse(localStorage.getItem(k)).codes, "qrgen-codes-v1");
  ok("A localStorage has 3 rows", stored.length === 3);
  // download export.json through the real button (CDP download)
  const cdp = await page.target().createCDPSession();
  await cdp.send("Browser.setDownloadBehavior", { behavior: "allow", downloadPath: OUT });
  await page.click("#incImg"); await page.click("#dlJson");
  const dl = path.join(OUT, "export.json"); for (let i = 0; i < 50 && !fs.existsSync(dl); i++) await new Promise(r => setTimeout(r, 100));
  ok("A export.json downloaded via button", fs.existsSync(dl));
  await page.click("#dlPng"); const dlp = path.join(OUT, "qr.png"); for (let i = 0; i < 50 && !fs.existsSync(dlp); i++) await new Promise(r => setTimeout(r, 100));
  ok("A PNG downloaded via button", fs.existsSync(dlp));
  const exp = JSON.parse(fs.readFileSync(dl, "utf8"));
  ok("A export count == stored rows", exp.count === stored.length && exp.codes.length === stored.length);
  ok("A export rows match stored (id,data,format,options,version,created_at,bytes)",
     exp.codes.every((c, i) => ["id", "data", "format", "version", "created_at", "bytes"].every(k => c[k] === stored[i][k]) && JSON.stringify(c.options) === JSON.stringify(stored[i].options)));
  ok("A export has image_base64 for all", exp.codes.every(c => typeof c.image_base64 === "string" && c.image_base64.length > 100));
  ok("A image_base64 byte size == recorded bytes", exp.codes.every(c => Buffer.from(c.image_base64, "base64").length === c.bytes));
  const noImg = await page.evaluate(async () => await window.qrgen.exportDoc(false));
  ok("A export without images omits image_base64", noImg.codes.every(c => !("image_base64" in c)));
  // persistence across reload
  await page.reload({ waitUntil: "load" }); await page.waitForFunction(() => document.querySelectorAll("#codes tbody tr[data-id]").length > 0);
  ok("A rows persist across reload", (await page.$$eval("#codes tbody tr[data-id]", t => t.length)) === 3);
  // toggle off -> nothing written
  await page.click("#dbOn"); r = await gen(page, "https://example.com/off", "png", 10, "M");
  stored = await page.evaluate(k => JSON.parse(localStorage.getItem(k)).codes, "qrgen-codes-v1");
  ok("A toggle off: generated but not saved", !r.msg.includes("saved") && stored.length === 3, r.msg);
  ok("A toggle off: UI shows off", (await page.$eval("#backend", e => e.textContent)) === "off");
  // import into a clean store
  await page.evaluate(k => localStorage.removeItem(k), "qrgen-codes-v1");
  await page.click("#dbOn"); // back on, now empty
  ok("A cleared store is empty", (await page.evaluate(() => window.qrgen.codes.length)) === 0);
  await page.$eval("#jsonBox", (el, v) => { el.value = v; }, fs.readFileSync(dl, "utf8"));
  await page.click("#importBtn"); await page.waitForFunction(() => /Imported/.test(document.querySelector("#ioMsg").textContent));
  const reimp = await page.evaluate(k => JSON.parse(localStorage.getItem(k)).codes, "qrgen-codes-v1");
  ok("A import restores all rows (ids, data, bytes equal)", reimp.length === exp.count && reimp.every((c, i) => c.id === exp.codes[i].id && c.data === exp.codes[i].data && c.bytes === exp.codes[i].bytes),
     await page.$eval("#ioMsg", e => e.textContent));
  await page.click("#importBtn"); await page.waitForFunction(() => /skipped 3/.test(document.querySelector("#ioMsg").textContent));
  ok("A re-import skips duplicates", true);

  // ---------- A2: WiFi + Contact content types ----------
  const wifiExpect = "WIFI:T:WPA;S:Cafe\\;Net;P:p\\:ass;H:true;;";
  const wifiBuilt = await page.evaluate(() => window.qrgen.buildWifi({ ssid: "Cafe;Net", password: "p:ass", auth: "WPA", hidden: true }));
  ok("A2 buildWifi escapes ; and :", wifiBuilt === wifiExpect, wifiBuilt);
  await page.select("#kind", "wifi");
  await page.$eval("#wifi-ssid", el => { el.value = "Cafe;Net"; });
  await page.$eval("#wifi-pass", el => { el.value = "p:ass"; });
  await page.select("#wifi-auth", "WPA");
  await page.$eval("#wifi-hidden", el => { el.checked = true; });
  await page.select("#format", "png"); await page.select("#box", "10"); await page.select("#ec", "M");
  await page.evaluate(() => { document.querySelector("#genMsg").textContent = ""; });
  await page.click("#gen");
  await page.waitForFunction(() => /Version/.test(document.querySelector("#genMsg").textContent));
  let wifiSaved = await page.evaluate(() => window.qrgen.codes[window.qrgen.codes.length - 1]?.data);
  ok("A2 WiFi form generates escaped payload", wifiSaved === wifiExpect, wifiSaved);
  save("a-wifi.png", await page.$eval("#qrImg", e => e.src));

  const vcardExpect = "BEGIN:VCARD\nVERSION:3.0\nFN:Jane Doe\nTEL:+15551234567\nEMAIL:jane@example.com\nEND:VCARD";
  const vcardBuilt = await page.evaluate(() => window.qrgen.buildVCard({ name: "Jane Doe", phone: "+15551234567", email: "jane@example.com" }));
  ok("A2 buildVCard format", vcardBuilt === vcardExpect, vcardBuilt);
  await page.select("#kind", "contact");
  await page.$eval("#contact-name", el => { el.value = "Jane Doe"; });
  await page.$eval("#contact-phone", el => { el.value = "+15551234567"; });
  await page.$eval("#contact-email", el => { el.value = "jane@example.com"; });
  await page.evaluate(() => { document.querySelector("#genMsg").textContent = ""; });
  await page.click("#gen");
  await page.waitForFunction(() => /Version/.test(document.querySelector("#genMsg").textContent));
  let contactSaved = await page.evaluate(() => window.qrgen.codes[window.qrgen.codes.length - 1]?.data);
  ok("A2 Contact form generates vCard payload", contactSaved === vcardExpect, contactSaved);
  save("a-contact.png", await page.$eval("#qrImg", e => e.src));
  await page.select("#kind", "text");
  // style: unofficial logo preset forces EC H
  await page.evaluate(async () => {
    const p = window.qrgen.LOGO_PRESETS.find(x => x.id === "bitcoin");
    await window.qrgen.setLogo(p.dataUrl, p.id);
  });
  ok("A2 logo preset forces EC select to H", await page.$eval("#ec", e => e.value === "H" && e.disabled));
  await page.evaluate(() => { document.querySelector("#genMsg").textContent = ""; });
  await page.$eval("#data", el => { el.value = "https://example.com/logo-test"; });
  await page.click("#gen");
  await page.waitForFunction(() => /Version/.test(document.querySelector("#genMsg").textContent));
  const logoOpts = await page.evaluate(() => window.qrgen.codes[window.qrgen.codes.length - 1]?.options);
  ok("A2 saved options include logo + EC H", logoOpts && logoOpts.ec === "H" && typeof logoOpts.logo === "string" && logoOpts.logo.indexOf("data:image/") === 0, JSON.stringify({ ec: logoOpts?.ec, logo: !!logoOpts?.logo }));
  await page.evaluate(async () => { await window.qrgen.setLogo(null); });
  await page.close();

  // ---------- B: mocked artifact window.storage (reported API) ----------
  page = await newPage(true);
  ok("B backend = artifact storage (personal)", (await page.$eval("#backend", e => e.textContent)).includes("artifact storage (personal)"));
  r = await gen(page, U1, "png", 6, "Q"); save("b1.png", r.src); ok("B PNG saved via window.storage", r.msg.includes("saved"), r.msg);
  r = await gen(page, U2, "svg", 10, "M"); ok("B SVG saved", r.msg.includes("saved"));
  const b = await page.evaluate(() => ({ calls: window.__calls, personal: window.__db.personal, shared: window.__db.shared, ls: (() => { try { return localStorage.getItem("qrgen-codes-v1"); } catch (e) { return "ERR"; } })() }));
  ok("B all calls pass shared=false explicitly", b.calls.length > 0 && b.calls.every(c => c[2] === false));
  ok("B one key, 2 rows, shared ns untouched", JSON.parse(b.personal["qrgen-codes-v1"]).codes.length === 2 && Object.keys(b.shared).length === 0);
  ok("B localStorage not written", b.ls === null);
  const nCalls = b.calls.length;
  await page.click("#dbOn"); await gen(page, "https://example.com/off", "png", 10, "M");
  ok("B toggle off: zero storage calls", (await page.evaluate(() => window.__calls.length)) === nCalls);
  const bexp = await page.evaluate(async () => {
    document.querySelector("#dbOn").click();
    await new Promise(r => setTimeout(r, 200));
    return await window.qrgen.exportDoc(true);
  });
  fs.writeFileSync(path.join(OUT, "b-export.json"), JSON.stringify(bexp));
  ok("B export count == stored rows", bexp.count === 2);
  await page.close();

  ok("no external network requests", external.length === 0, external.join(" "));
  await browser.close();
  console.log(`JS RESULT: ${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
