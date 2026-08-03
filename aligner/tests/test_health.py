"""就緒探測：BFF 與 docker HEALTHCHECK 皆據此判斷 aligner 可否收活。"""

from fastapi.testclient import TestClient

from vibe_vox_aligner.main import create_app

from fakes import FakeAligner


def test_health_not_ready_without_model() -> None:
    """未載入模型須回非 2xx：BFF 的探測只看狀態碼（adapters/vllm_asr.py 的 health）。"""
    client = TestClient(create_app(aligner=None))

    resp = client.get("/health")

    assert resp.status_code == 503
    assert resp.json() == {"ready": False}


def test_health_ready_with_model() -> None:
    client = TestClient(create_app(aligner=FakeAligner()))

    resp = client.get("/health")

    assert resp.status_code == 200
    assert resp.json() == {"ready": True}


def test_startup_survives_model_load_failure() -> None:
    """權重載入失敗（如機器無 GPU）不得使容器 crash-loop，改以 /health 回報未就緒。"""

    def failing_loader() -> FakeAligner:
        raise RuntimeError("no CUDA device")

    with TestClient(create_app(load_aligner=failing_loader)) as client:
        resp = client.get("/health")

    assert resp.status_code == 503
    assert resp.json() == {"ready": False}
