"""
Stephen dashboard - FastAPI + SSE live feed + call list + transcript viewer + Excel upload.

Run:  uvicorn dashboard:app --host 0.0.0.0 --port 8080 --reload
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from fastapi import FastAPI, UploadFile, File, HTTPException, Form
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse

import transcript_logger as tl
import scrubber
import agent_db
import agent_state
from agent_state import router as agent_state_router
from transfer_queue import router as transfer_router

app = FastAPI(title="Stephen Dashboard")
app.include_router(transfer_router)
app.include_router(agent_state_router)

_event_queues: set[asyncio.Queue] = set()
_background_reaper: asyncio.Task | None = None


@app.on_event("startup")
async def _startup() -> None:
    global _background_reaper
    _background_reaper = agent_state.start_background_tasks()


@app.on_event("shutdown")
async def _shutdown() -> None:
    if _background_reaper and not _background_reaper.done():
        _background_reaper.cancel()


async def agent_event(event_type: str, data: dict[str, Any]) -> None:
    """Called by agent.py to push live events to all connected dashboards."""
    payload = json.dumps({"event": event_type, "data": data})
    for q in list(_event_queues):
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            pass


@app.get("/api/calls")
def api_calls(limit: int = 200):
    return tl.list_calls(limit)


@app.get("/api/calls/{call_id}")
def api_call_detail(call_id: str):
    c = tl.get_call(call_id)
    if not c:
        raise HTTPException(404)
    return c


@app.get("/api/stats")
def api_stats(hours: float = 24):
    return tl.stats(hours)


@app.get("/api/stream")
async def stream():
    q: asyncio.Queue = asyncio.Queue(maxsize=200)
    _event_queues.add(q)

    async def gen():
        try:
            yield "data: {\"event\":\"hello\"}\n\n"
            while True:
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=25)
                    yield f"data: {payload}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            _event_queues.discard(q)

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/api/upload")
async def upload(
    file: UploadFile = File(...),
    concurrent: int = Form(2),
    limit: int = Form(0),
    dry_run: bool = Form(False),
    no_scrub: bool = Form(False),
    scrub_only: bool = Form(False),
):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in (".xlsx", ".xls", ".csv"):
        raise HTTPException(400, "upload .xlsx or .csv")
    tmp = Path(tempfile.mkstemp(suffix=suffix)[1])
    tmp.write_bytes(await file.read())

    cmd = [sys.executable, "dialer.py", str(tmp), "--concurrent", str(concurrent)]
    if limit > 0:
        cmd += ["--limit", str(limit)]
    if dry_run:
        cmd.append("--dry-run")
    if no_scrub:
        cmd.append("--no-scrub")
    if scrub_only:
        cmd.append("--scrub-only")

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return JSONResponse({"status": "dialing" if not scrub_only else "scrubbing", "pid": proc.pid, "cmd": " ".join(cmd)})


# ---------------------------------------------------------------------------
# Internal DNC management endpoints
# ---------------------------------------------------------------------------

@app.get("/api/dnc")
def api_dnc_list(limit: int = 500):
    return {"numbers": scrubber.list_internal_dnc(limit), "total": scrubber.internal_dnc_count()}


@app.post("/api/dnc/add")
async def api_dnc_add(phone: str = Form(...), reason: str = Form("manual")):
    from dialer import normalize_phone
    normalized = normalize_phone(phone)
    if not normalized:
        raise HTTPException(400, f"invalid phone: {phone}")
    scrubber.add_to_internal_dnc(normalized, reason=reason)
    return {"status": "added", "phone": normalized}


@app.post("/api/dnc/remove")
async def api_dnc_remove(phone: str = Form(...)):
    from dialer import normalize_phone
    normalized = normalize_phone(phone)
    if not normalized:
        raise HTTPException(400, f"invalid phone: {phone}")
    scrubber.remove_from_internal_dnc(normalized)
    return {"status": "removed", "phone": normalized}


@app.post("/api/learn")
def trigger_learn():
    proc = subprocess.Popen([sys.executable, "learnings.py"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return {"status": "learning", "pid": proc.pid}


_INDEX_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>Stephen - Health Insurance Dialer</title>
<style>
:root{--bg:#0b0f1a;--panel:#141a2a;--panel2:#1a2238;--border:#22304f;--txt:#e5ecf5;--muted:#8492a6;--accent:#3dd6a5;--danger:#ff6b7a;--warn:#f5c56b;--info:#6bb6f5}
*{box-sizing:border-box}body{margin:0;font:14px/1.5 -apple-system,Segoe UI,Inter,sans-serif;background:var(--bg);color:var(--txt)}
header{padding:14px 22px;background:var(--panel);border-bottom:1px solid var(--border);display:flex;align-items:center;gap:16px}
h1{margin:0;font-size:18px;font-weight:600}
.badge{background:var(--accent);color:#0b0f1a;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:700}
.main{display:grid;grid-template-columns:300px 1fr 380px;height:calc(100vh - 52px)}
.panel{overflow-y:auto;padding:14px}
.panel+.panel{border-left:1px solid var(--border)}
h2{font-size:11px;text-transform:uppercase;letter-spacing:1px;color:var(--muted);margin:0 0 8px;font-weight:600}
.stat{background:var(--panel);padding:8px 12px;border-radius:8px;margin-bottom:6px;border:1px solid var(--border);display:flex;justify-content:space-between;align-items:baseline;gap:8px}
.stat .n{font-size:20px;font-weight:700}.stat .l{color:var(--muted);font-size:11px;text-align:right}
.tabs{display:flex;gap:4px;margin-bottom:10px;background:var(--panel);padding:3px;border-radius:8px;border:1px solid var(--border)}
.tab{flex:1;padding:6px 8px;border:none;background:transparent;color:var(--muted);cursor:pointer;border-radius:5px;font-size:12px;font-weight:600}
.tab.on{background:var(--panel2);color:var(--txt)}
.toolbar{display:flex;gap:6px;margin-bottom:8px}
.toolbar input,.toolbar select{flex:1;padding:6px 8px;background:var(--panel);color:var(--txt);border:1px solid var(--border);border-radius:6px;font-size:12px;min-width:0}
.calls-table{width:100%;border-collapse:collapse;font-size:12px}
.calls-table th{text-align:left;padding:6px 8px;color:var(--muted);font-weight:600;text-transform:uppercase;font-size:10px;letter-spacing:.5px;border-bottom:1px solid var(--border);position:sticky;top:0;background:var(--bg);cursor:pointer;user-select:none}
.calls-table th:hover{color:var(--txt)}
.calls-table td{padding:7px 8px;border-bottom:1px solid var(--border)}
.calls-table tr{cursor:pointer}
.calls-table tr:hover td{background:var(--panel)}
.calls-table tr.sel td{background:#143a2e}
.calls-table .phone{font-family:ui-monospace,monospace;font-size:11px}
.calls-table .dur{color:var(--muted);text-align:right;font-variant-numeric:tabular-nums}
.calls-table .when{color:var(--muted);font-size:11px;white-space:nowrap}
.empty{text-align:center;color:var(--muted);padding:30px 10px;font-size:12px}
.pill{display:inline-block;font-size:10px;padding:2px 7px;border-radius:10px;font-weight:600;letter-spacing:.3px;white-space:nowrap}
.pill.good{background:#143a2e;color:var(--accent)}
.pill.bad{background:#3a1620;color:var(--danger)}
.pill.warn{background:#3a2c14;color:var(--warn)}
.pill.info{background:#14293a;color:var(--info)}
.pill.muted{background:#222a3a;color:var(--muted)}
.feed{font-family:ui-monospace,monospace;font-size:11px}
.evt{padding:6px 8px;border-left:2px solid var(--accent);margin-bottom:4px;background:var(--panel);border-radius:0 6px 6px 0;word-break:break-word}
.evt.error{border-color:var(--danger)}.evt.rejected{border-color:var(--warn)}
.evt .t{color:var(--muted);font-size:10px}
.turn{padding:8px 12px;margin:6px 0;border-radius:10px;max-width:85%}
.turn.user{background:#1a2538;margin-right:auto}
.turn.assistant{background:#143a2e;margin-left:auto;color:#d0f5e4}
.turn .role{font-size:10px;color:var(--muted);margin-bottom:2px;text-transform:uppercase;letter-spacing:.5px}
.upload{background:var(--panel);padding:12px;border-radius:8px;border:1px solid var(--border);margin-bottom:12px}
.upload input,.upload select,.upload button{width:100%;margin-top:5px;padding:7px;background:var(--bg);color:var(--txt);border:1px solid var(--border);border-radius:6px;font-size:12px}
.upload button{background:var(--accent);color:#0b0f1a;font-weight:700;cursor:pointer;border:none}
.upload button.secondary{background:transparent;color:var(--txt);border:1px solid var(--border)}
.upload button.secondary:hover{border-color:var(--accent);color:var(--accent)}
.hdr-meta{display:flex;gap:10px;padding:8px 12px;background:var(--panel);border-radius:8px;margin-bottom:10px;font-size:12px;color:var(--muted);flex-wrap:wrap}
.hdr-meta b{color:var(--txt)}
</style></head><body>
<header><h1>Stephen</h1><span class="badge">LIVE</span><span id="conn" style="color:var(--muted);font-size:12px">connecting…</span></header>
<div class="main">
  <div class="panel">
    <div class="upload">
      <h2>Dial from Excel</h2>
      <input type="file" id="xlsx" accept=".xlsx,.xls,.csv">
      <input type="number" id="limit" placeholder="limit (0=all)" value="0">
      <input type="number" id="concurrent" placeholder="concurrent" value="2" min="1" max="10">
      <label style="display:flex;gap:6px;align-items:center;margin-top:6px;font-size:12px;color:var(--muted)">
        <input type="checkbox" id="noScrub" style="width:auto;margin:0"> Skip scrub
      </label>
      <button onclick="upload(false)">Start Dialing</button>
      <button class="secondary" onclick="upload(true)">Dry Run</button>
      <button class="secondary" onclick="scrubOnly()">Scrub Only</button>
      <button class="secondary" onclick="trainNow()">Train on Past Calls</button>
    </div>
    <div class="upload">
      <h2>Internal DNC</h2>
      <div id="dnc-count" style="font-size:12px;color:var(--muted);margin-bottom:6px"></div>
      <input type="text" id="dnc-phone" placeholder="+1XXXXXXXXXX or 10-digit">
      <button class="secondary" onclick="addDnc()">Add to DNC</button>
    </div>
    <h2>Stats</h2>
    <div class="tabs">
      <button class="tab on" data-h="24" onclick="setRange(this,24)">Today</button>
      <button class="tab" data-h="168" onclick="setRange(this,168)">7 days</button>
      <button class="tab" data-h="0" onclick="setRange(this,0)">All time</button>
    </div>
    <div id="stats"></div>
  </div>
  <div class="panel">
    <h2 style="display:flex;justify-content:space-between;align-items:center">
      <span>Recent Calls</span>
      <span id="callcount" style="color:var(--muted);font-size:11px;text-transform:none;letter-spacing:0;font-weight:400"></span>
    </h2>
    <div class="toolbar">
      <input id="qSearch" placeholder="Search phone or name…" oninput="renderCalls()">
      <select id="qOutcome" onchange="renderCalls()">
        <option value="">All outcomes</option>
        <option value="transferred">Transferred</option>
        <option value="rejected">Rejected</option>
        <option value="dnc">DNC</option>
        <option value="voicemail">Voicemail</option>
        <option value="no_answer">No answer</option>
        <option value="in_progress">In progress</option>
        <option value="unknown">Dropped/unclear</option>
        <option value="_error">Errors only</option>
      </select>
    </div>
    <table class="calls-table">
      <thead><tr>
        <th onclick="setSort('started_at')">Time</th>
        <th onclick="setSort('phone')">Phone</th>
        <th>Name</th>
        <th onclick="setSort('outcome')">Outcome</th>
        <th onclick="setSort('duration_s')" style="text-align:right">Dur</th>
      </tr></thead>
      <tbody id="calls"></tbody>
    </table>
    <div id="detail-pane" style="margin-top:18px">
      <h2>Transcript</h2>
      <div id="detail" style="color:var(--muted)">Click a row above to view its transcript.</div>
    </div>
  </div>
  <div class="panel">
    <h2>Live Event Feed</h2>
    <div id="feed" class="feed"></div>
  </div>
</div>
<script>
const $ = s => document.querySelector(s);
const STATE = {hours:24, sortKey:'started_at', sortDir:-1, calls:[], selected:null};

// Map raw outcome -> {label, kind}.
function outcomeMeta(o){
  if(!o) return {label:'?', kind:'muted'};
  if(o==='transferred') return {label:'Transferred', kind:'good'};
  if(o==='rejected')    return {label:'Rejected', kind:'bad'};
  if(o==='dnc')         return {label:'DNC', kind:'bad'};
  if(o==='voicemail')   return {label:'Voicemail', kind:'warn'};
  if(o==='no_answer')   return {label:'No answer', kind:'muted'};
  if(o==='in_progress') return {label:'In progress', kind:'info'};
  if(o==='unknown')     return {label:'Dropped', kind:'muted'};
  if(o.startsWith('sip_error:603')) return {label:'Carrier declined', kind:'bad'};
  if(o.startsWith('sip_error:486')) return {label:'Busy', kind:'warn'};
  if(o.startsWith('sip_error:480')) return {label:'Unavailable', kind:'warn'};
  if(o.startsWith('sip_error:404')) return {label:'Number not found', kind:'bad'};
  if(o.startsWith('sip_error:'))    return {label:'Carrier error', kind:'bad'};
  if(o.startsWith('error:'))        return {label:'Agent crashed', kind:'bad'};
  return {label:o, kind:'muted'};
}

function relTime(ts){
  if(!ts) return '';
  const d = new Date(ts*1000);
  const diff = (Date.now() - d.getTime())/1000;
  if(diff < 60) return Math.round(diff)+'s ago';
  if(diff < 3600) return Math.round(diff/60)+'m ago';
  if(diff < 86400) return Math.round(diff/3600)+'h ago';
  if(diff < 604800) return Math.round(diff/86400)+'d ago';
  return d.toLocaleDateString();
}

function setRange(btn, hours){
  document.querySelectorAll('.tab').forEach(t=>t.classList.toggle('on', t===btn));
  STATE.hours = hours;
  loadStats(); renderCalls();
}

function setSort(key){
  if(STATE.sortKey===key) STATE.sortDir *= -1;
  else { STATE.sortKey = key; STATE.sortDir = -1; }
  renderCalls();
}

async function loadStats(){
  const h = STATE.hours || 24*365*10;  // "all time" = 10y window
  const s = await fetch('/api/stats?hours='+h).then(r=>r.json());
  const rejdnc = (s.rejected||0)+(s.dnc||0);
  $('#stats').innerHTML =
    statRow(s.total||0, 'Total calls') +
    statRow(s.transferred||0, 'Transferred', s.conversion_pct+'% conversion', 'good') +
    statRow(rejdnc, 'Rejected / DNC', null, rejdnc?'bad':'muted') +
    statRow(s.voicemail||0, 'Voicemails', null, 'warn');
}
function statRow(n,l,sub,kind){
  const c = kind==='good'?'var(--accent)':kind==='bad'?'var(--danger)':kind==='warn'?'var(--warn)':'var(--txt)';
  return `<div class="stat"><div class="n" style="color:${c}">${n}</div><div class="l">${l}${sub?'<br>'+sub:''}</div></div>`;
}

async function loadCalls(){
  const c = await fetch('/api/calls?limit=500').then(r=>r.json());
  STATE.calls = c;
  renderCalls();
}

function renderCalls(){
  const q = ($('#qSearch').value||'').toLowerCase().trim();
  const outFilter = $('#qOutcome').value;
  const cutoff = STATE.hours ? (Date.now()/1000 - STATE.hours*3600) : 0;
  let rows = STATE.calls.filter(x=>{
    if(cutoff && (x.started_at||0) < cutoff) return false;
    if(q){
      const hay = ((x.phone||'')+' '+(x.first_name||'')).toLowerCase();
      if(!hay.includes(q)) return false;
    }
    if(outFilter){
      const o = x.outcome||'';
      if(outFilter==='_error') { if(!o.startsWith('sip_error:') && !o.startsWith('error:')) return false; }
      else if(o !== outFilter) return false;
    }
    return true;
  });
  rows.sort((a,b)=>{
    const k = STATE.sortKey, d = STATE.sortDir;
    const av = a[k]||0, bv = b[k]||0;
    if(av<bv) return -d; if(av>bv) return d; return 0;
  });
  $('#callcount').textContent = `${rows.length} call${rows.length===1?'':'s'}`;
  if(!rows.length){
    $('#calls').innerHTML = `<tr><td colspan="5" class="empty">No calls match these filters.</td></tr>`;
    return;
  }
  $('#calls').innerHTML = rows.map(x=>{
    const m = outcomeMeta(x.outcome);
    const dur = x.duration_s ? Math.round(x.duration_s)+'s' : '';
    const sel = STATE.selected===x.id ? 'sel' : '';
    return `<tr class="${sel}" onclick="showCall('${x.id}')">
      <td class="when">${relTime(x.started_at)}</td>
      <td class="phone">${x.phone||'?'}</td>
      <td>${escapeHTML(x.first_name||'')}</td>
      <td><span class="pill ${m.kind}">${m.label}</span></td>
      <td class="dur">${dur}</td>
    </tr>`;
  }).join('');
}

async function showCall(id){
  STATE.selected = id;
  document.querySelectorAll('.calls-table tr').forEach(r=>r.classList.remove('sel'));
  const c = await fetch('/api/calls/'+id).then(r=>r.json());
  const m = outcomeMeta(c.outcome);
  const header = `<div class="hdr-meta">
    <span><b>${escapeHTML(c.first_name||'?')}</b></span>
    <span>${c.phone||''}</span>
    <span>ZIP: <b>${c.zip||'-'}</b></span>
    <span>DOB: <b>${c.dob||'-'}</b></span>
    <span>Outcome: <span class="pill ${m.kind}">${m.label}</span></span>
    <span>${c.duration_s?Math.round(c.duration_s)+'s':''}</span>
  </div>`;
  const turns = (c.turns||[]).map(t=>`<div class="turn ${t.role}"><div class="role">${t.role}</div>${escapeHTML(t.text)}</div>`).join('');
  $('#detail').innerHTML = header + (turns||'<i>no turns yet</i>');
  renderCalls();
}

function escapeHTML(s){return (s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
async function upload(dryRun){
  const f = $('#xlsx').files[0]; if(!f){alert('pick a file');return}
  const fd = new FormData(); fd.append('file',f);
  fd.append('limit',$('#limit').value||0); fd.append('concurrent',$('#concurrent').value||2);
  fd.append('dry_run',dryRun);
  fd.append('no_scrub',$('#noScrub').checked);
  const r = await fetch('/api/upload',{method:'POST',body:fd}).then(r=>r.json());
  alert((dryRun?'Dry run started':'Dialing started')+' pid '+r.pid);
}
async function scrubOnly(){
  const f = $('#xlsx').files[0]; if(!f){alert('pick a file');return}
  const fd = new FormData(); fd.append('file',f);
  fd.append('limit',$('#limit').value||0); fd.append('concurrent',1);
  fd.append('scrub_only','true');
  const r = await fetch('/api/upload',{method:'POST',body:fd}).then(r=>r.json());
  alert('Scrub-only started pid '+r.pid);
}
async function addDnc(){
  const ph=$('#dnc-phone').value.trim(); if(!ph){alert('enter a phone number');return}
  const fd=new FormData(); fd.append('phone',ph); fd.append('reason','manual_dashboard');
  const r=await fetch('/api/dnc/add',{method:'POST',body:fd}).then(r=>r.json());
  if(r.status==='added'){alert('Added '+r.phone+' to internal DNC');$('#dnc-phone').value='';loadDnc()}
  else alert('Error: '+JSON.stringify(r));
}
async function loadDnc(){
  const r=await fetch('/api/dnc').then(r=>r.json());
  $('#dnc-count').textContent=r.total+' numbers on internal DNC list';
}
async function trainNow(){const r=await fetch('/api/learn',{method:'POST'}).then(r=>r.json());alert('Training started pid '+r.pid)}
function evt(o){
  const d = document.createElement('div');
  const kind = (o.event||'').includes('error')?'error':(o.event||'').includes('rejected')?'rejected':'';
  d.className='evt '+kind;
  d.innerHTML = `<div class="t">${new Date().toLocaleTimeString()}</div><b>${o.event}</b> ${JSON.stringify(o.data||{})}`;
  $('#feed').prepend(d);
  if($('#feed').childElementCount>150)$('#feed').lastChild.remove();
  if(['call_ended','call_transferring','call_rejected','call_voicemail','call_error','call_no_answer','call_started'].includes(o.event)){loadCalls();loadStats()}
}
const es=new EventSource('/api/stream');
es.onopen=()=>{$('#conn').textContent='connected';$('#conn').style.color='var(--accent)'};
es.onerror=()=>{$('#conn').textContent='reconnecting…';$('#conn').style.color='var(--danger)'};
es.onmessage=e=>{try{evt(JSON.parse(e.data))}catch{}};
loadStats();loadCalls();loadDnc();setInterval(()=>{loadStats();loadCalls();loadDnc()},15000);
</script></body></html>
"""


