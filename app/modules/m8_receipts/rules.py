import csv, hashlib, io, json
from datetime import datetime, timezone
from decimal import Decimal

from app.config import RECEIPT_COLUMNS, RECEIPT_REQUIRED, RECON_RULE_VERSION, BENEFIT_POLICY, BENEFIT_POLICY_LABEL
from app.errors import DomainError
from app.reasons import reject
from app.values import instant, money, number
from app.modules.m1_taps.rules import decode
from app.modules.m5_discounts.rules import allocate

TRUE_WORDS = {"1", "true", "yes", "y", "是", "final", "finalized"}
FALSE_WORDS = {"0", "false", "no", "n", "否", "partial", ""}


def _flag(value):
    text = str(value).strip().lower()
    if text in TRUE_WORDS:
        return True
    if text in FALSE_WORDS:
        return False
    raise DomainError("BAD_FINAL", "final 只能表示终结或部分")


def parse_receipts(raw):
    """解析银行回执。同一 receipt_id 只保留首次到达值，重复重试不重复入账。"""
    text, encoding = decode(raw)
    if not text:
        raise DomainError("EMPTY_FILE", "回执文件为空")
    if not text.strip():
        raise DomainError("BLANK_ONLY", "回执全部为空行")
    stream = csv.reader(io.StringIO(text, newline=""))
    header = next(stream, None)
    if header != RECEIPT_COLUMNS:
        raise DomainError("BAD_HEADER", "回执表头不匹配")
    rows, bad, seen = [], [], {}
    duplicate = blank = repeated = count = 0
    try:
        for cells in stream:
            count += 1
            line = stream.line_num
            if not cells or not any(c.strip() for c in cells):
                blank += 1
                continue
            if cells == header:
                repeated += 1
                continue
            base = {"_line": line, "_sources": [line], "_raw": cells, "_kind": "receipt"}
            if len(cells) != len(header):
                bad.append(reject(base, "BAD_COLUMNS", "回执列数不匹配"))
                continue
            row = {**base, **dict(zip(header, [x.strip() for x in cells]))}
            try:
                for key in RECEIPT_REQUIRED:
                    if not row.get(key):
                        raise DomainError("EMPTY_VALUE", f"{key} 为空")
                amount = number(row["amount"])
                if amount < 0:
                    raise DomainError("NEGATIVE_FARE", "回执金额不能为负")
                received = instant(row["received_at"])
                final = _flag(row.get("final", ""))
            except DomainError as error:
                bad.append(reject(row, error.code, error.message))
                continue
            row.update(amount=money(amount), received_at=received.isoformat(), final=final)
            fingerprint = json.dumps(
                [row["event_id"], row.get("card", ""), row["amount"], row["received_at"], final],
                ensure_ascii=False,
            )
            previous = seen.get(row["receipt_id"])
            if previous:
                if fingerprint == previous["fingerprint"]:
                    duplicate += 1
                else:
                    bad.append(reject(row, "DUPLICATE_CONFLICT", "同一回执号取值冲突"))
                continue
            row["fingerprint"] = fingerprint
            seen[row["receipt_id"]] = row
            rows.append(row)
    except csv.Error:
        raise DomainError("BAD_CSV", "回执引号未闭合或文件损坏")
    if count == 0:
        raise DomainError("HEADER_ONLY", "回执仅有表头")
    if not rows and not bad:
        raise DomainError("BLANK_ONLY", "回执全部为空行")
    return {
        "rows": rows,
        "quarantine": bad,
        "metrics": {
            "receipts": len(rows),
            "quarantine": len(bad),
            "duplicate": duplicate,
            "blank": blank,
            "repeated_headers": repeated,
            "input": count,
            "encoding": encoding,
        },
    }


def confirmations(receipts):
    """按终结回执确定每程已落账金额。

    回执可拆分多条：终结回执及其之前到达的同程回执求和即已确认金额；
    终结回执之后到达的回执不重复计入，单独标记待人工核对。
    """
    groups = {}
    for row in receipts:
        groups.setdefault(row["event_id"], []).append(row)
    result = {}
    for event_id, items in groups.items():
        ordered = sorted(items, key=lambda r: (r["received_at"], r["receipt_id"]))
        closing = next((i for i, r in enumerate(ordered) if r.get("final")), None)
        if closing is None:
            result[event_id] = {
                "confirmed": False,
                "amount": sum((number(r["amount"]) for r in ordered), Decimal(0)),
                "receipts": ordered,
                "late": [],
            }
            continue
        result[event_id] = {
            "confirmed": True,
            "amount": sum((number(r["amount"]) for r in ordered[:closing + 1]), Decimal(0)),
            "receipts": ordered[:closing + 1],
            "late": ordered[closing + 1:],
        }
    return result


