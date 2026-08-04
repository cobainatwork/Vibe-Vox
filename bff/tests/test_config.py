"""Settings 的環境變數對接。"""

from vibe_vox.config import Settings


def test_asr_base_url_defaults_to_compose_vllm():
    # 預設對齊 docker-compose 的 vllm 服務位址（整套同機部署時的內部位址）。
    assert Settings().asr_base_url == "http://vllm:8000"


def test_asr_base_url_reads_env(monkeypatch):
    monkeypatch.setenv("VIBE_VOX_ASR_BASE_URL", "http://10.0.0.5:8000")
    assert Settings().asr_base_url == "http://10.0.0.5:8000"


def test_aligner_base_url_defaults_to_compose_aligner():
    # 預設對齊 docker-compose 的 aligner 服務與其容器內埠 9100（內部服務，不對外映射）。
    assert Settings().aligner_base_url == "http://aligner:9100"


def test_aligner_slice_buffer_covers_longest_word():
    # 依據為 #26 實測的單字時長上界 0.40 秒（ADR-0004 Consequences）：buffer 小於它
    # 則邊界字可能被切成兩半、兩段都對不準。放大則納入更多鄰段語音干擾對齊。
    assert Settings().aligner_slice_buffer_seconds == 0.5


def test_asr_sample_rate_matches_plugin_target():
    # 官方 vllm_plugin/inputs.py 一律 resample 至 24000。設 16000 會使高取樣率
    # 來源先被我方降採樣、再由 plugin 上採樣回 24k，丟失的高頻無法還原。
    assert Settings().asr_sample_rate == 24000
