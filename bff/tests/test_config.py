"""Settings 的環境變數對接。"""

from vibe_qwen.config import Settings


def test_asr_base_url_defaults_to_compose_vllm():
    # 預設對齊 docker-compose 的 vllm 服務位址（整套同機部署時的內部位址）。
    assert Settings().asr_base_url == "http://vllm:8000"


def test_asr_base_url_reads_env(monkeypatch):
    monkeypatch.setenv("VIBE_QWEN_ASR_BASE_URL", "http://10.0.0.5:8000")
    assert Settings().asr_base_url == "http://10.0.0.5:8000"