def _base(journey):
    return {
        "event_id": journey["event_id"],
        "journey": journey.get("journey", ""),
        "card": journey["card"],
        "business_day": journey.get("business_day", ""),
        "route": journey.get("route", ""),
        "at": journey.get("at", ""),
        "base_fare": journey.get("base_fare", "0.00"),
    }


def _pending(journey, item, note):
    pending = item is None or not item["confirmed"]
    receipts = [] if item is None else item["receipts"]
    return {
        **_base(journey),
        "transfer_anchor": "—",
        "transfer_discount": "0.00",
        "cap_discount": "0.00",
        "expected": "—",
        "accounted": money(sum((number(r["amount"]) for r in receipts), Decimal(0))),
        "receipt_status": "待回执" if pending else "已确认",
        "receipt_ids": ",".join(r["receipt_id"] for r in receipts),
        "action": "待回执" if pending else "不变",
        "amount": "0.00",
        "note": note,
    }


def _settled(journey, item, allocated):
    expected = number(allocated["expected"])
    accounted = item["amount"]
    delta = expected - accounted
    action = "不变" if delta == 0 else ("补扣" if delta > 0 else "退款")
    return {
        **_base(journey),
        "transfer_anchor": allocated["transfer_anchor"],
        "transfer_discount": allocated["transfer_discount"],
        "cap_discount": allocated["cap_discount"],
        "expected": allocated["expected"],
        "accounted": money(accounted),
        "receipt_status": "已确认",
        "receipt_ids": ",".join(r["receipt_id"] for r in item["receipts"]),
        "action": action,
        "amount": money(abs(delta)),
        "note": "重复回执重试不再补扣或退款" if action != "不变" else "",
    }


