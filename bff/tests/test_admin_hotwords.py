"""#2 管理平面 Hotword CRUD：在 BFF HTTP seam 驗證完整欄位與行為。"""

from fastapi.testclient import TestClient

from vibe_vox.config import Settings
from vibe_vox.main import create_app


def _client(tmp_path) -> TestClient:
    return TestClient(create_app(settings=Settings(db_path=tmp_path / "t.db")))


def test_create_hotword_returns_full_shape(tmp_path):
    client = _client(tmp_path)

    resp = client.post("/api/admin/hotwords", json={"term": "Vibe-Vox", "note": "產品名"})

    assert resp.status_code == 201
    data = resp.json()["data"]
    assert data["term"] == "Vibe-Vox"
    assert data["note"] == "產品名"
    assert data["enabled"] is True
    assert isinstance(data["id"], str) and len(data["id"]) >= 32
    assert data["created_at"]
    assert data["updated_at"]


def test_list_hotwords_returns_all_created(tmp_path):
    client = _client(tmp_path)
    client.post("/api/admin/hotwords", json={"term": "alpha"})
    client.post("/api/admin/hotwords", json={"term": "beta"})

    resp = client.get("/api/admin/hotwords")

    assert resp.status_code == 200
    terms = [h["term"] for h in resp.json()["data"]]
    assert set(terms) == {"alpha", "beta"}


def test_search_hotwords_filters_by_term_and_note(tmp_path):
    client = _client(tmp_path)
    client.post("/api/admin/hotwords", json={"term": "台積電", "note": "半導體"})
    client.post("/api/admin/hotwords", json={"term": "聯發科", "note": "IC 設計"})
    client.post("/api/admin/hotwords", json={"term": "鴻海", "note": "半導體封測"})

    by_term = client.get("/api/admin/hotwords", params={"q": "聯發科"})
    assert [h["term"] for h in by_term.json()["data"]] == ["聯發科"]

    by_note = client.get("/api/admin/hotwords", params={"q": "半導體"})
    assert {h["term"] for h in by_note.json()["data"]} == {"台積電", "鴻海"}


def test_update_hotword_changes_term_and_note(tmp_path):
    client = _client(tmp_path)
    created = client.post("/api/admin/hotwords", json={"term": "舊詞", "note": "舊註"}).json()["data"]

    resp = client.put(
        f"/api/admin/hotwords/{created['id']}", json={"term": "新詞", "note": "新註"}
    )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["id"] == created["id"]
    assert data["term"] == "新詞"
    assert data["note"] == "新註"


def test_toggle_enabled_sets_state(tmp_path):
    client = _client(tmp_path)
    created = client.post("/api/admin/hotwords", json={"term": "詞"}).json()["data"]
    assert created["enabled"] is True

    off = client.patch(f"/api/admin/hotwords/{created['id']}/enabled", json={"enabled": False})
    assert off.status_code == 200
    assert off.json()["data"]["enabled"] is False

    on = client.patch(f"/api/admin/hotwords/{created['id']}/enabled", json={"enabled": True})
    assert on.json()["data"]["enabled"] is True


def test_delete_hotword_removes_it(tmp_path):
    client = _client(tmp_path)
    created = client.post("/api/admin/hotwords", json={"term": "待刪"}).json()["data"]

    resp = client.delete(f"/api/admin/hotwords/{created['id']}")

    assert resp.status_code == 200
    assert resp.json()["data"]["success"] is True
    assert client.get("/api/admin/hotwords").json()["data"] == []


def test_create_sanitizes_term_only_not_note(tmp_path):
    client = _client(tmp_path)

    # term 會注入 context，故 <|...|> 特殊 token 標記與控制字元須剝除以字面
    # tokenize；note 不進 prompt，保留原值以免誤刪合法說明。
    resp = client.post(
        "/api/admin/hotwords",
        json={"term": "台積\x00電<|speaker:1|>", "note": "用 <|im_start|> 標記說明"},
    )

    assert resp.status_code == 201
    data = resp.json()["data"]
    assert data["term"] == "台積電"
    assert data["note"] == "用 <|im_start|> 標記說明"


def test_create_sanitizes_markers_that_reform_after_one_pass(tmp_path):
    # `<\|.*?\|>` 是非貪婪匹配，移除一層後殘骸可能重新組成一個合法的標記：
    # `<<||>|speaker:1|>` 的中間段 `<||>` 被吃掉後剩下 `<|speaker:1|>`。單次替換會讓它
    # 原樣進 Context prompt，而那正是本清洗要擋的東西——term 是不可信輸入。
    client = _client(tmp_path)

    resp = client.post(
        "/api/admin/hotwords", json={"term": "台積<<||>|speaker:1|>電"}
    )

    assert resp.status_code == 201
    assert resp.json()["data"]["term"] == "台積電"


def test_create_rejects_term_empty_after_sanitize(tmp_path):
    client = _client(tmp_path)

    # 整個 term 皆為特殊 token 與控制字元，清洗後無有效內容可存。
    resp = client.post("/api/admin/hotwords", json={"term": "<|endoftext|>\x00"})

    assert resp.status_code == 400
    assert resp.json()["error"]["code"]
    assert client.get("/api/admin/hotwords").json()["data"] == []


def test_create_rejects_missing_term(tmp_path):
    client = _client(tmp_path)

    resp = client.post("/api/admin/hotwords", json={"note": "無 term"})

    assert resp.status_code == 400
    assert resp.json()["error"]["code"]


def test_operations_on_missing_id_return_404(tmp_path):
    client = _client(tmp_path)
    missing = "00000000-0000-0000-0000-000000000000"

    put = client.put(f"/api/admin/hotwords/{missing}", json={"term": "x"})
    patch = client.patch(f"/api/admin/hotwords/{missing}/enabled", json={"enabled": False})
    delete = client.delete(f"/api/admin/hotwords/{missing}")

    for resp in (put, patch, delete):
        assert resp.status_code == 404
        assert resp.json()["error"]["code"]
