import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.config import DATA_DIR
from app.errors import DomainError


def connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DATA_DIR / "records.db", timeout=20)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY,payload TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 1,decision TEXT NOT NULL DEFAULT '待复核')")
    db.execute("CREATE TABLE IF NOT EXISTS raw_documents (sha256 TEXT PRIMARY KEY,kind TEXT NOT NULL,content BLOB NOT NULL,stored_at TEXT NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS receipts (run_id TEXT NOT NULL,receipt_id TEXT NOT NULL,payload TEXT NOT NULL,fingerprint TEXT NOT NULL,raw_sha256 TEXT NOT NULL,ingested_at TEXT NOT NULL,PRIMARY KEY (run_id,receipt_id))")
    db.execute("CREATE TABLE IF NOT EXISTS receipt_conflicts (run_id TEXT NOT NULL,receipt_id TEXT NOT NULL,fingerprint TEXT NOT NULL,payload TEXT NOT NULL,raw_sha256 TEXT NOT NULL,ingested_at TEXT NOT NULL,PRIMARY KEY (run_id,receipt_id,fingerprint))")
    db.execute("CREATE TABLE IF NOT EXISTS receipt_resolutions (run_id TEXT NOT NULL,receipt_id TEXT NOT NULL,decision TEXT NOT NULL,note TEXT NOT NULL,resolved_at TEXT NOT NULL,PRIMARY KEY (run_id,receipt_id))")
    db.execute("CREATE TABLE IF NOT EXISTS receipt_duplicates (run_id TEXT NOT NULL,receipt_id TEXT NOT NULL,fingerprint TEXT NOT NULL,seen_at TEXT NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS receipt_files (run_id TEXT NOT NULL,sha256 TEXT NOT NULL,accepted INTEGER NOT NULL,rejected INTEGER NOT NULL,ingested_at TEXT NOT NULL,PRIMARY KEY (run_id,sha256))")
    db.execute("CREATE TABLE IF NOT EXISTS recons (run_id TEXT NOT NULL,version INTEGER NOT NULL,set_sha256 TEXT NOT NULL,payload TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY (run_id,version),UNIQUE (run_id,set_sha256))")
    db.execute("CREATE TABLE IF NOT EXISTS recon_errors (id INTEGER PRIMARY KEY AUTOINCREMENT,run_id TEXT NOT NULL,set_sha256 TEXT NOT NULL,message TEXT NOT NULL,created_at TEXT NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS seals (run_id TEXT PRIMARY KEY,recon_version INTEGER NOT NULL,set_sha256 TEXT NOT NULL,records_sha256 TEXT NOT NULL,sealed_at TEXT NOT NULL)")
    return db


def _now():
    return datetime.now(timezone.utc).isoformat()


def keep_raw(kind, raw):
    """原样留档原始刷卡或回执文件，按内容摘要去重，不丢字节。"""
    import hashlib
    sha = hashlib.sha256(raw).hexdigest()
    with connect() as db:
        db.execute("INSERT OR IGNORE INTO raw_documents(sha256,kind,content,stored_at) VALUES (?,?,?,?)", (sha, kind, raw, _now()))
    return sha


def get_raw(sha):
    with connect() as db:
        row = db.execute("SELECT kind,content FROM raw_documents WHERE sha256=?", (sha,)).fetchone()
    if not row:
        raise DomainError("NOT_FOUND", "原始文件不存在", 404)
    return {"sha256": sha, "kind": row[0], "content": row[1]}


def save(run_id, payload):
    with connect() as db:
        db.execute("INSERT OR IGNORE INTO runs(id,payload) VALUES (?,?)", (run_id, json.dumps(payload, ensure_ascii=False)))
    return get(run_id)


def get(run_id):
    with connect() as db:
        row = db.execute("SELECT payload,version,decision FROM runs WHERE id=?", (run_id,)).fetchone()
    if not row:
        raise DomainError("NOT_FOUND", "批次不存在", 404)
    return {**json.loads(row[0]), "version": row[1], "decision": row[2]}


def latest():
    with connect() as db:
        row = db.execute("SELECT id FROM runs ORDER BY rowid DESC LIMIT 1").fetchone()
    return get(row[0]) if row else None


def history():
    with connect() as db:
        return [{"run_id": i, "version": v, "decision": d} for i, v, d in db.execute("SELECT id,version,decision FROM runs ORDER BY rowid DESC")]


