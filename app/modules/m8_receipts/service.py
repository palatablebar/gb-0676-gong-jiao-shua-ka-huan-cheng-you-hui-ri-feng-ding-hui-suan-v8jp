from app import store
from app.errors import DomainError
from app.modules.m8_receipts import rules as receipts


def reconcile_existing(run_id, duplicate_receipts=None):
    """按当前已保存的回执与人工裁决重算；不引入新回执。"""
    run = store.get(run_id)
    if store.seal_status(run_id)["sealed"]:
        raise DomainError("SEALED", "已封存批次不能再重算", 409)
    journeys = run["stages"][3]["rows"]
    all_receipts = store.receipts_for(run_id)
    conflicts = store.conflicts_for(run_id)
    resolved = [r["receipt_id"] for r in store.resolutions_for(run_id)]
    set_sha = receipts.set_digest(all_receipts, run.get("parameters", {}), conflicts, resolved)
    if duplicate_receipts is None:
        duplicate_receipts = store.duplicates_for(run_id)
    try:
        result = receipts.reconcile(
            journeys,
            all_receipts,
            params=run.get("parameters", {}),
            duplicate_receipts=duplicate_receipts,
            conflicts=conflicts,
            resolved=resolved,
            set_sha256=set_sha,
        )
    except DomainError as error:
        store.log_recon_error(run_id, set_sha, f"{error.code}: {error.message}")
        raise
    return store.save_recon(run_id, set_sha, result)
