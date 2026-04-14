"""
Emma dashboard - FastAPI + SSE live feed + call list + transcript viewer + Excel upload.

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

app = FastAPI(title="Emma Dashboard")

_event_queues: set[asyncio.Queue] = set()


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
async def upload(file: UploadFile = File(...), concurrent: int = Form(2), limit: int = Form(0), dry_run: bool = Form(False)):
    suffix = Path(file.filename).suffix.lower()
    if suffix not in (".xlsx", ".xls", ".csv"):
        raise HTTPException(400, "upload .xlsx or .csv")
    tmp = Path(tempfile.mkstemp(suffix=suffix)[1])
    tmp.write_bytes(await file.read())

    cmd = [sys.executable, "dialer.py", str(tmp), "--concurrent", str(concurrent)]
    if limit > 0:
        cmd += ["--limit", str(limit)]
    if dry_run:
        cmd.append("--dry-run")

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return JSONResponse({"status": "dialing", "pid": proc.pid, "cmd": " ".join(cmd)})


@app.post("/api/learn")
def trigger_learn():
    proc = subprocess.Popen([sys.executable, "learnings.py"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return {"status": "learning", "pid": proc.pid}


_INDEX_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>Emma - Health Insurance Dialer</title>
<style>
:root{--bg:#0b0f1a;--panel:#141a2a;--border:#22304f;--txt:#e5ecf5;--muted:#8492a6;--accent:#3dd6a5;--danger:#ff6b7a;--warn:#f5c56b}
*{box-sizing:border-box}body{margin:0;font:14px/1.5 -apple-system,Segoe UI,Inter,sans-serif;background:var(--bg);color:var(--txt)}
header{padding:14px 22px;background:var(--panel);border-bottom:1px solid var(--border);display:flex;align-items:center;gap:16px}
h1{margin:0;font-size:18px;font-weight:600}
.badge{background:var(--accent);color:#0b0f1a;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:700}
.main{display:grid;grid-template-columns:340px 1fr 420px;height:calc(100vh - 52px)}
.panel{overflow-y:auto;padding:14px}
.panel+.panel{border-left:1px solid var(--border)}
h2{font-size:12px;text-transform:uppercase;letter-spacing:1px;color:var(--muted);margin:0 0 10px}
.stat{background:var(--panel);padding:10px 12px;border-radius:8px;margin-bottom:8px;border:1px solid var(--border)}
.stat .n{font-size:22px;font-weight:700}.stat .l{color:var(--muted);font-size:12px}
.call{padding:10px;border:1px solid var(--border);border-radius:8px;margin-bottom:6px;cursor:pointer;background:var(--panel)}
.call:hover{border-color:var(--accent)}
.call .row{display:flex;justify-content:space-between;gap:8px;align-items:center}
.call .phone{font-family:ui-monospace,monospace;font-size:13px}
.call .name{color:var(--muted);font-size:12px}
.pill{font-size:10px;padding:2px 7px;border-radius:10px;font-weight:600;text-transform:uppercase}
.pill.transferred{background:#143a2e;color:var(--accent)}
.pill.rejected,.pill.dnc{background:#3a1620;color:var(--danger)}
.pill.voicemail{background:#3a2c14;color:var(--warn)}
.pill.in_progress{background:#14293a;color:#6bb6f5}
.pill.unknown,.pill.completed{background:#222a3a;color:var(--muted)}
.feed{font-family:ui-monospace,monospace;font-size:12px}
.evt{padding:6px 8px;border-left:2px solid var(--accent);margin-bottom:4px;background:var(--panel);border-radius:0 6px 6px 0;word-break:break-word}
.evt.error{border-color:var(--danger)}.evt.rejected{border-color:var(--warn)}
.evt .t{color:var(--muted);font-size:10px}
.turn{padding:8px 12px;margin:6px 0;border-radius:10px;max-width:85%}
.turn.user{background:#1a2538;margin-right:auto}
.turn.assistant{background:#143a2e;margin-left:auto;color:#d0f5e4}
.turn .role{font-size:10px;color:var(--muted);margin-bottom:2px;text-transform:uppercase;letter-spacing:.5px}
.upload{background:var(--panel);padding:14px;border-radius:10px;border:1px solid var(--border);margin-bottom:14px}
.upload input,.upload button{width:100%;margin-top:6px;padding:8px;background:#0b0f1a;color:var(--txt);border:1px solid var(--border);border-radius:6px}
.upload button{background:var(--accent);color:#0b0f1a;font-weight:700;cursor:pointer;border:none}
.upload button.secondary{background:transparent;color:var(--txt);border:1px solid var(--border)}
.hdr-meta{display:flex;gap:10px;padding:8px 12px;background:var(--panel);border-radius:8px;margin-bottom:10px;font-size:12px;color:var(--muted);flex-wrap:wrap}
.hdr-meta b{color:var(--txt)}
</style></head><body>
<header><h1>Emma</h1><span class="badge">LIVE</span><span id="conn" style="color:var(--muted);font-size:12px">connecting…</span></header>
<div class="main">
  <div class="panel">
    <div class="upload">
      <h2>Dial from Excel</h2>
      <input type="file" id="xlsx" accept=".xlsx,.xls,.csv">
      <input type="number" id="limit" placeholder="limit (0=all)" value="0">
      <input type="number" id="concurrent" placeholder="concurrent" value="2" min="1" max="10">
      <button onclick="upload(false)">Start Dialing</button>
      <button class="secondary" onclick="upload(true)">Dry Run</button>
      <button class="secondary" onclick="trainNow()">Train on Past Calls</button>
    </div>
    <h2>Stats (24h)</h2>
    <div id="stats"></div>
    <h2>Recent Calls</h2>
    <div id="calls"></div>
  </div>
  <div class="panel" id="detail-pane">
    <h2>Transcript</h2>
    <div id="detail" style="color:var(--muted)">Select a call on the left to view its transcript.</div>
  </div>
  <div class="panel">
    <h2>Live Event Feed</h2>
    <div id="feed" class="feed"></div>
  </div>
</div>
<script>
const $ = s => document.querySelector(s);
async function loadStats(){
  const s = await fetch('/api/stats').then(r=>r.json());
  $('#stats').innerHTML =
    stat(s.total,'Total calls') +
    stat(s.transferred,'Transferred',`${s.conversion_pct}% conversion`) +
    stat((s.rejected||0)+(s.dnc||0),'Rejected / DNC') +
    stat(s.voicemail||0,'Voicemails');
}
function stat(n,l,sub){return `<div class="stat"><div class="n">${n}</div><div class="l">${l}${sub?' · '+sub:''}</div></div>`}
async function loadCalls(){
  const c = await fetch('/api/calls?limit=100').then(r=>r.json());
  $('#calls').innerHTML = c.map(x=>`
    <div class="call" onclick="showCall('${x.id}')">
      <div class="row"><span class="phone">${x.phone||'?'}</span><span class="pill ${x.outcome||'unknown'}">${x.outcome||'?'}</span></div>
      <div class="row"><span class="name">${x.first_name||''}</span><span class="name">${x.duration_s?Math.round(x.duration_s)+'s':''}</span></div>
    </div>`).join('');
}
async function showCall(id){
  const c = await fetch('/api/calls/'+id).then(r=>r.json());
  const header = `<div class="hdr-meta">
    <span><b>${c.first_name||'?'}</b></span>
    <span>${c.phone||''}</span>
    <span>ZIP: <b>${c.zip||'-'}</b></span>
    <span>DOB: <b>${c.dob||'-'}</b></span>
    <span>Outcome: <b>${c.outcome}</b></span>
    <span>${c.duration_s?Math.round(c.duration_s)+'s':''}</span>
  </div>`;
  const turns = (c.turns||[]).map(t=>`<div class="turn ${t.role}"><div class="role">${t.role}</div>${escapeHTML(t.text)}</div>`).join('');
  $('#detail').innerHTML = header + (turns||'<i>no turns yet</i>');
}
function escapeHTML(s){return (s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
async function upload(dryRun){
  const f = $('#xlsx').files[0]; if(!f){alert('pick a file');return}
  const fd = new FormData(); fd.append('file',f);
  fd.append('limit',$('#limit').value||0); fd.append('concurrent',$('#concurrent').value||2);
  fd.append('dry_run',dryRun);
  const r = await fetch('/api/upload',{method:'POST',body:fd}).then(r=>r.json());
  alert((dryRun?'Dry run started':'Dialing started')+' pid '+r.pid);
}
async function trainNow(){const r=await fetch('/api/learn',{method:'POST'}).then(r=>r.json());alert('Training started pid '+r.pid)}
function evt(o){
  const d = document.createElement('div');
  const kind = (o.event||'').includes('error')?'error':(o.event||'').includes('rejected')?'rejected':'';
  d.className='evt '+kind;
  d.innerHTML = `<div class="t">${new Date().toLocaleTimeString()}</div><b>${o.event}</b> ${JSON.stringify(o.data||{})}`;
  $('#feed').prepend(d);
  if($('#feed').childElementCount>150)$('#feed').lastChild.remove();
  if(['call_ended','call_transferring','call_rejected','call_voicemail','call_error'].includes(o.event)){loadCalls();loadStats()}
}
const es=new EventSource('/api/stream');
es.onopen=()=>{$('#conn').textContent='connected';$('#conn').style.color='var(--accent)'};
es.onerror=()=>{$('#conn').textContent='reconnecting…';$('#conn').style.color='var(--danger)'};
es.onmessage=e=>{try{evt(JSON.parse(e.data))}catch{}};
loadStats();loadCalls();setInterval(()=>{loadStats();loadCalls()},15000);
</script></body></html>
"""


@app.get("/", response_class=HTMLResponse)
def index():
    return _INDEX_HTML
