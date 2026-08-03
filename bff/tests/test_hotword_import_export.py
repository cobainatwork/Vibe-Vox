"""#3 Hotword 匯入匯出：BFF HTTP seam。欄位契約固定為 term/note/enabled。"""

import json

from fastapi.testclient import TestClient

from vibe_vox.config import Settings
from vibe_vox.main import create_app


def _client(tmp_path, **settings) -> TestClient:
    return TestClient(create_app(settings=Settings(db_path=tmp_path / "t.db", **settings)))


def test_export_json_returns_field_contract(tmp_path):
    client = _client(tmp_path)
    client.post("/api/admin/hotwords", json={"term": "台積電", "note": "半導體"})
    disabled = client.post("/api/admin/hotwords", json={"term": "停用"}).json()["data"]
    client.patch(f"/api/admin/hotwords/{disabled['id']}/enabled", json={"enabled": False})

    resp = client.get("/api/admin/hotwords/export?format=json")

    assert resp.status_code == 200
    rows = resp.json()
    assert {"term": "台積電", "note": "半導體", "enabled": True} in rows
    assert {"term": "停用", "note": None, "enabled": False} in rows
    assert all(set(r.keys()) == {"term", "note", "enabled"} for r in rows)


def test_export_csv_has_header_and_rows(tmp_path):
    client = _client(tmp_path)
    client.post("/api/admin/hotwords", json={"term": "台積電", "note": "半導體"})

    resp = client.get("/api/admin/hotwords/export?format=csv")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    lines = resp.text.splitlines()
    assert lines[0] == "term,note,enabled"
    assert any(row.startswith("台積電,半導體,true") for row in lines[1:])


def test_import_json_upserts_by_term(tmp_path):
    client = _client(tmp_path)
    existing = client.post(
        "/api/admin/hotwords", json={"term": "台積電", "note": "舊註"}
    ).json()["data"]

    payload = json.dumps(
        [
            {"term": "台積電", "note": "新註", "enabled": False},  # 更新既有
            {"term": "聯發科"},  # 新增，enabled 缺省 true
        ]
    )
    resp = client.post(
        "/api/admin/hotwords/import?format=json",
        files={"file": ("hotwords.json", payload, "application/json")},
    )

    assert resp.status_code == 200
    assert resp.json()["data"] == {"created": 1, "updated": 1}

    by_term = {r["term"]: r for r in client.get("/api/admin/hotwords").json()["data"]}
    assert by_term["台積電"]["id"] == existing["id"]  # 同列更新，非新增
    assert by_term["台積電"]["note"] == "新註"
    assert by_term["台積電"]["enabled"] is False
    assert by_term["聯發科"]["enabled"] is True  # 缺省 true


def test_import_csv_upserts_with_enabled_default_true(tmp_path):
    client = _client(tmp_path)
    csv_content = "term,note,enabled\n台積電,半導體,false\n聯發科,,\n"

    resp = client.post(
        "/api/admin/hotwords/import?format=csv",
        files={"file": ("hotwords.csv", csv_content, "text/csv")},
    )

    assert resp.status_code == 200
    assert resp.json()["data"] == {"created": 2, "updated": 0}

    by_term = {r["term"]: r for r in client.get("/api/admin/hotwords").json()["data"]}
    assert by_term["台積電"]["enabled"] is False
    assert by_term["聯發科"]["enabled"] is True  # enabled 欄位空 → 缺省 true


def test_import_rejects_oversized_file(tmp_path):
    client = _client(tmp_path, hotword_import_max_bytes=50)
    payload = json.dumps([{"term": "x" * 100}])

    resp = client.post(
        "/api/admin/hotwords/import?format=json",
        files={"file": ("h.json", payload, "application/json")},
    )

    assert resp.status_code == 413
    assert resp.json()["error"]["code"]


def test_import_rejects_too_many_rows(tmp_path):
    client = _client(tmp_path, hotword_import_max_rows=2)
    payload = json.dumps([{"term": f"t{i}"} for i in range(3)])

    resp = client.post(
        "/api/admin/hotwords/import?format=json",
        files={"file": ("h.json", payload, "application/json")},
    )

    assert resp.status_code == 413
    assert resp.json()["error"]["code"]


def test_import_rejects_malformed_json(tmp_path):
    client = _client(tmp_path)

    resp = client.post(
        "/api/admin/hotwords/import?format=json",
        files={"file": ("h.json", "{not valid json", "application/json")},
    )

    assert resp.status_code == 400
    assert resp.json()["error"]["code"]


def test_import_rejects_csv_missing_term_column(tmp_path):
    client = _client(tmp_path)

    resp = client.post(
        "/api/admin/hotwords/import?format=csv",
        files={"file": ("h.csv", "note,enabled\n說明,true\n", "text/csv")},
    )

    assert resp.status_code == 400
    assert resp.json()["error"]["code"]
