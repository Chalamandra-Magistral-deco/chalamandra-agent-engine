import sqlite3, os, json, httpx, time, hmac, hashlib
from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, HttpUrl
from typing import List
from pathlib import Path
router=APIRouter()
SECRET=os.environ.get("CHALAMANDRA_DISTRIBUTION_HMAC_SECRET") or os.environ.get("CHALAMANDRA_CATEDRALX_JWT_SECRET") or os.environ.get("JWT_SECRET") or "local-dev-secret-change-me"
class SubReq(BaseModel): subscriber_id:str; webhook_url:HttpUrl; secret:str; events:List[str]=["candidate.accepted"]
class DistPayload(BaseModel): candidate_id:str; event_id:str=""; trace_id:str=""; event_type:str="result.accepted"
def get_db():
    d=os.environ.get("CHALAMANDRA_STATE_DIR","/home/chalamandra/.local/state/chalamandra")
    Path(d).mkdir(parents=True,exist_ok=True); Path(os.path.join(d,"agents")).mkdir(parents=True,exist_ok=True)
    conn=sqlite3.connect(os.path.join(d,"agents","tasks.db")); conn.row_factory=sqlite3.Row; return conn
def init():
    with get_db() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS subscriptions (id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT NOT NULL, events TEXT NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        conn.execute("CREATE TABLE IF NOT EXISTS distribution_events (event_id TEXT PRIMARY KEY, result_id TEXT, trace_id TEXT, dispatched_at INTEGER)")
        conn.commit()
def sign_body(b:bytes): return hmac.new(SECRET.encode(), b, hashlib.sha256).hexdigest()
@router.post("/v1/subscriptions",status_code=201)
async def create_subscription(sub:SubReq, request:Request):
    init()
    with get_db() as conn:
        cur=conn.cursor(); cur.execute("INSERT INTO subscriptions (url, events) VALUES (?,?)",(str(sub.webhook_url), json.dumps(sub.events))); sid=cur.lastrowid; conn.commit()
    return {"status":"success","subscription_id":sid}
@router.post("/internal/v1/distributions")
async def trigger_distribution(payload:DistPayload, traceparent:str=Header(None)):
    init()
    if payload.event_id:
        with get_db() as conn:
            if conn.execute("SELECT 1 FROM distribution_events WHERE event_id=?",(payload.event_id,)).fetchone():
                return {"status":"already_dispatched","event_id":payload.event_id,"trace_id":payload.trace_id}
    state_dir=os.environ.get("CHALAMANDRA_STATE_DIR","/home/chalamandra/.local/state/chalamandra")
    acc_path=os.path.join(state_dir,"agents","acceptance_store.jsonl")
    alt_path=os.path.join(state_dir,"acceptance_store.jsonl")
    target=None
    for cand_path in [acc_path, alt_path]:
        if os.path.exists(cand_path):
            acc_path=cand_path
            break
    if os.path.exists(acc_path):
        with open(acc_path) as f:
            for line in f:
                rec=json.loads(line)
                if rec.get("candidate_id")==payload.candidate_id or rec.get("result_id")==payload.candidate_id: target=rec; break
    if not target: target={"candidate_id":payload.candidate_id,"trace_id":payload.trace_id,"traceparent":traceparent}
    with get_db() as conn: rows=conn.execute("SELECT url, events FROM subscriptions").fetchall()
    dispatched=0; tp=traceparent or target.get("traceparent","00-5e5e59d88d1c4268aa9c4b3ad52db540-8b3f84dda25a423a-01")
    async with httpx.AsyncClient(timeout=5) as client:
        for row in rows:
            ev=json.loads(row["events"])
            if "candidate.accepted" in ev or payload.event_type in ev:
                body=json.dumps(target).encode(); sig=sign_body(body)
                try:
                    r=await client.post(row["url"], content=body, headers={"Content-Type":"application/json","X-Signature":sig,"X-Trace-Id":payload.trace_id or target.get("trace_id",""),"traceparent":tp})
                    if r.status_code==200: dispatched+=1
                except Exception as e: print(f"err {e}")
    if payload.event_id:
        with get_db() as conn: conn.execute("INSERT OR REPLACE INTO distribution_events VALUES (?,?,?,?)",(payload.event_id,payload.candidate_id,payload.trace_id,int(time.time()))); conn.commit()
    with open(os.path.join(state_dir,"traces.jsonl"),"a") as f: f.write(json.dumps({"type":"distribution","candidate_id":payload.candidate_id,"event_id":payload.event_id,"trace_id":payload.trace_id,"traceparent":tp,"dispatched_to":dispatched})+"\n")
    return {"status":"dispatched","event_id":payload.event_id,"dispatched_to":dispatched,"trace_id":payload.trace_id}
