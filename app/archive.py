import csv, io, json
from app import store
from app.errors import DomainError


def build_archive(run):
    """证据导出：原始刷卡行、隔离行、回执原文、补扣退款清单与封存状态同档。"""
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["run_id", "input_sha256", "rule_version", "kind", "source_lines", "reason", "evidence"])
    meta = run["manifest"]
    run_id = run["run_id"]
    for row in run["stages"][-1]["rows"]:
        writer.writerow([run_id, meta["input_sha256"], meta["rule_version"], "accepted", json.dumps(row.get("_sources", [])), row.get("status", ""), json.dumps(row, ensure_ascii=False)])
    for row in run["stages"][-1].get("cumulative_quarantine", []):
        writer.writerow([run_id, meta["input_sha256"], meta["rule_version"], "quarantine", json.dumps(row.get("_sources", [])), row.get("reason", ""), json.dumps(row, ensure_ascii=False)])
    try:
        recon = store.recon_latest(run_id)
    except DomainError:
        recon = None
    for row in (recon["rows"] if recon else []):
        writer.writerow([run_id, meta["input_sha256"], meta["rule_version"], "recon", json.dumps([row.get("event_id", "")]), row.get("action", ""), json.dumps(row, ensure_ascii=False)])
    for row in (recon["receipt_quarantine"] if recon else []):
        writer.writerow([run_id, meta["input_sha256"], meta["rule_version"], "receipt_quarantine", json.dumps(row.get("_sources", [])), row.get("reason", ""), json.dumps(row, ensure_ascii=False)])
    for row in store.receipts_for(run_id):
        writer.writerow([run_id, meta["input_sha256"], meta["rule_version"], "receipt", json.dumps(row.get("_sources", [])), "", json.dumps(row, ensure_ascii=False)])
    seal = store.seal_status(run_id)
    for document in store.raw_documents_for(run_id):
        writer.writerow([run_id, meta["input_sha256"], meta["rule_version"], "raw_document", "[]", document["kind"], json.dumps(document, ensure_ascii=False)])
    writer.writerow([run_id, meta["input_sha256"], meta["rule_version"], "seal", "[]", "已封存" if seal["sealed"] else "未封存", json.dumps(seal, ensure_ascii=False, default=str)])
    return "﻿" + stream.getvalue()
