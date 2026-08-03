"""S5：嚴格 CORS。僅對白名單前端 Origin 回 ACAO，不開放萬用字元。"""

from fastapi.testclient import TestClient

from vibe_qwen.config import Settings
from vibe_qwen.main import create_app

FRONTEND = "http://localhost:5173"


def _client() -> TestClient:
    return TestClient(
        create_app(settings=Settings(allowed_origins=[FRONTEND], use_stub_models=True))
    )


def _preflight(client: TestClient, origin: str):
    return client.options(
        "/api/health",
        headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
    )


def test_preflight_from_allowed_origin_echoes_that_origin():
    resp = _preflight(_client(), FRONTEND)

    assert resp.headers.get("access-control-allow-origin") == FRONTEND


def test_preflight_from_foreign_origin_gets_no_cors_header():
    # 非白名單 Origin 不得取得 ACAO，證明未設成萬用字元 *。
    resp = _preflight(_client(), "http://evil.example")

    assert resp.headers.get("access-control-allow-origin") is None