def review(run_id, version, decision):
    if decision not in {"已复核", "退回复核"}:
        raise DomainError("BAD_DECISION", "复核状态无效")
    with connect() as db:
        if db.execute("SELECT 1 FROM seals WHERE run_id=?", (run_id,)).fetchone():
            raise DomainError("SEALED", "已封存批次不能再复核", 409)
        cursor = db.execute("UPDATE runs SET version=version+1,decision=? WHERE id=? AND version=?", (decision, run_id, version))
        if cursor.rowcount != 1:
            if not db.execute("SELECT 1 FROM runs WHERE id=?", (run_id,)).fetchone():
                raise DomainError("NOT_FOUND", "批次不存在", 404)
            raise DomainError("STALE_VERSION", "版本已更新，请刷新", 409)
    return get(run_id)


def ingest_receipts(run_id, parsed, raw):
    """回执入账：按 (run_id, receipt_id) 去重；重复重试计 duplicate，不重复计算。

    冲突回执原样保存在 receipt_conflicts，且不复写已采用的首条，便于人工核对。
    """
    raw_sha = keep_raw("receipts", raw)
    accepted = duplicate = 0
    with connect() as db:
        for row in parsed["rows"]:
            previous = db.execute(
                "SELECT fingerprint FROM receipts WHERE run_id=? AND receipt_id=?",
                (run_id, row["receipt_id"]),
            ).fetchone()
            if previous:
                if previous[0] == row["fingerprint"]:
                    duplicate += 1
                    db.execute(
                        "INSERT INTO receipt_duplicates(run_id,receipt_id,fingerprint,seen_at) VALUES (?,?,?,?)",
                        (run_id, row["receipt_id"], row["fingerprint"], _now()),
                    )
                else:
                    conflict = {**row, "reason": "DUPLICATE_CONFLICT", "detail": "同一回执号取值冲突"}
                    db.execute(
                        "INSERT OR IGNORE INTO receipt_conflicts(run_id,receipt_id,fingerprint,payload,raw_sha256,ingested_at) VALUES (?,?,?,?,?,?)",
                        (run_id, row["receipt_id"], row["fingerprint"], json.dumps(conflict, ensure_ascii=False), raw_sha, _now()),
                    )
                continue
            db.execute(
                "INSERT INTO receipts(run_id,receipt_id,payload,fingerprint,raw_sha256,ingested_at) VALUES (?,?,?,?,?,?)",
                (run_id, row["receipt_id"], json.dumps(row, ensure_ascii=False), row["fingerprint"], raw_sha, _now()),
            )
            accepted += 1
        db.execute(
            "INSERT OR IGNORE INTO receipt_files(run_id,sha256,accepted,rejected,ingested_at) VALUES (?,?,?,?,?)",
            (run_id, raw_sha, len(parsed["rows"]), len(parsed["quarantine"]), _now()),
        )
    return {"accepted": accepted, "duplicate": duplicate, "duplicates_total": duplicates_for(run_id), "conflicts": conflicts_for(run_id), "raw_sha256": raw_sha}


def duplicates_for(run_id):
    with connect() as db:
        return db.execute("SELECT COUNT(*) FROM receipt_duplicates WHERE run_id=?", (run_id,)).fetchone()[0]


def conflicts_for(run_id):
    with connect() as db:
        rows = db.execute("SELECT payload FROM receipt_conflicts WHERE run_id=? ORDER BY receipt_id,fingerprint", (run_id,)).fetchall()
    return [json.loads(r[0]) for r in rows]


def resolve_receipt(run_id, receipt_id, decision, note):
    """人工裁决一条异常回执。裁决留痕，回执本身与原始文件仍全部保留。"""
    if decision not in {"已核对", "忽略"}:
        raise DomainError("BAD_DECISION", "裁决只能为已核对或忽略")
    with connect() as db:
        if db.execute("SELECT 1 FROM seals WHERE run_id=?", (run_id,)).fetchone():
            raise DomainError("SEALED", "已封存批次不能再裁决回执", 409)
        known = db.execute("SELECT 1 FROM receipts WHERE run_id=? AND receipt_id=?", (run_id, receipt_id)).fetchone() \
            or db.execute("SELECT 1 FROM receipt_conflicts WHERE run_id=? AND receipt_id=?", (run_id, receipt_id)).fetchone()
        if not known:
            raise DomainError("NOT_FOUND", "回执不存在", 404)
        db.execute(
            "INSERT OR REPLACE INTO receipt_resolutions(run_id,receipt_id,decision,note,resolved_at) VALUES (?,?,?,?,?)",
            (run_id, receipt_id, decision, note or "", _now()),
        )
    return {"receipt_id": receipt_id, "decision": decision, "note": note or ""}


def resolutions_for(run_id):
    with connect() as db:
        return [{"receipt_id": r, "decision": d, "note": n, "resolved_at": t} for r, d, n, t in db.execute(
            "SELECT receipt_id,decision,note,resolved_at FROM receipt_resolutions WHERE run_id=? ORDER BY receipt_id", (run_id,)
        )]


