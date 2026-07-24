#!/usr/bin/env python3
"""
PNX Record Lookup -- a local staff tool.

Enter an MMS ID, get the full PNX record (including the internal fields the public
catalog view hides), displayed in plain tables with CSV / Excel export.

It works the same way the link checker did: a headless browser loads the record's
showPnx view and reads the PNX JSON out of the page, so no API key is needed.

SETUP (one time):
    pip install playwright
    python -m playwright install chromium

RUN:
    python pnx_lookup_app.py
    # then open http://localhost:8765  (it tries to open automatically)

The institution settings default to USC and can be changed in the UI (saved in your
browser). For another institution, set the Primo host and view id (vid).
"""
import json
import os
import re
import sys
import threading
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sys.exit("Missing dependency.\n  pip install playwright\n  python -m playwright install chromium")

PORT = int(os.environ.get("PNX_PORT", "8765"))

# --------------------------------------------------------------- host allowlist
# The lookup drives a real browser to whatever host you provide, so only allow
# Ex Libris Primo hosts. This keeps the tool institution-agnostic (any Primo
# library works) while preventing it from being pointed at arbitrary servers.
# Vanity Primo domains (e.g. search.lib.example.edu) can be added via the
# PNX_EXTRA_HOSTS environment variable (comma-separated).
_PRIMO_RE = re.compile(r"^[a-z0-9][a-z0-9.-]*\.primo\.exlibrisgroup\.com$", re.I)
_EXTRA_HOSTS = {h.strip().lower() for h in os.environ.get("PNX_EXTRA_HOSTS", "").split(",") if h.strip()}


def host_allowed(host):
    host = (host or "").strip().lower()
    return bool(_PRIMO_RE.match(host)) or host in _EXTRA_HOSTS


# ------------------------------------------------------------------ PNX fetch
def extract_pnx_json(text):
    """Find the full PNX object inside arbitrary page text."""
    dec = json.JSONDecoder()
    for mt in re.finditer(r'\{', text):
        try:
            obj, _ = dec.raw_decode(text[mt.start():])
        except ValueError:
            continue
        if isinstance(obj, dict):
            if isinstance(obj.get("pnx"), dict):
                return obj["pnx"]
            if "display" in obj:
                return obj
            docs = obj.get("docs")
            if isinstance(docs, list) and docs and isinstance(docs[0], dict) and "pnx" in docs[0]:
                return docs[0]["pnx"]
    return None


def fetch_pnx(page, mmsid, host, vid, scope, tab):
    mmsid = mmsid.strip()
    doc = mmsid if mmsid.startswith("alma") else "alma" + mmsid
    url = (f"https://{host}/discovery/fulldisplay?context=L&vid={vid}"
           f"&search_scope={scope}&tab={tab}&lang=en&docid={doc}&showPnx=true")
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
    except Exception:
        pass
    for _ in range(40):  # up to ~20s
        try:
            values = page.eval_on_selector_all("textarea", "els => els.map(e => e.value)")
        except Exception:
            values = []
        for v in values:
            if v and ('"display"' in v or '"pnx"' in v):
                pnx = extract_pnx_json(v)
                if pnx:
                    return {"ok": True, "pnx": pnx, "url": url}
        page.wait_for_timeout(500)
    try:
        body = page.inner_text("body").lower()
    except Exception:
        body = ""
    if "cannot be displayed" in body or "no records" in body or "page not found" in body:
        return {"ok": False, "error": "Record cannot be displayed -- check the MMS ID.", "url": url}
    return {"ok": False, "error": "Timed out reading the PNX. Try again, or check host/vid settings.", "url": url}


