"""#3 context 編譯與 token 護欄：BFF HTTP seam。"""

from fastapi.testclient import TestClient

from vibe_vox.config import Settings
from vibe_vox.main import create_app


def _client(tmp_path, **settings) -> TestClient:
    return TestClient(create_app(settings=Settings(db_path=tmp_path / "t.db", **settings)))


def test_context_preview_compiles_enabled_terms_with_estimate(tmp_path):
    client = _client(tmp_path)
    client.post("/api/admin/hotwords", json={"term": "台積電"})
    client.post("/api/admin/hotwords", json={"term": "聯發科"})
    disabled = client.post("/api/admin/hotwords", json={"term": "停用詞"}).json()["data"]
    client.patch(f"/api/admin/hotwords/{disabled['id']}/enabled", json={"enabled": False})

    resp = client.get("/api/admin/hotwords/context/preview")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "台積電" in data["context"]
    assert "聯發科" in data["context"]
    assert "停用詞" not in data["context"]  # 停用者不進 context
    assert data["token_estimate"] > 0
    assert data["token_budget"] == 8000


def test_context_preview_over_budget_returns_413(tmp_path):
    # 伺服器端強制：估算超出預算的 context 直接回 413，不送模型。
    client = _client(tmp_path, hotword_context_token_budget=5)
    client.post("/api/admin/hotwords", json={"term": "台積電聯發科鴻海"})

    resp = client.get("/api/admin/hotwords/context/preview")

    assert resp.status_code == 413
    error = resp.json()["error"]
    assert set(error.keys()) == {"code", "message"}  # 回歸統一 {code, message} 信封
    assert str(5) in error["message"]  # message 已含上限數字供 UI 顯示
