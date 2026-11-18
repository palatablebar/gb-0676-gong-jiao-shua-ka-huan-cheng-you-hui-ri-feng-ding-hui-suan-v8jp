import csv
import io
import json

from app import store
from app.errors import DomainError
from app.modules.m8_receipts import rules as r
from app.pipeline import reconcile_run


def full_receipts(run):
    header = "receipt_id,event_id,card,amount,received_at,final\n"
    body = "".join(
        f"B-{j['event_id']},{j['event_id']},{j['card']},2.50,2026-11-17T08:00:00+08:00,true\n"
        for j in run["stages"][3]["rows"]
    )
    return header + body


def create_run(client, sample="dirty.csv"):
    response = client.post("/api/runs", json={"sample": sample})
    assert response.status_code == 200
    return response.get_json()


def test_receipts_before_any_receipt(client):
    run = create_run(client)
    body = client.get(f"/api/runs/{run['run_id']}/receipts").get_json()
    assert body["receipts"] == [] and body["recon"] is None
    assert body["seal"]["sealed"] is False


def test_ingest_receipts_and_recon(client):
    run = create_run(client, "clean.csv")
    response = client.post(f"/api/runs/{run['run_id']}/receipts", json={"csv": full_receipts(run)})
    assert response.status_code == 200
    body = response.get_json()
    assert body["metrics"]["balanced"] is True and body["metrics"]["confirmed"] == 32
    detail = client.get(f"/api/runs/{run['run_id']}/recon").get_json()
    assert detail["version"] == 1 and len(detail["rows"]) == 32


def test_upload_receipts_multipart(client):
    run = create_run(client)
    data = {"file": (io.BytesIO(open("sample_data/receipts_partial.csv", "rb").read()), "receipts_partial.csv")}
    response = client.post(f"/api/runs/{run['run_id']}/receipts", data=data, content_type="multipart/form-data")
    assert response.status_code == 200
    metrics = response.get_json()["metrics"]
    assert metrics["confirmed"] == 3 and metrics["pending"] == 29


def test_seal_blocked_until_balanced(client):
    run = create_run(client)
    client.post(f"/api/runs/{run['run_id']}/receipts", json={"csv": full_receipts(run)})
    assert client.post(f"/api/runs/{run['run_id']}/seal").status_code == 200
    status = client.get(f"/api/runs/{run['run_id']}/seal").get_json()
    assert status["sealed"] is True and status["recon_version"] == 1


def test_seal_blocked_with_pending(client):
    run = create_run(client)
    client.post(f"/api/runs/{run['run_id']}/receipts", json={"csv": "receipt_id,event_id,card,amount,received_at,final\nB-0010,0010,0001,2.50,2026-11-17T08:00:00+08:00,true\n"})
    response = client.post(f"/api/runs/{run['run_id']}/seal")
    assert response.status_code == 409 and response.get_json()["code"] == "UNBALANCED_RECEIPTS"


def test_sealed_run_rejects_new_receipts_and_review(client):
    run = create_run(client)
    client.post(f"/api/runs/{run['run_id']}/receipts", json={"csv": full_receipts(run)})
    client.post(f"/api/runs/{run['run_id']}/seal")
    late = "receipt_id,event_id,card,amount,received_at,final\nX,0010,0001,0.10,2026-11-19T08:00:00+08:00,false\n"
    assert client.post(f"/api/runs/{run['run_id']}/receipts", json={"csv": late}).status_code == 409
    assert client.post(f"/api/runs/{run['run_id']}/review", json={"version": 1, "decision": "已复核"}).status_code == 409


def test_archive_contains_receipts_and_recon(client):
    run = create_run(client, "clean.csv")
    client.post(f"/api/runs/{run['run_id']}/receipts", json={"csv": full_receipts(run)})
    data = client.get(f"/api/runs/{run['run_id']}/archive").data.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(data)))
    kinds = {row[3] for row in rows[1:]}
    assert {"accepted", "recon", "receipt", "seal"} <= kinds
    recon_rows = [row for row in rows[1:] if row[3] == "recon"]
    assert len(recon_rows) == 32
    assert all(json.loads(row[6])["action"] in {"补扣", "退款", "不变"} for row in recon_rows)


def test_raw_documents_export(client):
    run = create_run(client)
    raw = open("sample_data/receipts_partial.csv", "rb").read()
    body = client.post(f"/api/runs/{run['run_id']}/receipts", data={"file": (io.BytesIO(raw), "p.csv")}, content_type="multipart/form-data").get_json()
    sha = body["raw_sha256"]
    response = client.get(f"/api/runs/{run['run_id']}/raw/{sha}")
    assert response.status_code == 200 and response.data == raw
    taps = store.keep_raw("taps", b"0001,1")
    assert client.get(f"/api/runs/{run['run_id']}/raw/{taps}").data == b"0001,1"


