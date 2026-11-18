from flask import Blueprint, Response, request

from app import pipeline, store
from app.config import RECEIPT_COLUMNS
from app.errors import DomainError

bp = Blueprint("receipts", __name__)


@bp.route("/api/receipts", methods=["GET"])
def receipts_help():
    return {
        "columns": RECEIPT_COLUMNS,
        "template": pipeline.receipt_template(),
        "policy": "未终结回执之前，行程不参与换乘与封顶计算；终结回执到达后按已确认集合重算。",
    }


@bp.route("/api/runs/<run_id>/receipts", methods=["GET"])
def receipts_list(run_id):
    run = store.get(run_id)
    payload = {
        "receipts": store.receipts_for(run_id),
        "conflicts": store.conflicts_for(run_id),
        "resolutions": store.resolutions_for(run_id),
        "seal": store.seal_status(run_id),
        "raw_documents": store.raw_documents_for(run_id),
        "intake": run["stages"][5]["metrics"],
    }
    try:
        payload["recon"] = store.recon_latest(run_id)
    except DomainError:
        payload["recon"] = None
    return payload


@bp.route("/api/runs/<run_id>/receipts", methods=["POST"])
def receipts_ingest(run_id):
    store.get(run_id)
    upload = request.files.get("file")
    if upload is not None:
        raw = upload.read()
    else:
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            raise DomainError("BAD_BODY", "请求需要对象")
        if body.get("sample") is not None:
            raw = pipeline.load_receipt_sample(body["sample"])
        else:
            text = body.get("csv")
            if not isinstance(text, str):
                raise DomainError("BAD_CSV", "回执需要文本")
            raw = text.encode()
    return pipeline.reconcile_run(run_id, raw)


@bp.route("/api/runs/<run_id>/receipts/<receipt_id>/resolve", methods=["POST"])
def resolve(run_id, receipt_id):
    store.get(run_id)
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise DomainError("BAD_BODY", "请求需要对象")
    store.resolve_receipt(run_id, receipt_id, body.get("decision"), body.get("note", ""))
    from app.modules.m8_receipts.service import reconcile_existing
    return reconcile_existing(run_id)


@bp.route("/api/runs/<run_id>/recon", methods=["GET"])
def recon_latest(run_id):
    return store.recon_latest(run_id)


@bp.route("/api/runs/<run_id>/recon/versions", methods=["GET"])
def recon_versions(run_id):
    store.get(run_id)
    return {"versions": store.recon_versions(run_id), "errors": store.recon_errors(run_id)}


@bp.route("/api/runs/<run_id>/seal", methods=["POST"])
def seal(run_id):
    store.get(run_id)
    return store.seal(run_id)


@bp.route("/api/runs/<run_id>/seal", methods=["GET"])
def seal_status(run_id):
    store.get(run_id)
    return store.seal_status(run_id)


@bp.route("/api/runs/<run_id>/raw/<sha256>", methods=["GET"])
def raw_document(run_id, sha256):
    store.get(run_id)
    document = store.get_raw(sha256)
    return Response(
        document["content"],
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{sha256}.csv"'},
    )