def reconcile(journeys, receipts, upstream_quarantine=None, params=None,
              duplicate_receipts=0, conflicts=None, set_sha256=None, resolved=None):
    """以已确认行程为唯一权益集合重算，再按程生成补扣/退款/不变清单。

    已知未封存批次可重复调用：结果只由行程、回执集合、参数和规则版本决定，
    因此同一回执重试不会产生第二笔补扣或退款。人工已裁决的回执号（resolved）
    仍完整留档，但不再阻塞封存。
    """
    params = params or {}
    conflicts = list(conflicts or [])
    resolved = set(resolved or ())
    upstream = list(upstream_quarantine or [])
    receipt_issues = []
    index = {}
    for journey in journeys:
        index.setdefault(journey["event_id"], journey)
    valid, unmatched, dismissed = [], [], 0
    for row in receipts:
        journey = index.get(row["event_id"])
        if not journey:
            if row["receipt_id"] in resolved:
                dismissed += 1
            else:
                unmatched.append(reject(row, "UNKNOWN_JOURNEY", "回执找不到对应行程"))
            continue
        if row.get("card") and row["card"] != journey.get("card"):
            if row["receipt_id"] in resolved:
                dismissed += 1
            else:
                receipt_issues.append(reject(row, "RECEIPT_CARD_MISMATCH", "回执卡号与行程不一致"))
            continue
        valid.append(row)

    confirmed = confirmations(valid)
    confirmed_set = [
        journey for journey in journeys
        if confirmed.get(journey["event_id"], {}).get("confirmed")
    ]
    allocated, spent = allocate(confirmed_set, params)
    by_id = {row["event_id"]: row for row in allocated}

    rows, late = [], []
    for journey in sorted(journeys, key=lambda r: (r["card"], r.get("at", ""), r["event_id"])):
        item = confirmed.get(journey["event_id"])
        if item is None:
            rows.append(_pending(journey, None, "回执未到，未占封顶与换乘额度"))
            continue
        for extra in item["late"]:
            flagged = reject(extra, "RECEIPT_AFTER_FINAL", "终结回执之后到达，未重复计入")
            if extra["receipt_id"] in resolved:
                dismissed += 1
            else:
                late.append(flagged)
                receipt_issues.append(flagged)
        if not item["confirmed"]:
            rows.append(_pending(journey, item, "回执未齐，未占封顶与换乘额度"))
            continue
        rows.append(_settled(journey, item, by_id[journey["event_id"]]))

    charge = [r for r in rows if r["action"] == "补扣"]
    refund = [r for r in rows if r["action"] == "退款"]
    unchanged = [r for r in rows if r["action"] == "不变"]
    pending = [r for r in rows if r["action"] == "待回执"]
    open_conflicts = [row for row in conflicts if row.get("receipt_id") not in resolved]
    dismissed += len(conflicts) - len(open_conflicts)
    receipt_quarantine = receipt_issues + unmatched + open_conflicts
    balanced = not pending and not receipt_quarantine
    settled = [r for r in rows if r["expected"] != "—"]
    expected_total = sum((number(r["expected"]) for r in settled), Decimal(0))
    base_total = sum((number(r["base_fare"]) for r in settled), Decimal(0))
    transfer_total = sum((number(r["transfer_discount"]) for r in settled), Decimal(0))
    cap_total = sum((number(r["cap_discount"]) for r in settled), Decimal(0))
    discount_total = transfer_total + cap_total
    accounted_settled = sum((number(r["accounted"]) for r in settled), Decimal(0))
    received_total = sum((number(r["accounted"]) for r in rows), Decimal(0))
    charge_total = sum((number(r["amount"]) for r in charge), Decimal(0))
    refund_total = sum((number(r["amount"]) for r in refund), Decimal(0))
    if money(base_total) != money(expected_total + discount_total):
        raise DomainError("RECON_UNBALANCED", "基础票价与应扣、减免合计不守恒")
    if money(accounted_settled + charge_total - refund_total) != money(expected_total):
        raise DomainError("RECON_UNBALANCED", "到账、补扣、退款与应扣合计不守恒")
    metrics = {
        "journeys": len(journeys),
        "confirmed": len(confirmed_set),
        "pending": len(pending),
        "unmatched": len(unmatched),
        "conflicts": len(open_conflicts),
        "late_ignored": len(late),
        "resolved": dismissed,
        "duplicate_receipts": duplicate_receipts,
        "receipts": len(valid),
        "expected_total": money(expected_total),
        "accounted_total": money(received_total),
        "settled_accounted": money(accounted_settled),
        "pending_received": money(received_total - accounted_settled),
        "base_total": money(base_total),
        "transfer_discount_total": money(transfer_total),
        "cap_discount_total": money(cap_total),
        "discount_total": money(discount_total),
        "charge_count": len(charge),
        "charge_total": money(charge_total),
        "refund_count": len(refund),
        "refund_total": money(refund_total),
        "unchanged": len(unchanged),
        "daily_caps": len(spent),
        "balanced": balanced,
        "seal_ready": balanced,
        "benefit_policy": BENEFIT_POLICY,
        "benefit_policy_label": BENEFIT_POLICY_LABEL[BENEFIT_POLICY],
        "set_sha256": set_sha256 or _set_sha(valid, params, open_conflicts, sorted(resolved)),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    return {
        "rows": rows,
        "unmatched": unmatched,
        "conflicts": open_conflicts,
        "late": late,
        "quarantine": upstream + receipt_quarantine,
        "receipt_quarantine": receipt_quarantine,
        "receipts": valid,
        "metrics": metrics,
    }


def set_digest(receipts, params=None, conflicts=None, resolved=None):
    """回执集合摘要：决定重算是否同一版本，重复重试得到同一摘要。"""
    return _set_sha(receipts, params or {}, list(conflicts or []), list(resolved or []))


def _set_sha(receipts, params, conflicts, resolved=None):
    payload = {
        "rule": RECON_RULE_VERSION,
        "policy": BENEFIT_POLICY,
        "parameters": params,
        "receipts": sorted(
            ([r["receipt_id"], r["event_id"], r.get("card", ""), r["amount"], r["received_at"], r["final"]]
             for r in receipts),
            key=lambda x: x[0],
        ),
        "conflicts": sorted(str(r.get("receipt_id", "")) for r in conflicts),
        "resolved": sorted(str(x) for x in (resolved or [])),
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
