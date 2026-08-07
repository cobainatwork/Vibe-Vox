"""Settings 的環境變數對接。"""

import re
from pathlib import Path

import pytest

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


def _aligner_service_max_batch_items() -> int:
    """從 aligner 服務端的 config 實際讀出單次段數上限的預設值。

    刻意不寫死、也不 import：aligner 是獨立的部署單元與獨立的 venv，BFF 匯入不到它。
    而寫死的斷言防不了「其中一邊被改掉」這種失效，那正是 #35 的教訓：跨元件的邊界值
    若只靠註解同步，某天一邊改了就悄悄失效，而失效的表現是全段拿不到時間戳（#36）。
    """
    conf = (
        Path(__file__).resolve().parents[2]
        / "aligner"
        / "src"
        / "vibe_vox_aligner"
        / "config.py"
    )
    m = re.search(
        r'"VIBE_VOX_ALIGNER_MAX_BATCH_ITEMS",\s*"(\d+)"', conf.read_text("utf-8")
    )
    assert m is not None, f"{conf} 未定義 VIBE_VOX_ALIGNER_MAX_BATCH_ITEMS 的預設值"
    return int(m.group(1))


def test_batch_size_does_not_exceed_the_aligner_service_limit():
    # 服務端超過上限即整批回 400 BATCH_TOO_LARGE，該批全段拿不到時間戳。呼叫端必須先於
    # 送出就遵守它，不能靠撞牆後重試，故本端的值必須小於或等於服務端（#36）。
    assert (
        Settings().aligner_max_batch_items <= _aligner_service_max_batch_items()
    )


@pytest.mark.parametrize("value", ["0", "-1"])
def test_batch_size_below_one_fails_at_startup(monkeypatch, value):
    # 分批在 batch size 小於 1 時拋 ValueError，而端點只攔對齊服務的兩種例外，故每個
    # 辨識請求都會回 500、**連逐字稿一起失效**，正是 ADR-0004 第二層降級要避免的結果。
    # 設定錯誤該在啟動時就喊出來，而不是等到每個請求都壞掉才發現。
    monkeypatch.setenv("VIBE_VOX_ALIGNER_MAX_BATCH_ITEMS", value)

    with pytest.raises(ValueError, match="VIBE_VOX_ALIGNER_MAX_BATCH_ITEMS"):
        Settings()


def _compose_services() -> dict[str, str]:
    """讀 docker-compose.yml，回傳服務名到該服務整段內容（原文）的對照。

    刻意不引入 YAML 依賴：只為測試加執行期相依不值得，而以服務層縮排切塊已足夠
    （本檔的服務鍵一律兩格縮排且後方無值）。
    """
    compose = Path(__file__).resolve().parents[2] / "docker-compose.yml"
    blocks = re.split(r"^  ([\w-]+):$", compose.read_text("utf-8"), flags=re.M)
    # split 後的形狀為 [前言, 服務名, 內容, 服務名, 內容, ...]
    return dict(zip(blocks[1::2], blocks[2::2]))


def _compose_env_by_service(variable: str) -> dict[str, str]:
    """各服務為該環境變數設的值（未設的服務不列入）。"""
    found: dict[str, str] = {}
    for name, body in _compose_services().items():
        m = re.search(rf"^\s*{variable}:\s*(.+?)\s*$", body, re.M)
        if m is not None:
            found[name] = m.group(1)
    return found


@pytest.mark.parametrize(
    "flag", ["--max-model-len", "--max-num-seqs", "--gpu-memory-utilization"]
)
def test_vllm_memory_params_are_set_explicitly(flag):
    # **這條測的是「有沒有被選擇過」，不是值本身**；取值依據見 .env.example。
    #
    # #35 的根因是沒人選過的預設值（nginx 的 60 秒）成了系統的實際上限。同一個失效模式在
    # 此更隱蔽：這三個值寫在 image 內的上游腳本裡，連 grep 都找不到。
    assert flag in _compose_services()["vllm"], (
        f"vllm 服務未顯式設定 {flag}，會落回上游腳本的預設值"
    )


def test_batch_size_is_wired_to_both_services_identically():
    # bff 用這個值決定怎麼分批，aligner 用它決定拒絕什麼。compose 漏接任一邊不會報錯，
    # 該服務只是改用自己的程式預設值；而兩邊不一致的症狀是整批回 400、該批全段拿不到
    # 時間戳（#36）。修 #36 時發現 aligner 服務原本連 environment 區塊都沒有，設了
    # 這個變數也不會生效，屬 #35 那類「設定看得到卻不生效」。
    wired = _compose_env_by_service("VIBE_VOX_ALIGNER_MAX_BATCH_ITEMS")

    assert set(wired) == {"bff", "aligner"}, f"漏接的服務會用自己的預設值：{wired}"
    assert len(set(wired.values())) == 1, f"兩邊的值不一致：{wired}"


def test_asr_sample_rate_matches_plugin_target():
    # 官方 vllm_plugin/inputs.py 一律 resample 至 24000。設 16000 會使高取樣率
    # 來源先被我方降採樣、再由 plugin 上採樣回 24k，丟失的高頻無法還原。
    assert Settings().asr_sample_rate == 24000
