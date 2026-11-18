import pytest

from app import store
from app.errors import DomainError
from app.modules.m8_receipts import rules as r
from app.pipeline import execute, load_sample, reconcile_run

HEADER = b"receipt_id,event_id,card,amount,received_at,final\n"


def receipt(id, event, amount, at="2026-11-17T08:00:00+08:00", final="true", card="0001"):
    return f"{id},{event},{card},{amount},{at},{final}\n".encode()


@pytest.fixture
def run(sample):
    return execute(sample)


def test_parse_receipts_dedupes_same_id():
    raw = HEADER + receipt("B1", "0010", "1.00") + receipt("B1", "0010", "1.00")
    parsed = r.parse_receipts(raw)
    assert len(parsed["rows"]) == 1 and parsed["metrics"]["duplicate"] == 1


def test_parse_receipts_conflict_quarantined():
    raw = HEADER + receipt("B1", "0010", "1.00") + receipt("B1", "0010", "2.00")
    parsed = r.parse_receipts(raw)
    assert len(parsed["rows"]) == 1
    assert parsed["quarantine"][0]["reason"] == "DUPLICATE_CONFLICT"


def test_split_receipts_sum_until_final():
    raw = HEADER + receipt("A", "0010", "1.50", final="false") + receipt("B", "0010", "1.00", "2026-11-17T08:05:00+08:00")
    items = r.parse_receipts(raw)["rows"]
    confirmed = r.confirmations(items)
    assert confirmed["0010"]["confirmed"] is True
    assert confirmed["0010"]["amount"] == r.number("2.50")


def test_receipts_without_final_are_pending():
    items = r.parse_receipts(HEADER + receipt("A", "0010", "1.50", final="false"))["rows"]
    assert r.confirmations(items)["0010"]["confirmed"] is False


def test_late_receipt_not_double_counted():
    raw = HEADER + receipt("A", "0010", "2.50") + receipt("LATE", "0010", "0.50", "2026-11-18T08:00:00+08:00", "false")
    items = r.parse_receipts(raw)["rows"]
    confirmed = r.confirmations(items)["0010"]
    assert confirmed["amount"] == r.number("2.50") and len(confirmed["late"]) == 1


def test_pending_journey_does_not_consume_cap(run):
    """未回执行程不提前占额：只给 0011 回执时，它仍按当日首程计费。"""
    out = reconcile_run(run["run_id"], HEADER + receipt("B-0011", "0011", "2.50"))
    row = next(x for x in out["rows"] if x["event_id"] == "0011")
    assert row["expected"] == "2.50" and row["transfer_discount"] == "0.00"
    assert out["metrics"]["pending"] == 31 and out["metrics"]["seal_ready"] is False


def test_confirmed_journey_gets_transfer_after_receipt(run):
    raw = HEADER + receipt("B-0010", "0010", "2.50", "2026-11-17T08:00:00+08:00") + receipt("B-0011", "0011", "2.50", "2026-11-17T08:01:00+08:00")
    out = reconcile_run(run["run_id"], raw)
    row = next(x for x in out["rows"] if x["event_id"] == "0011")
    assert row["expected"] == "1.50" and row["transfer_discount"] == "1.00"
    assert row["action"] == "退款" and row["amount"] == "1.00"


def test_full_receipts_balance_and_refund_list(run):
    out = reconcile_run(run["run_id"], HEADER + b"".join(
        receipt(f"B-{j['event_id']}", j["event_id"], "2.50", card=j["card"]) for j in run["stages"][3]["rows"]
    ))
    m = out["metrics"]
    assert m["balanced"] is True and m["seal_ready"] is True
    assert m["accounted_total"] == "80.00" and m["refund_total"] == "32.00"
    assert all(x["action"] in {"补扣", "退款", "不变"} for x in out["rows"])


def test_repeated_receipt_does_not_recharge(run):
    raw = HEADER + receipt("B-0010", "0010", "2.50")
    first = reconcile_run(run["run_id"], raw)
    second = reconcile_run(run["run_id"], raw)
    assert second["version"] == first["version"]
    assert second["metrics"]["charge_total"] == first["metrics"]["charge_total"]
    assert second["intake"]["duplicate"] == 1 and second["reused"] is True
    assert store.duplicates_for(run["run_id"]) == 1
    assert len(store.recon_versions(run["run_id"])) == 1


def test_recompute_failure_keeps_previous_version(run, monkeypatch):
    reconcile_run(run["run_id"], HEADER + receipt("B-0010", "0010", "2.50"))

    def explode(*args, **kwargs):
        raise DomainError("RECON_FAILED", "重算失败")

    monkeypatch.setattr(r, "reconcile", explode)
    with pytest.raises(DomainError):
        reconcile_run(run["run_id"], HEADER + receipt("B-0011", "0011", "2.50"))
    latest = store.recon_latest(run["run_id"])
    assert latest["version"] == 1 and latest["metrics"]["confirmed"] == 1
    assert store.recon_errors(run["run_id"])


def test_raw_documents_archived(run):
    raw = HEADER + receipt("B-0010", "0010", "2.50")
    out = reconcile_run(run["run_id"], raw)
    assert store.get_raw(out["raw_sha256"])["content"] == raw
    taps = store.keep_raw("taps", load_sample("dirty.csv"))
    assert store.get_raw(taps)["kind"] == "taps"


def test_unmatched_receipt_blocks_seal(run):
    out = reconcile_run(run["run_id"], HEADER + receipt("B-9999", "9999", "2.50"))
    assert out["metrics"]["unmatched"] == 1 and out["metrics"]["seal_ready"] is False


def test_resolved_exception_unblocks_seal(run):
    reconcile_run(run["run_id"], HEADER + receipt("B-9999", "9999", "2.50"))
    store.resolve_receipt(run["run_id"], "B-9999", "忽略", "非本批行程")
    from app.modules.m8_receipts.service import reconcile_existing
    out = reconcile_existing(run["run_id"])
    assert out["metrics"]["unmatched"] == 0 and out["metrics"]["resolved"] == 1
    assert out["receipt_quarantine"] == []
    raw = HEADER + b"".join(receipt(f"B-{j['event_id']}", j["event_id"], "2.50", card=j["card"]) for j in run["stages"][3]["rows"])
    balanced = reconcile_run(run["run_id"], raw)
    assert balanced["metrics"]["seal_ready"] is True


def test_settlement_conservation(run):
    raw = HEADER + b"".join(receipt(f"B-{j['event_id']}", j["event_id"], "2.50", card=j["card"]) for j in run["stages"][3]["rows"])
    out = reconcile_run(run["run_id"], raw)
    total = sum((r.number(x["expected"]) for x in out["rows"]), r.Decimal(0))
    assert r.money(total) == out["metrics"]["expected_total"] == run["stages"][4]["metrics"]["expected_total"]