# ------------------------------------------------------------------ HTTP server
class Handler(BaseHTTPRequestHandler):
    page = None  # set in main

    def log_message(self, *a):
        pass  # quiet

    def _send(self, code, body, ctype="application/json"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/":
            self._send(200, HTML_PAGE, "text/html; charset=utf-8")
            return
        if u.path == "/api/pnx":
            q = parse_qs(u.query)
            mmsid = (q.get("mmsid") or [""])[0]
            if not mmsid.strip():
                self._send(400, json.dumps({"ok": False, "error": "Enter an MMS ID."}))
                return
            host = (q.get("host") or [""])[0].strip()
            vid = (q.get("vid") or [""])[0].strip()
            scope = (q.get("scope") or ["MyInst_and_CI"])[0]
            tab = (q.get("tab") or ["Everything"])[0]
            if not host or not vid:
                self._send(400, json.dumps({"ok": False,
                    "error": "Set your Primo host and view id (vid) in Institution settings."}))
                return
            if not host_allowed(host):
                self._send(400, json.dumps({"ok": False,
                    "error": "Host must be an Ex Libris Primo host (ending in primo.exlibrisgroup.com)."}))
                return
            try:
                result = fetch_pnx(Handler.page, mmsid, host, vid, scope, tab)
            except Exception as e:
                result = {"ok": False, "error": f"Lookup failed: {e}"}
            self._send(200, json.dumps(result))
            return
        self._send(404, json.dumps({"ok": False, "error": "not found"}))


# ------------------------------------------------------------------ UI
HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PNX Record Lookup</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/xlsx/0.18.5/xlsx.full.min.js"></script>
<style>
  :root{
    --bg:#f6f6f4; --card:#fff; --ink:#1c1c1a; --muted:#6b6b66; --line:#e3e3df;
    --accent:#2f5b8f; --accent-soft:#e8eff7; --ok:#2f6b2f; --warn:#9a6a00;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);
    font:15px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial}
  header{background:var(--accent);color:#fff;padding:14px 22px}
  header h1{margin:0;font-size:17px;font-weight:600;letter-spacing:.01em}
  header p{margin:2px 0 0;font-size:12.5px;opacity:.85}
  .wrap{max-width:1040px;margin:0 auto;padding:22px}
  .bar{background:var(--card);border:1px solid var(--line);border-radius:12px;
    padding:16px;display:flex;gap:10px;align-items:flex-end;flex-wrap:wrap}
  .field{display:flex;flex-direction:column;gap:4px}
  .field label{font-size:12px;color:var(--muted)}
  input{font:inherit;padding:9px 11px;border:1px solid var(--line);border-radius:8px;background:#fff;color:var(--ink)}
  input:focus{outline:2px solid var(--accent-soft);border-color:var(--accent)}
  #mmsid{min-width:280px;font-variant-numeric:tabular-nums}
  button{font:inherit;cursor:pointer;border:1px solid transparent;border-radius:8px;padding:9px 16px;font-weight:600}
  .primary{background:var(--accent);color:#fff}
  .primary:hover{background:#244a76}
  .ghost{background:#fff;border-color:var(--line);color:var(--ink);font-weight:500}
  .ghost:hover{background:#faf7f7}
  .settings{margin-top:10px;font-size:13px}
  .settings summary{cursor:pointer;color:var(--muted);user-select:none}
  .settings .grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin-top:10px}
  .toolbar{display:flex;gap:8px;align-items:center;margin:18px 0 8px;flex-wrap:wrap}
  .toolbar .spacer{flex:1}
  .chk{font-size:13px;color:var(--muted);display:flex;gap:6px;align-items:center;cursor:pointer}
  .status{margin:16px 0;font-size:14px}
  .status.err{color:var(--accent)}
  .summary{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin-bottom:14px}
  .summary h2{margin:0 0 2px;font-size:18px}
  .summary .sub{color:var(--muted);font-size:13.5px}
  .pills{margin-top:10px;display:flex;gap:8px;flex-wrap:wrap}
  .pill{background:var(--accent-soft);color:var(--accent);border-radius:999px;padding:3px 11px;font-size:12.5px;font-weight:500}
  .pill b{font-weight:700}
  section.block{background:var(--card);border:1px solid var(--line);border-radius:12px;margin-bottom:12px;overflow:hidden}
  section.block > summary{cursor:pointer;padding:11px 16px;font-weight:600;font-size:14px;
    display:flex;justify-content:space-between;align-items:center;background:#fbfbfa;list-style:none}
  section.block > summary::-webkit-details-marker{display:none}
  section.block > summary .count{color:var(--muted);font-weight:500;font-size:12.5px}
  table{width:100%;border-collapse:collapse;font-size:13.5px}
  th,td{text-align:left;vertical-align:top;padding:8px 16px;border-top:1px solid var(--line)}
  th{width:230px;color:var(--muted);font-weight:600;white-space:nowrap}
  td{word-break:break-word}
  td .val{display:block;padding:1px 0}
  td .sub{color:var(--accent);background:var(--accent-soft);border-radius:4px;padding:0 5px;margin-left:4px;font-size:11.5px}
  .mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12.5px}
  .empty{color:var(--muted);text-align:center;padding:40px 0}
  footer{color:var(--muted);font-size:12px;text-align:center;padding:20px}
  .kbd{font-family:ui-monospace,monospace;background:#eee;border-radius:4px;padding:1px 5px;font-size:11px}
</style>
</head>
<body>
<header>
  <h1>PNX Record Lookup</h1>
  <p>Staff tool &middot; fetch a record's full normalized PNX by MMS ID</p>
</header>
<div class="wrap">
  <div class="bar">
    <div class="field">
      <label for="mmsid">MMS ID</label>
      <input id="mmsid" placeholder="e.g. 991049317955103731" autofocus>
    </div>
    <button class="primary" onclick="lookup()">Look up</button>
    <div style="flex:1"></div>
  </div>

  <details class="settings" id="settingsBox">
    <summary>Institution settings</summary>
    <div class="grid">
      <div class="field"><label>Primo host</label><input id="host" placeholder="your-inst.primo.exlibrisgroup.com"></div>
      <div class="field"><label>View id (vid)</label><input id="vid" placeholder="01ABC_INST:01ABC"></div>
      <div class="field"><label>Search scope</label><input id="scope" value="MyInst_and_CI"></div>
      <div class="field"><label>Tab</label><input id="tab" value="Everything"></div>
    </div>
  </details>

  <div id="status" class="status"></div>

  <div class="toolbar" id="toolbar" style="display:none">
    <input id="filter" placeholder="Filter fields &hellip;" oninput="applyFilter()"
           style="min-width:210px;padding:7px 10px;border:1px solid var(--line);border-radius:8px;font:inherit">
    <label class="chk"><input type="checkbox" id="splitSub" onchange="render()"> Split <span class="mono">$$</span> subfields</label>
    <div class="spacer"></div>
    <button class="ghost" onclick="exportCSV()">Export CSV</button>
    <button class="ghost" onclick="exportXLSX()">Export Excel</button>
  </div>

  <div id="results">
    <div class="empty">Enter an MMS ID above to retrieve its PNX record.</div>
  </div>
</div>
<footer>Runs locally &middot; reads the record's <span class="kbd">showPnx</span> view, no API key required</footer>

<script>
let CURRENT = null;       // current pnx object
let CURRENT_ID = "";      // mms id

const $ = id => document.getElementById(id);

// persist settings
["host","vid","scope","tab"].forEach(k=>{
  const v = localStorage.getItem("pnx_"+k);
  if(v) $(k).value = v;
  $(k).addEventListener("change", ()=>localStorage.setItem("pnx_"+k, $(k).value));
});
// Open settings automatically until a Primo host has been configured.
if(!$("host").value){ $("settingsBox").open = true; }
$("mmsid").addEventListener("keydown", e=>{ if(e.key==="Enter") lookup(); });

async function lookup(){
  const mmsid = $("mmsid").value.trim();
  if(!mmsid){ setStatus("Enter an MMS ID.", true); return; }
  setStatus("Looking up &hellip;");
  $("toolbar").style.display="none";
  $("results").innerHTML = '<div class="empty">Loading the record &hellip;</div>';
  const params = new URLSearchParams({mmsid, host:$("host").value, vid:$("vid").value, scope:$("scope").value, tab:$("tab").value});
  try{
    const r = await fetch("/api/pnx?"+params.toString());
    const data = await r.json();
    if(!data.ok){ setStatus(data.error||"Lookup failed.", true); $("results").innerHTML=""; CURRENT=null; return; }
    CURRENT = data.pnx; CURRENT_ID = mmsid;
    setStatus("");
    render();
    $("toolbar").style.display="flex";
  }catch(e){ setStatus("Could not reach the local service. Is the app still running?", true); }
}

function setStatus(html, err){ const s=$("status"); s.innerHTML=html; s.className = "status"+(err?" err":""); }

function arr(x){ return Array.isArray(x)?x : (x==null?[]:[x]); }

// render one value, optionally splitting $$ subfields
function renderValue(v){
  const split = $("splitSub").checked;
  if(typeof v === "object"){ return '<span class="val mono">'+escapeHtml(JSON.stringify(v))+'</span>'; }
  let s = String(v);
  if(split && s.includes("$$")){
    const parts = s.split(/\$\$/);
    let out = '<span class="val">'+escapeHtml(parts[0]);
    for(let i=1;i<parts.length;i++){
      const code = parts[i].slice(0,1), rest = parts[i].slice(1);
      out += ' <span class="sub">$$'+escapeHtml(code)+'</span>'+escapeHtml(rest);
    }
    return out+'</span>';
  }
  return '<span class="val">'+escapeHtml(s)+'</span>';
}

function escapeHtml(s){ return String(s).replace(/[&<>"]/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }

function summaryCard(pnx){
  const d = pnx.display||{}, c = pnx.control||{}, a = pnx.addata||{};
  const title = arr(d.title)[0]||"(untitled)";
  const author = (arr(d.creator).concat(arr(d.contributor)))[0]||arr(a.au)[0]||"";
  const ed = arr(d.edition)[0]||"";
  const type = arr(d.type)[0]||"";
  const year = arr(d.creationdate)[0]||"";
  const rid = arr(c.recordid)[0]||"";
  const isbn = arr(a.isbn).join(", ");
  const cleanAuthor = author.split("$$")[0];
  let pills = "";
  if(ed) pills += '<span class="pill"><b>Edition:</b> '+escapeHtml(ed)+'</span>';
  if(type) pills += '<span class="pill"><b>Type:</b> '+escapeHtml(type)+'</span>';
  if(year) pills += '<span class="pill"><b>Year:</b> '+escapeHtml(year)+'</span>';
  if(rid) pills += '<span class="pill mono">'+escapeHtml(rid)+'</span>';
  if(isbn) pills += '<span class="pill"><b>ISBN:</b> '+escapeHtml(isbn)+'</span>';
  return '<div class="summary"><h2>'+escapeHtml(title.trim())+'</h2>'+
    (cleanAuthor?'<div class="sub">'+escapeHtml(cleanAuthor)+'</div>':'')+
    '<div class="pills">'+pills+'</div></div>';
}

const SECTION_ORDER = ["display","addata","search","sort","control","facets","delivery","links","browse","frbr"];

function render(){
  if(!CURRENT){ return; }
  const pnx = CURRENT;
  let html = summaryCard(pnx);
  const keys = Object.keys(pnx);
  keys.sort((a,b)=>{
    let ia=SECTION_ORDER.indexOf(a), ib=SECTION_ORDER.indexOf(b);
    if(ia<0) ia=99; if(ib<0) ib=99;
    return ia-ib || a.localeCompare(b);
  });
  for(const sec of keys){
    const obj = pnx[sec];
    if(obj==null) continue;
    const entries = (typeof obj==="object" && !Array.isArray(obj)) ? Object.entries(obj) : [[sec, obj]];
    const rows = entries.filter(([k,v])=> v!=null && !(Array.isArray(v)&&v.length===0));
    if(!rows.length) continue;
    html += '<details class="block" open><summary>'+escapeHtml(sec)+
      '<span class="count">'+rows.length+' field'+(rows.length>1?'s':'')+'</span></summary><table><tbody>';
    for(const [k,v] of rows){
      const vals = arr(v).map(renderValue).join("");
      html += '<tr><th>'+escapeHtml(k)+'</th><td>'+vals+'</td></tr>';
    }
    html += '</tbody></table></details>';
  }
  $("results").innerHTML = html;
  applyFilter();
}

// live filter: hide rows (and empty sections) that don't match the query
function applyFilter(){
  const q = ($("filter") ? $("filter").value : "").trim().toLowerCase();
  document.querySelectorAll("#results details.block").forEach(sec=>{
    let visible = 0;
    sec.querySelectorAll("tbody tr").forEach(tr=>{
      const show = !q || tr.textContent.toLowerCase().includes(q);
      tr.style.display = show ? "" : "none";
      if(show) visible++;
    });
    sec.style.display = visible ? "" : "none";
  });
}

// flatten to [Section, Field, Value] rows for export
function flatten(){
  const rows = [["Section","Field","Value"]];
  if(!CURRENT) return rows;
  for(const [sec,obj] of Object.entries(CURRENT)){
    if(obj==null) continue;
    const entries = (typeof obj==="object" && !Array.isArray(obj)) ? Object.entries(obj) : [[sec,obj]];
    for(const [k,v] of entries){
      const vals = arr(v).map(x=> typeof x==="object"?JSON.stringify(x):String(x));
      if(!vals.length) continue;
      rows.push([sec, k, vals.join("  |  ")]);
    }
  }
  return rows;
}

function exportCSV(){
  const rows = flatten();
  const csv = rows.map(r=> r.map(c=>{
    const s=String(c); return /[",\n]/.test(s) ? '"'+s.replace(/"/g,'""')+'"' : s;
  }).join(",")).join("\r\n");
  download(new Blob([csv],{type:"text/csv"}), "pnx_"+CURRENT_ID+".csv");
}
function exportXLSX(){
  const ws = XLSX.utils.aoa_to_sheet(flatten());
  ws["!cols"]=[{wch:14},{wch:26},{wch:90}];
  const wb = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(wb, ws, "PNX");
  XLSX.writeFile(wb, "pnx_"+CURRENT_ID+".xlsx");
}
function download(blob, name){
  const a=document.createElement("a"); a.href=URL.createObjectURL(blob); a.download=name; a.click();
  setTimeout(()=>URL.revokeObjectURL(a.href), 1000);
}
</script>
</body>
</html>"""


def main():
    print("Starting headless browser ...")
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)
    ctx = browser.new_context(user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"))
    Handler.page = ctx.new_page()

    httpd = HTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://localhost:{PORT}"
    print(f"\n  PNX Record Lookup is running at  {url}\n  Press Ctrl+C to stop.\n")
    if not os.environ.get("PNX_NO_BROWSER"):
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down ...")
    finally:
        try:
            browser.close(); pw.stop()
        except Exception:
            pass


if __name__ == "__main__":
    main()
