"""Settings 的環境變數對接。"""

import re
from pathlib import Path

from vibe_vox.config import Settings


def test_asr_base_url_defaults_to_compose_vllm():
    # 預設對齊 docker-compose 的 vllm 服務位址（整套同機部署時的內部位址）。
    assert Settings().asr_base_url == "http://vllm:8000"


def test_asr_base_url_reads_env(monkeypatch):
    monkeypatch.setenv("VIBE_VOX_ASR_BASE_URL", "http://10.0.0.5:8000")
    assert Settings().asr_base_url == "http://10.0.0.5:8000"


def test_asr_timeout_covers_realistic_test_audio():
    # 逾時直接決定可用的音檔長度上限：max_tokens = duration*10 + 100，實測生成速度
    # 約 50 tokens/s，故最壞情況（輸出達上限）可容納 5T − 10 秒的音檔。120 秒只到
    # 9.8 分鐘，而 10 分鐘的會議錄音即因此 504（#35）。300 秒約 24.8 分鐘。
    assert Settings().asr_timeout_seconds == 300


def _nginx_proxy_read_timeout() -> float:
    """從 frontend/nginx.conf 實際讀出 proxy_read_timeout。

    刻意不寫死：#35 的根因是那幾行**根本不存在**（nginx 預設 60 秒成了系統的實際
    上限）。字面值的斷言防不了那種失效：把設定刪掉，斷言照樣過。
    """
    conf = Path(__file__).resolve().parents[2] / "frontend" / "nginx.conf"
    m = re.search(r"^\s*proxy_read_timeout\s+(\d+)s\s*;", conf.read_text("utf-8"), re.M)
    assert m is not None, f"{conf} 未設 proxy_read_timeout，逾時鏈路最短的一環會是它"
    return float(m.group(1))


def test_reverse_proxy_timeout_exceeds_heavy_guard():
    # 順序必須是「內層先觸發」：BFF 的 guard 先到才會回 JSON 錯誤信封，nginx 先到
    # 則使用者拿到 HTML 錯誤頁（#35 就是後者）。
    #
    # 用 Settings.heavy_request_budget() 而非自行相加：端點也用同一個方法，故某天多
    # 一個子系統的逾時時，兩邊會一起變，這條保證不會悄悄失效。
    assert _nginx_proxy_read_timeout() > Settings().heavy_request_budget()


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