def test_receipts_page_shows_actions(client):
    run = create_run(client, "clean.csv")
    client.post(f"/api/runs/{run['run_id']}/receipts", json={"csv": full_receipts(run)})
    page = client.get("/receipts")
    assert page.status_code == 200
    text = page.get_data(as_text=True)
    assert "补扣" in text and "退款" in text and "封存" in text


def test_resolve_receipt_endpoint(client):
    run = create_run(client)
    client.post(f"/api/runs/{run['run_id']}/receipts", json={"csv": "receipt_id,event_id,card,amount,received_at,final\nB-X,9999,0001,1.00,2026-11-17T08:00:00+08:00,true\n"})
    response = client.post(f"/api/runs/{run['run_id']}/receipts/B-X/resolve", json={"decision": "忽略", "note": "非本批"})
    assert response.status_code == 200
    assert response.get_json()["metrics"]["unmatched"] == 0
    assert store.resolutions_for(run["run_id"])[0]["decision"] == "忽略"


def test_recon_versions_are_monotonic(client):
    run = create_run(client)
    first = client.post(f"/api/runs/{run['run_id']}/receipts", json={"csv": "receipt_id,event_id,card,amount,received_at,final\nB-0010,0010,0001,2.50,2026-11-17T08:00:00+08:00,true\n"}).get_json()
    second = client.post(f"/api/runs/{run['run_id']}/receipts", json={"csv": "receipt_id,event_id,card,amount,received_at,final\nB-0011,0011,0001,2.50,2026-11-17T08:01:00+08:00,true\n"}).get_json()
    assert first["version"] == 1 and second["version"] == 2
    versions = client.get(f"/api/runs/{run['run_id']}/recon/versions").get_json()
    assert [v["version"] for v in versions["versions"]] == [1, 2]


def test_parse_error_keeps_old_recon(client):
    run = create_run(client)
    client.post(f"/api/runs/{run['run_id']}/receipts", json={"csv": "receipt_id,event_id,card,amount,received_at,final\nB-0010,0010,0001,2.50,2026-11-17T08:00:00+08:00,true\n"})
    before = client.get(f"/api/runs/{run['run_id']}/recon").get_json()
    bad = client.post(f"/api/runs/{run['run_id']}/receipts", json={"csv": "receipt_id,event_id\n"})
    assert bad.status_code == 422
    after = client.get(f"/api/runs/{run['run_id']}/recon").get_json()
    assert after["version"] == before["version"] and after["metrics"] == before["metrics"]


def test_conservation_failure_keeps_old_version(client, monkeypatch):
    run = create_run(client)
    client.post(f"/api/runs/{run['run_id']}/receipts", json={"csv": "receipt_id,event_id,card,amount,received_at,final\nB-0010,0010,0001,2.50,2026-11-17T08:00:00+08:00,true\n"})
    before = client.get(f"/api/runs/{run['run_id']}/recon").get_json()
    original = r.allocate

    def corrupt(*args, **kwargs):
        rows, spent = original(*args, **kwargs)
        for row in rows:
            row["expected"] = "9.99"
        return rows, spent

    monkeypatch.setattr(r, "allocate", corrupt)
    response = client.post(f"/api/runs/{run['run_id']}/receipts", json={"csv": "receipt_id,event_id,card,amount,received_at,final\nB-0011,0011,0001,2.50,2026-11-17T08:01:00+08:00,true\n"})
    assert response.status_code == 422
    after = client.get(f"/api/runs/{run['run_id']}/recon").get_json()
    versions = client.get(f"/api/runs/{run['run_id']}/recon/versions").get_json()
    assert after["version"] == before["version"] and after["metrics"] == before["metrics"]
    assert versions["errors"] and "RECON_UNBALANCED" in versions["errors"][0]["message"]


def test_conflict_receipt_blocks_until_resolved(client):
    run = create_run(client)
    client.post(f"/api/runs/{run['run_id']}/receipts", json={"csv": "receipt_id,event_id,card,amount,received_at,final\nB-0011,0011,0001,2.50,2026-11-17T08:01:00+08:00,true\n"})
    clash = client.post(f"/api/runs/{run['run_id']}/receipts", json={"csv": "receipt_id,event_id,card,amount,received_at,final\nB-0011,0011,0001,1.50,2026-11-17T08:01:00+08:00,true\n"}).get_json()
    assert clash["metrics"]["conflicts"] == 1 and clash["metrics"]["seal_ready"] is False
    assert len(store.receipts_for(run["run_id"])) == 1
    resolved = client.post(f"/api/runs/{run['run_id']}/receipts/B-0011/resolve", json={"decision": "已核对", "note": "以首条为准"}).get_json()
    assert resolved["metrics"]["conflicts"] == 0
    assert store.conflicts_for(run["run_id"])
