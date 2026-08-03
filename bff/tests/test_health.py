"""S1：健康檢查端點在 BFF HTTP seam 回報 ASR/TTS 就緒狀態。"""

from fastapi.testclient import TestClient

from vibe_qwen.adapters.stub import StubAsrClient, StubTtsClient
from vibe_qwen.config import Settings
from vibe_qwen.main import create_app


def test_health_reports_both_services_ready_with_stub_adapters():
    app = create_app(settings=Settings(use_stub_models=True))  # stub 模式，兩者皆就緒
    client = TestClient(app)

    resp = client.get("/api/health")

    assert resp.status_code == 200
    assert resp.json() == {"data": {"asr": {"ready": True}, "tts": {"ready": True}}}


def test_health_reflects_adapter_readiness_per_service():
    # 就緒狀態須來自 adapter 探測：ASR 未就緒時只有 asr.ready 為 false。
    app = create_app(asr_client=StubAsrClient(ready=False), tts_client=StubTtsClient(ready=True))
    client = TestClient(app)

    resp = client.get("/api/health")

    assert resp.status_code == 200
    assert resp.json() == {"data": {"asr": {"ready": False}, "tts": {"ready": True}}}
