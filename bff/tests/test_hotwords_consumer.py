"""#2 消費端契約 /api/hotwords：約束性 {id, word} 形狀（ADR-0003）。

形狀凍結、不套 {data} 信封，對齊 AI_practise 既有 provider 期望。
"""

from fastapi.testclient import TestClient

from vibe_vox.config import Settings
from vibe_vox.main import create_app


def _client(tmp_path) -> TestClient:
    return TestClient(create_app(settings=Settings(db_path=tmp_path / "t.db")))


def test_consumer_list_returns_id_word_shape_enabled_only(tmp_path):
    client = _client(tmp_path)
    a = client.post("/api/admin/hotwords", json={"term": "啟用詞"}).json()["data"]
    b = client.post("/api/admin/hotwords", json={"term": "停用詞"}).json()["data"]
    client.patch(f"/api/admin/hotwords/{b['id']}/enabled", json={"enabled": False})

    resp = client.get("/api/hotwords")

    assert resp.status_code == 200
    # 只回 enabled，形狀為 {id, word}（word 即 term），無 note/enabled 欄位。
    assert resp.json() == [{"id": a["id"], "word": "啟用詞"}]


def test_consumer_create_accepts_word_returns_id_word(tmp_path):
    client = _client(tmp_path)

    resp = client.post("/api/hotwords", json={"word": "新詞"})

    assert resp.status_code == 201
    body = resp.json()
    assert set(body.keys()) == {"id", "word"}
    assert body["word"] == "新詞"
    assert isinstance(body["id"], str) and body["id"]


def test_consumer_delete_returns_success(tmp_path):
    client = _client(tmp_path)
    created = client.post("/api/hotwords", json={"word": "待刪"}).json()

    resp = client.delete(f"/api/hotwords/{created['id']}")

    assert resp.status_code == 200
    assert resp.json() == {"success": True}
    assert client.get("/api/hotwords").json() == []
