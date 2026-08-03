"""Settings 的環境變數對接。"""

from vibe_vox.config import Settings


def test_asr_base_url_defaults_to_compose_vllm():
    # 預設對齊 docker-compose 的 vllm 服務位址（整套同機部署時的內部位址）。
    assert Settings().asr_base_url == "http://vllm:8000"


def test_asr_base_url_reads_env(monkeypatch):
    monkeypatch.setenv("VIBE_VOX_ASR_BASE_URL", "http://10.0.0.5:8000")
    assert Settings().asr_base_url == "http://10.0.0.5:8000"


def test_asr_sample_rate_matches_plugin_target():
    # 官方 vllm_plugin/inputs.py 一律 resample 至 24000。設 16000 會使高取樣率
    # 來源先被我方降採樣、再由 plugin 上採樣回 24k，丟失的高頻無法還原。
    assert Settings().asr_sample_rate == 24000
