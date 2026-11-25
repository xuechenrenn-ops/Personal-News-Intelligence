import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

CACHE_ROOT=Path(".cache")
STATE_PATH=CACHE_ROOT/"maintenance.json"
TRANSIENT_DIR=CACHE_ROOT/"transient"
WEEKLY_CACHE_DIRS=("search_results","article_pages","model_responses","downloads")

def _now(now=None):
    value=now or datetime.now(timezone.utc)
    if value.tzinfo is None: value=value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)

def _load():
    try:
        value=json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value,dict) else {}
    except (OSError,json.JSONDecodeError): return {}

def _save(data):
    CACHE_ROOT.mkdir(parents=True,exist_ok=True)
    tmp=STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
    tmp.replace(STATE_PATH)

def cleanup_daily_transients():
    if not TRANSIENT_DIR.exists(): return {"files_removed":0,"bytes_released":0}
    count=size=0
    for p in TRANSIENT_DIR.rglob("*"):
        if p.is_file():
            try: size+=p.stat().st_size; count+=1
            except OSError: pass
    shutil.rmtree(TRANSIENT_DIR,ignore_errors=True)
    return {"files_removed":count,"bytes_released":size}

def run_weekly_cache_cleanup(now=None):
    current=_now(now); state=_load(); previous=state.get("last_weekly_cleanup_at")
    if previous:
        try:
            last=datetime.fromisoformat(str(previous).replace("Z","+00:00"))
            if last.tzinfo is None: last=last.replace(tzinfo=timezone.utc)
            if current-last<timedelta(days=7): return {"ran":False,"files_removed":0,"bytes_released":0}
        except ValueError: pass
    cutoff=current.timestamp()-7*86400; count=size=0
    for dirname in WEEKLY_CACHE_DIRS:
        root=CACHE_ROOT/dirname
        if not root.is_dir(): continue
        for p in sorted(root.rglob("*"),reverse=True):
            try:
                if p.is_file() and p.stat().st_mtime<cutoff:
                    size+=p.stat().st_size; p.unlink(); count+=1
                elif p.is_dir():
                    try: p.rmdir()
                    except OSError: pass
            except OSError: continue
    state["last_weekly_cleanup_at"]=current.isoformat(); _save(state)
    return {"ran":True,"files_removed":count,"bytes_released":size}

def monthly_tavily_credits(now=None):
    return int(_load().get("tavily_monthly_credits",{}).get(_now(now).strftime("%Y-%m"),0))

def record_tavily_credits(credits,now=None):
    current=_now(now); state=_load(); usage=state.get("tavily_monthly_credits",{}); month=current.strftime("%Y-%m")
    usage[month]=int(usage.get(month,0))+max(0,int(credits))
    state["tavily_monthly_credits"]={k:usage[k] for k in sorted(usage)[-12:]}
    _save(state); return int(state["tavily_monthly_credits"][month])
