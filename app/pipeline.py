import time
from app.config import ROOT,MODULES,RECEIPT_COLUMNS
from app.errors import DomainError
from app.values import scalar
from app.manifest import digest,manifest
from app import store
from app.modules.m1_taps.rules import evaluate as stage1
from app.modules.m2_routes.rules import evaluate as stage2
from app.modules.m3_fares.rules import evaluate as stage3
from app.modules.m4_journeys.rules import evaluate as stage4
from app.modules.m5_discounts.rules import evaluate as stage5
from app.modules.m6_clearing.rules import evaluate as stage6
from app.modules.m7_settlement.rules import evaluate as stage7
from app.modules.m8_receipts import rules as receipts
STAGES=[stage1,stage2,stage3,stage4,stage5,stage6,stage7]
def load_sample(name):
    if name not in {"dirty.csv","clean.csv","legacy.csv"}: raise DomainError("BAD_SAMPLE","样例不存在",404)
    return (ROOT/"sample_data"/name).read_bytes()
RECEIPT_SAMPLES={"receipts_full.csv","receipts_partial.csv","receipts_late.csv","receipts_conflict.csv"}
def load_receipt_sample(name):
    if name not in RECEIPT_SAMPLES: raise DomainError("BAD_SAMPLE","回执样例不存在",404)
    return (ROOT/"sample_data"/name).read_bytes()
def calculate(raw,params=None,stop=6):
    params=params or {};started=time.monotonic();context={"parameters":params,"raw":raw,"quarantine":[]};stages=[];rows=[]
    for index,stage in enumerate(STAGES[:stop+1]):
        result=stage(raw if index==0 else rows,context)
        result=scalar(result);rows=result["rows"]
        context["quarantine"].extend(result.get("quarantine",[]))
        if index==0: context["intake"]=result["metrics"]
        result["cumulative_quarantine"]=list(context["quarantine"])
        context[MODULES[index][0]]=result
        stages.append(result)
    return {"run_id":digest(raw,params)[:20],"stages":stages,"manifest":manifest(raw,params,started),"parameters":params}
def execute(raw,params=None):
    result=calculate(raw,params);store.keep_raw("taps",raw);return store.save(result["run_id"],result)
def stage_preview(index,payload):
    if "rows" in payload and index>0:
        if not isinstance(payload["rows"],list) or any(not isinstance(row,dict) for row in payload["rows"]): raise DomainError("BAD_ROWS","记录需要对象列表")
        if not isinstance(payload.get("context",{}),dict): raise DomainError("BAD_CONTEXT","上下文需要对象")
        return scalar(STAGES[index](payload["rows"],payload.get("context",{"parameters":{},"quarantine":[]})))
    raw=payload.get("csv")
    raw=raw.encode() if isinstance(raw,str) else load_sample(payload.get("sample","dirty.csv"))
    return calculate(raw,payload.get("parameters",{}),index)["stages"][-1]


def receipt_template():
    return ",".join(RECEIPT_COLUMNS)+"\n"


def reconcile_run(run_id,raw):
    """录入回执并重算。先算后写：计算失败只记错误，旧版结果原样保留。"""
    from app.modules.m8_receipts.service import reconcile_existing
    parsed=receipts.parse_receipts(raw)
    run=store.get(run_id)
    if store.seal_status(run_id)["sealed"]:
        raise DomainError("SEALED","已封存批次不能再接收回执",409)
    intake=store.ingest_receipts(run_id,parsed,raw)
    result=reconcile_existing(run_id,duplicate_receipts=intake["duplicates_total"])
    return {**result,"intake":{**intake,"parsed":parsed["metrics"]},"raw_sha256":intake["raw_sha256"]}
