"""S2：狀態變更請求的 Origin/Referer 防護（CSRF 型跨來源偽造）。

用哨兵路徑（無對應路由）隔離中介層決策：被 Origin 閘門擋下回 403，
通過閘門則因無路由回 404。如此測試不依賴任何業務端點是否存在。
"""

from fastapi.testclient import TestClient

from vibe_qwen.config import Settings
from vibe_qwen.main import create_app

FRONTEND = "http://localhost:5173"
PROBE = "/api/__origin_probe__"


def _client() -> TestClient:
    return TestClient(
        create_app(settings=Settings(allowed_origins=[FRONTEND], use_stub_models=True))
    )


def test_state_change_from_foreign_origin_is_rejected_403():
    resp = _client().post(PROBE, headers={"Origin": "http://evil.example"})

    assert resp.status_code == 403
    body = resp.json()
    assert "error" in body
    assert body["error"]["code"]
    assert body["error"]["message"]


def test_state_change_from_allowed_origin_passes_gate():
    # 通過 Origin 閘門後因哨兵路徑無路由而回 404，證明未被 403 擋下。
    resp = _client().post(PROBE, headers={"Origin": FRONTEND})

    assert resp.status_code == 404


def test_state_change_without_origin_headers_passes_gate():
    # server-to-server 消費端不帶 Origin/Referer，須放行而非誤擋成 403。
    resp = _client().post(PROBE)

    assert resp.status_code == 404


def test_same_origin_state_change_passes_gate():
    # 前端與 API 同源（經 nginx 同一 host:port，Origin == Host）非 CSRF，須放行；
    # 支援動態 IP 部署（http://<IP>:8088）零設定，Origin 不必在固定白名單內。
    resp = _client().post(PROBE, headers={"Origin": "http://testserver"})

    assert resp.status_code == 404  # 通過 Origin 閘門後因哨兵路徑無路由而 404


def test_safe_method_from_foreign_origin_is_not_blocked():
    # 安全方法（GET）不做 Origin 檢查：健康檢查仍回 200。
    resp = _client().get("/api/health", headers={"Origin": "http://evil.example"})

    assert resp.status_code == 200