@app.get("/", response_class=HTMLResponse)
def index():
    return _INDEX_HTML


# ---------------------------------------------------------------------------
# Agent presence pages
# ---------------------------------------------------------------------------

_AGENT_PAGE_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>{name} — Agent Presence</title>
<style>
body{{margin:0;font:16px/1.5 -apple-system,Segoe UI,Inter,sans-serif;background:#0b0f1a;color:#e5ecf5;display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:100vh;padding:20px}}
.card{{background:#141a2a;border:1px solid #22304f;border-radius:14px;padding:28px;max-width:420px;width:100%;text-align:center}}
h1{{margin:0 0 4px;font-size:24px}}.sub{{color:#8492a6;font-size:13px;margin-bottom:24px}}
.status{{font-size:36px;font-weight:700;letter-spacing:0.5px;padding:18px;border-radius:12px;margin-bottom:24px}}
.status.Available{{background:#143a2e;color:#3dd6a5}}
.status.Busy{{background:#14293a;color:#6bb6f5}}
.status.Wrap-Up{{background:#3a2c14;color:#f5c56b}}
.status.Lunch{{background:#3a1f14;color:#f59c6b}}
.status.Offline{{background:#222a3a;color:#8492a6}}
.btns{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px}}
button{{padding:14px 8px;background:#0b0f1a;color:#e5ecf5;border:1px solid #22304f;border-radius:10px;font-size:14px;font-weight:600;cursor:pointer}}
button:hover{{border-color:#3dd6a5}}button.active{{background:#3dd6a5;color:#0b0f1a;border-color:#3dd6a5}}
.meta{{margin-top:18px;font-size:12px;color:#8492a6;display:flex;justify-content:space-between;gap:8px}}
.dot{{display:inline-block;width:8px;height:8px;border-radius:50%;background:#3dd6a5;margin-right:6px;animation:pulse 2s infinite}}
@keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:.3}}}}
.warn{{margin-top:12px;color:#f5c56b;font-size:12px}}
</style></head><body>
<div class="card">
  <h1>{name}</h1>
  <div class="sub">{email} · cell {cell} · licensed {states}</div>
  <div id="status" class="status Offline">OFFLINE</div>
  <div class="btns">
    <button onclick="setStatus('Available')" data-act="Available">Available</button>
    <button onclick="setStatus('Lunch')" data-act="Lunch">Lunch</button>
    <button onclick="setStatus('Offline')" data-act="Offline">Offline</button>
  </div>
  <div class="meta">
    <span><span class="dot"></span><span id="conn">connected</span></span>
    <span id="hb-ts">--</span>
  </div>
  <div class="warn">When a call comes in, your phone will ring at <b>{cell}</b>. Keep this page open.</div>
</div>
<script>
const AGENT_ID = {agent_id};
const $ = s => document.querySelector(s);

function render(activity) {{
  const st = $('#status');
  st.className = 'status ' + activity;
  st.textContent = activity.toUpperCase();
  document.querySelectorAll('button[data-act]').forEach(b => {{
    b.classList.toggle('active', b.dataset.act === activity);
  }});
}}

async function setStatus(activity) {{
  const r = await fetch(`/api/agents/${{AGENT_ID}}/activity`, {{
    method: 'PUT',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{activity}}),
  }});
  if (r.ok) {{
    const a = await r.json();
    render(a.current_activity);
  }} else {{
    alert('failed: ' + r.status);
  }}
}}

async function heartbeat() {{
  try {{
    await fetch(`/api/agents/${{AGENT_ID}}/heartbeat`, {{method:'POST'}});
    $('#hb-ts').textContent = 'hb ' + new Date().toLocaleTimeString();
    $('#conn').textContent = 'connected';
  }} catch(e) {{
    $('#conn').textContent = 'offline';
  }}
}}

async function refresh() {{
  try {{
    const a = await fetch(`/api/agents/${{AGENT_ID}}`).then(r => r.json());
    render(a.current_activity);
  }} catch(e) {{}}
}}

refresh();
heartbeat();
setInterval(heartbeat, 30000);
setInterval(refresh, 5000);
</script>
</body></html>
"""


@app.get("/agent/{agent_id}", response_class=HTMLResponse)
def agent_page(agent_id: int):
    agent = agent_db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, f"no agent with id={agent_id}")
    return _AGENT_PAGE_HTML.format(
        agent_id=agent_id,
        name=agent["name"],
        email=agent.get("email") or "—",
        cell=agent["cell_phone"],
        states=", ".join(agent.get("state_licenses") or []) or "—",
    )


# Admin view of all agents + recent routing decisions — quick at-a-glance for
# the sales manager. Phase-2 adds the Recent Routings panel below the agents
# table, color-coded by temperature so hot leads + compliance escalations pop.
_AGENTS_INDEX_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>Agents — Clairvo</title>
<style>
body{margin:0;font:14px/1.5 -apple-system,Segoe UI,Inter,sans-serif;background:#0b0f1a;color:#e5ecf5}
header{padding:14px 22px;background:#141a2a;border-bottom:1px solid #22304f;display:flex;align-items:center;gap:16px}
h1{margin:0;font-size:18px;font-weight:600}
main{padding:20px;max-width:1200px;margin:0 auto}
section{margin-bottom:30px}
h2{font-size:12px;text-transform:uppercase;letter-spacing:1px;color:#8492a6;margin:0 0 10px}
table{width:100%;border-collapse:collapse;background:#141a2a;border:1px solid #22304f;border-radius:10px;overflow:hidden}
th,td{padding:10px 12px;text-align:left;border-bottom:1px solid #22304f;vertical-align:middle}
th{font-size:11px;text-transform:uppercase;letter-spacing:1px;color:#8492a6;background:#0e1422}
tr:last-child td{border-bottom:none}
.pill{font-size:11px;padding:2px 8px;border-radius:10px;font-weight:600;display:inline-block}
.pill.Available{background:#143a2e;color:#3dd6a5}.pill.Busy{background:#14293a;color:#6bb6f5}
.pill.Wrap-Up{background:#3a2c14;color:#f5c56b}.pill.Lunch{background:#3a1f14;color:#f59c6b}
.pill.Offline{background:#222a3a;color:#8492a6}
.temp{font-size:10px;padding:2px 8px;border-radius:10px;font-weight:700;text-transform:uppercase}
.temp.hot{background:#3a1620;color:#ff6b7a}
.temp.warm{background:#143a2e;color:#3dd6a5}
.temp.compliance{background:#3a2c14;color:#f5c56b}
.temp.callback{background:#14293a;color:#6bb6f5}
.src{font-size:10px;padding:2px 8px;border-radius:10px;font-weight:600;text-transform:uppercase;color:#8492a6;background:#222a3a}
.src.no_agent_timeout{background:#3a1620;color:#ff6b7a}
.src.prospect_requested{background:#143a2e;color:#3dd6a5}
.btn-sm{font-size:11px;padding:3px 8px;background:#0b0f1a;color:#e5ecf5;border:1px solid #22304f;border-radius:6px;cursor:pointer;margin-right:4px}
.btn-sm:hover{border-color:#3dd6a5}
.outcome{font-size:11px;color:#8492a6}
.outcome.bridged{color:#3dd6a5}.outcome.abandoned,.outcome.no_agent,.outcome.failed{color:#ff6b7a}
a{color:#3dd6a5;text-decoration:none}a:hover{text-decoration:underline}
.hb-stale{color:#ff6b7a}
.mono{font-family:ui-monospace,monospace;font-size:12px}
.dim{color:#8492a6}
</style></head><body>
<header><h1>Agents</h1><span style="color:#8492a6">live presence + routing</span></header>
<main>
  <section>
    <h2>Live presence</h2>
    <table>
      <thead><tr><th>Name</th><th>Status</th><th>States</th><th>Cell</th><th>Heartbeat</th><th>Link</th></tr></thead>
      <tbody id="agent-rows"></tbody>
    </table>
  </section>
  <section>
    <h2>Recent routings (last 50)</h2>
    <table>
      <thead><tr><th>When</th><th>Temp</th><th>Lead</th><th>State</th><th>Agent</th><th>Outcome</th><th>Latency</th></tr></thead>
      <tbody id="routing-rows"></tbody>
    </table>
  </section>
  <section>
    <h2>Pending callbacks</h2>
    <table>
      <thead><tr><th>Added</th><th>Lead</th><th>State</th><th>Source</th><th>Requested</th><th>Attempts</th><th>Actions</th></tr></thead>
      <tbody id="cb-rows"></tbody>
    </table>
  </section>
</main>
<script>
function ago(ts) {
  if (!ts) return '—';
  const d = (Date.now()/1000) - ts;
  if (d < 60) return Math.round(d)+'s ago';
  if (d < 3600) return Math.round(d/60)+'m ago';
  return Math.round(d/3600)+'h ago';
}
function ms(a, b) {
  if (!a || !b) return '—';
  const d = Math.max(0, b - a);
  return d < 60 ? d.toFixed(1)+'s' : (d/60).toFixed(1)+'m';
}
async function loadAgents() {
  const agents = await fetch('/api/agents').then(r=>r.json());
  const now = Date.now()/1000;
  document.getElementById('agent-rows').innerHTML = agents.map(a => {
    const hb = a.last_heartbeat ? (now - a.last_heartbeat) : null;
    const hbCls = (hb===null || hb>90) ? 'hb-stale' : '';
    const hbTxt = hb===null ? 'never' : (hb<60 ? Math.round(hb)+'s ago' : Math.round(hb/60)+'m ago');
    const states = (a.state_licenses||[]).join(', ') || '—';
    return `<tr>
      <td>${a.name}${a.is_manager?' <span style="color:#f5c56b;font-size:11px">★ mgr</span>':''}</td>
      <td><span class="pill ${a.current_activity}">${a.current_activity}</span></td>
      <td>${states}</td>
      <td class="mono">${a.cell_phone}</td>
      <td class="${hbCls}">${hbTxt}</td>
      <td><a href="/agent/${a.id}">presence →</a></td>
    </tr>`;
  }).join('');
}
async function loadRoutings() {
  const log = await fetch('/api/transfer/log?limit=50').then(r=>r.json());
  document.getElementById('routing-rows').innerHTML = log.map(r => {
    const temp = (r.temperature||'warm').toLowerCase();
    const outcome = r.outcome || (r.assigned_at ? 'in-flight' : 'queued');
    const latency = ms(r.prepared_at, r.accepted_at);
    const agent = r.agent_name || (r.worker_sid ? r.worker_sid.slice(0,8)+'…' : '—');
    return `<tr>
      <td class="dim">${ago(r.prepared_at)}</td>
      <td><span class="temp ${temp}">${temp}</span></td>
      <td class="mono">${r.lead_phone||''} <span class="dim">${r.first_name||''}</span></td>
      <td>${r.required_state||'—'}</td>
      <td>${agent}</td>
      <td><span class="outcome ${outcome}">${outcome}</span></td>
      <td class="dim">${latency}</td>
    </tr>`;
  }).join('') || '<tr><td colspan="7" class="dim" style="text-align:center;padding:20px">no transfers yet</td></tr>';
}
async function loadCallbacks() {
  const cbs = await fetch('/api/transfer/callbacks?status=pending&limit=50').then(r=>r.json());
  document.getElementById('cb-rows').innerHTML = cbs.map(cb => {
    return `<tr>
      <td class="dim">${ago(cb.created_at)}</td>
      <td class="mono">${cb.lead_phone||''} <span class="dim">${cb.first_name||''}</span></td>
      <td>${cb.required_state||'—'}</td>
      <td><span class="src ${cb.source||''}">${cb.source||'—'}</span></td>
      <td class="dim">${cb.requested_at_local||'ASAP'}</td>
      <td>${cb.attempts||0}</td>
      <td>
        <button class="btn-sm" onclick="cbDone(${cb.id})">mark done</button>
        <button class="btn-sm" onclick="cbGaveUp(${cb.id})">give up</button>
      </td>
    </tr>`;
  }).join('') || '<tr><td colspan="7" class="dim" style="text-align:center;padding:20px">no pending callbacks</td></tr>';
}
async function cbDone(id) {
  await fetch(`/api/transfer/callbacks/${id}/completed`, {method:'PUT'});
  loadCallbacks();
}
async function cbGaveUp(id) {
  if (!confirm('Mark callback #'+id+' as given up?')) return;
  await fetch(`/api/transfer/callbacks/${id}/gave_up`, {method:'PUT', headers:{'Content-Type':'application/json'}, body:'{}'});
  loadCallbacks();
}
function tick() { loadAgents(); loadRoutings(); loadCallbacks(); }
tick(); setInterval(tick, 3000);
</script>
</body></html>
"""


@app.get("/agents", response_class=HTMLResponse)
def agents_index():
    return _AGENTS_INDEX_HTML