def receipts_for(run_id):
    with connect() as db:
        rows = db.execute("SELECT payload,raw_sha256 FROM receipts WHERE run_id=? ORDER BY ingested_at,receipt_id", (run_id,)).fetchall()
    return [{**json.loads(r[0]), "raw_sha256": r[1]} for r in rows]


def raw_documents_for(run_id):
    """列出本批留档的原始刷卡与银行回执文件摘要。"""
    run = get(run_id)
    shas = [("taps", run["manifest"]["input_sha256"])]
    with connect() as db:
        files = db.execute("SELECT sha256 FROM receipt_files WHERE run_id=? ORDER BY ingested_at,sha256", (run_id,)).fetchall()
    shas.extend(("receipts", row[0]) for row in files)
    out, seen = [], set()
    for kind, sha in shas:
        if sha in seen:
            continue
        seen.add(sha)
        try:
            document = get_raw(sha)
        except DomainError:
            continue
        out.append({"sha256": sha, "kind": document["kind"], "bytes": len(document["content"])})
    return out


def save_recon(run_id, set_sha256, payload):
    """同集合重算幂等复用；新版本只在计算成功后一次写入，失败不触碰旧版。"""
    for attempt in range(2):
        try:
            with connect() as db:
                db.execute("BEGIN IMMEDIATE")
                existing = db.execute("SELECT version,payload FROM recons WHERE run_id=? AND set_sha256=?", (run_id, set_sha256)).fetchone()
                if existing:
                    return {"version": existing[0], **json.loads(existing[1]), "reused": True}
                version = db.execute("SELECT COALESCE(MAX(version),0)+1 FROM recons WHERE run_id=?", (run_id,)).fetchone()[0]
                db.execute(
                    "INSERT INTO recons(run_id,version,set_sha256,payload,created_at) VALUES (?,?,?,?,?)",
                    (run_id, version, set_sha256, json.dumps(payload, ensure_ascii=False), _now()),
                )
            return {"version": version, **payload, "reused": False}
        except sqlite3.IntegrityError:
            if attempt:
                raise DomainError("RECON_BUSY", "对账版本写入冲突，请重试", 409)
    raise DomainError("RECON_BUSY", "对账版本写入冲突，请重试", 409)


def recon_latest(run_id):
    with connect() as db:
        row = db.execute("SELECT version,set_sha256,payload,created_at FROM recons WHERE run_id=? ORDER BY version DESC LIMIT 1", (run_id,)).fetchone()
    if not row:
        raise DomainError("NO_RECON", "该批次尚未收到回执", 404)
    return {"version": row[0], "set_sha256": row[1], **json.loads(row[2]), "created_at": row[3]}


def recon_versions(run_id):
    with connect() as db:
        return [{"version": v, "set_sha256": s, "created_at": c} for v, s, c in db.execute("SELECT version,set_sha256,created_at FROM recons WHERE run_id=? ORDER BY version", (run_id,))]


def log_recon_error(run_id, set_sha256, message):
    with connect() as db:
        db.execute("INSERT INTO recon_errors(run_id,set_sha256,message,created_at) VALUES (?,?,?,?)", (run_id, set_sha256, message, _now()))


def recon_errors(run_id):
    with connect() as db:
        return [{"set_sha256": s, "message": m, "created_at": c} for s, m, c in db.execute("SELECT set_sha256,message,created_at FROM recon_errors WHERE run_id=? ORDER BY id", (run_id,))]


def seal(run_id):
    """未对平不封存日结：封存前强制读取最新一版回执对账。"""
    recon = recon_latest(run_id)
    if not recon["metrics"].get("balanced"):
        raise DomainError("UNBALANCED_RECEIPTS", "回执未对平，不能封存日结", 409)
    records_sha = recon["metrics"]["set_sha256"]
    with connect() as db:
        previous = db.execute("SELECT recon_version,set_sha256 FROM seals WHERE run_id=?", (run_id,)).fetchone()
        if previous:
            return {"run_id": run_id, "recon_version": previous[0], "set_sha256": previous[1], "sealed": True, "reused": True}
        db.execute(
            "INSERT INTO seals(run_id,recon_version,set_sha256,records_sha256,sealed_at) VALUES (?,?,?,?,?)",
            (run_id, recon["version"], records_sha, records_sha, _now()),
        )
    return {"run_id": run_id, "recon_version": recon["version"], "set_sha256": records_sha, "sealed": True, "reused": False}


def seal_status(run_id):
    with connect() as db:
        row = db.execute("SELECT recon_version,set_sha256,sealed_at FROM seals WHERE run_id=?", (run_id,)).fetchone()
    return {"sealed": bool(row), "recon_version": row[0] if row else None, "set_sha256": row[1] if row else None, "sealed_at": row[2] if row else None}
