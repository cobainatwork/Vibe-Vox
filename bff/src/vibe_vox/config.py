"""BFF 設定。各值可經環境變數覆寫，供 Docker Compose 正式版與 dev 版切換。"""

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

T = TypeVar("T")


def _env(name: str, default: str, cast: Callable[[str], T]) -> Callable[[], T]:
    """回傳 default_factory：實例化時讀環境變數並轉型，未設則用 default。"""
    return lambda: cast(os.environ.get(name, default))


def _parse_origins(raw: str) -> list[str]:
    return [o.strip() for o in raw.split(",") if o.strip()]


# 重量級端點的總體 guard 相對「各子系統逾時之和」的餘裕倍數。
#
# 和本身只涵蓋轉碼、辨識與對齊，但 guard 還包住 200 MB 級的上傳讀取、wav 長度讀取
# 與送 vLLM 前的 base64 編碼。無餘裕時 guard 可能先於 asr_timeout 觸發，使用者拿到
# 籠統的 REQUEST_TIMEOUT 而非精確的 ASR_TIMEOUT，違反「內層先觸發」的順序（#35）。
HEAVY_GUARD_MARGIN = 1.2


@dataclass(frozen=True)
class Settings:
    # 嚴格 CORS/Origin 白名單：僅允許前端 Origin，不開放萬用字元。
    allowed_origins: list[str] = field(
        default_factory=_env("VIBE_VOX_FRONTEND_ORIGINS", "http://localhost:5173", _parse_origins)
    )
    # SQLite 單檔資料庫路徑（Hotword 與後續 Voice metadata）。
    db_path: Path = field(default_factory=_env("VIBE_VOX_DB_PATH", "var/vibe_vox.db", Path))
    # Hotword context 的 token 預算上界（64K 為 context＋音訊共用，留裕度給音訊）。
    hotword_context_token_budget: int = field(
        default_factory=_env("VIBE_VOX_HOTWORD_CONTEXT_TOKEN_BUDGET", "8000", int)
    )
    # Hotword 匯入上限：檔案大小（bytes）與筆數，超過回 413。
    hotword_import_max_bytes: int = field(
        default_factory=_env("VIBE_VOX_HOTWORD_IMPORT_MAX_BYTES", "1048576", int)
    )
    hotword_import_max_rows: int = field(
        default_factory=_env("VIBE_VOX_HOTWORD_IMPORT_MAX_ROWS", "10000", int)
    )
    # 音色參考音的存放目錄。須與 temp_dir 分開並置於持久化 volume：參考音是
    # 音色的一部分，隨容器銷毀等同音色消失（同 #33 對資料庫的處置）。
    voice_dir: Path = field(default_factory=_env("VIBE_VOX_VOICE_DIR", "var/voices", Path))
    # 暫存資料夾與孤兒檔保留期（秒）；啟動序列據此回收過期檔。
    temp_dir: Path = field(default_factory=_env("VIBE_VOX_TEMP_DIR", "var/tmp", Path))
    temp_max_age_seconds: float = field(
        default_factory=_env("VIBE_VOX_TEMP_MAX_AGE_SECONDS", "86400", float)
    )
    # 重量級請求資源護欄：單一請求逾時（秒）與同時處理上限。
    request_timeout_seconds: float = field(
        default_factory=_env("VIBE_VOX_REQUEST_TIMEOUT_SECONDS", "60", float)
    )
    max_concurrent_heavy_requests: int = field(
        default_factory=_env("VIBE_VOX_MAX_CONCURRENT_HEAVY_REQUESTS", "8", int)
    )
    # 音檔上傳單檔上限（bytes）；超過回 413 語意。200 MiB 容納長會議錄音。
    # **這個值同時被 Voice clone 的參考音上傳沿用**（api/admin_voices.py），而參考音
    # 的合理上限是數 MiB——兩者解耦見 #44。
    audio_max_bytes: int = field(
        default_factory=_env("VIBE_VOX_AUDIO_MAX_BYTES", "209715200", int)
    )
    # ASR 目標取樣率：對齊官方 vllm_plugin/inputs.py，該處三度寫死 24000
    # （load_audio 的 target_sr、load_audio_bytes_use_ffmpeg、duration 換算）。
    # 原設 16000 會對高取樣率來源（如 48k 上傳檔）先降採樣、再由 plugin 上採樣
    # 回 24k，8kHz 以上的真實內容被我方丟棄且無法還原。設為 24000 則一次降採樣、
    # plugin 端成為 no-op。
    # 註：AI_practise 前端錄音本身即 16k（recorder.ts 的 TARGET_RATE），該來源
    # 改與不改等價；本設定的收益在管理平面上傳的高取樣率音檔。
    # transcode_to_wav 的 sample_rate 為必填參數，模組本身不硬編此值。
    asr_sample_rate: int = field(
        default_factory=_env("VIBE_VOX_ASR_SAMPLE_RATE", "24000", int)
    )
    # 遠端語音轉文字模型（vLLM）位址；預設對齊 compose 的 vllm 服務（同機部署內部位址）。
    asr_base_url: str = field(
        default_factory=_env("VIBE_VOX_ASR_BASE_URL", "http://vllm:8000", str)
    )
    # vLLM 的 --served-model-name；client 呼叫時的 model 參數用它（官方預設 vibevoice）。
    # 模型權重已 bake 進 vllm image（docker/vllm.Dockerfile），無需在此指定模型路徑。
    asr_served_name: str = field(
        default_factory=_env("VIBE_VOX_ASR_SERVED_NAME", "vibevoice", str)
    )
    # 呼叫遠端 ASR 的逾時（秒）：涵蓋網路 + 模型推論，較 ffmpeg 寬。
    #
    # **此值直接決定可用的音檔長度上限**，與 docs/api/asr.md 記載的模型上限（61 分鐘）
    # 無關。vllm_asr 的 max_tokens = duration*10 + 100，而實測生成速度約 50 tokens/s，
    # 故 T 秒的逾時約支援 T*5.35 秒的音檔（依 214 秒音檔／850 字／約 40 秒的實測比例）。
    #
    # 原值 120 只到約 10.7 分鐘，10 分鐘的會議錄音即卡在邊界（#35）。300 秒約 26 分鐘，
    # 覆蓋管理平面的測試情境；實際負載是回合制對話的 1–2 分鐘，遠低於此。
    #
    # 不設更大：61 分鐘需要約 750 秒，而長逾時會讓掛住的請求佔住 GPU 與連線。
    #
    # **改此值要同步三處。** 只有第 1 項有測試保護（`test_config.py` 的
    # `test_reverse_proxy_timeout_exceeds_heavy_guard` 會實際去讀 nginx.conf），
    # 另兩項只能靠這條註解：
    #   1. frontend/nginx.conf 的 proxy_read_timeout（須大於 heavy_request_budget）
    #   2. frontend/src/AsrPanel.tsx 的 MAX_DURATION_SECONDS（操作者看到的警示閾值）
    #   3. docs/api/asr.md §3.3 的「音訊長度（實際可用）」與 §5 的 ASR_TIMEOUT 列
    asr_timeout_seconds: float = field(
        default_factory=_env("VIBE_VOX_ASR_TIMEOUT_SECONDS", "300", float)
    )
    # 字級強制對齊服務位址；預設對齊 compose 的 aligner 服務（內部服務，不對外映射）。
    aligner_base_url: str = field(
        default_factory=_env("VIBE_VOX_ALIGNER_BASE_URL", "http://aligner:9100", str)
    )
    # 對齊的逾時（秒），**所有批次共用的一份預算而非每批各有一份**。實測 32 段（總音訊
    # 1075 秒）耗時 1.4 秒、日常 2–4 段約 0.2 秒（ADR-0004），故 60 秒的餘裕主要用於多段
    # multipart 的傳輸而非推論。
    #
    # 語義為整體是 `heavy_request_budget()` 的前提：那條算式只加它一次，若它是每批的
    # 逾時，63 段的會議錄音（預設上限下為 8 批）最壞就要 8 倍，guard 會先於內層觸發並回
    # 504，逐字稿一併喪失。預算的執行在 `adapters/aligner.py` 的 `_align_batches`。
    aligner_timeout_seconds: float = field(
        default_factory=_env("VIBE_VOX_ALIGNER_TIMEOUT_SECONDS", "60", float)
    )
    # 單次送去對齊的段數上限，**須與 aligner 服務端的同名變數一致或更小**。取值依據與
    # 這條耦合的完整理由見 `adapters/aligner.py` 的 DEFAULT_MAX_BATCH_ITEMS；不變量由
    # `test_config.py` 實際比對兩邊的設定檔守著，不靠註解（#35 的教訓）。
    aligner_max_batch_items: int = field(
        default_factory=_env("VIBE_VOX_ALIGNER_MAX_BATCH_ITEMS", "8", int)
    )
    # 逐段切片左右各留的 buffer（秒）。VibeVoice 的段界是模型自選切點而非發音邊界，
    # 可能落在某個字的發音中間；buffer 使邊界字的音訊完整落在切片內。取值依據見
    # adapters/aligner.py 的 DEFAULT_SLICE_BUFFER_SECONDS。
    aligner_slice_buffer_seconds: float = field(
        default_factory=_env("VIBE_VOX_ALIGNER_SLICE_BUFFER_SECONDS", "0.5", float)
    )
    # 遠端語音合成服務（vLLM-Omni）位址；預設對齊 compose 的 tts 服務。
    tts_base_url: str = field(
        default_factory=_env("VIBE_VOX_TTS_BASE_URL", "http://tts:8000", str)
    )
    # vLLM-Omni 的 --served-model-name；合成請求的 model 參數用它。
    tts_served_name: str = field(
        default_factory=_env("VIBE_VOX_TTS_SERVED_NAME", "voxcpm2", str)
    )
    # 呼叫遠端 TTS 的逾時（秒）。**尚未以實測校準**：延遲 bar 是 #17，服務跑起來
    # 才量得到。此值為覆蓋冷啟的保守估計——recipe 記載 server 冷啟後首個請求約 25 秒
    # （compile／graph capture），穩態 RTF ~0.12，故一句話的穩態成本遠低於此。
    tts_timeout_seconds: float = field(
        default_factory=_env("VIBE_VOX_TTS_TIMEOUT_SECONDS", "120", float)
    )
    # 單次合成的文字長度上限（字元），超過回 413。
    #
    # 消費端是逐句合成（一次請求一種語氣，見 docs/api/tts.md §5.2），故實際請求遠低於
    # 此值；上限擋的是整篇文章被灌進來而長時間佔住 GPU。**不是從模型上限推導的**：
    # VoxCPM2 的 max_model_len 為 4096 token，而中文的字元對 token 比例未量測。
    tts_max_input_chars: int = field(
        default_factory=_env("VIBE_VOX_TTS_MAX_INPUT_CHARS", "2000", int)
    )
    # 無 GPU 環境（dev）以 stub adapter 回假結果啟動，不連真實模型服務。
    use_stub_models: bool = field(
        default_factory=_env(
            "VIBE_VOX_USE_STUB_MODELS", "false", lambda v: v.lower() == "true"
        )
    )
    # FFmpeg 轉碼子進程逾時（秒）。
    ffmpeg_timeout_seconds: float = field(
        default_factory=_env("VIBE_VOX_FFMPEG_TIMEOUT_SECONDS", "60", float)
    )

    def __post_init__(self) -> None:
        """擋下會讓每個請求都壞掉的設定值。

        batch size 小於 1 時分批會在執行期拋 `ValueError`。對齊的降級只涵蓋「服務給不出
        結果」（那些已由 `AlignerClient.align` 轉成 omission），設定錯誤不在其中，故它會
        冒成 500 使**逐字稿一併失效**，正是 ADR-0004 第二層降級要避免的結果。設定錯誤在
        啟動時就喊出來，而不是等每個辨識請求都壞掉才被發現。
        """
        if self.aligner_max_batch_items < 1:
            raise ValueError(
                "VIBE_VOX_ALIGNER_MAX_BATCH_ITEMS 須至少為 1，"
                f"得到 {self.aligner_max_batch_items}"
            )

    def heavy_request_budget(self) -> float:
        """重量級端點（轉碼＋辨識＋對齊）的總體逾時預算。

        端點與測試都用這個方法，不各自重算：否則某天多一個子系統時，端點加了而
        測試沒加，那條「內層先觸發」的保證就悄悄失效。餘裕見 HEAVY_GUARD_MARGIN。
        """
        return (
            self.asr_timeout_seconds
            + self.ffmpeg_timeout_seconds
            + self.aligner_timeout_seconds
        ) * HEAVY_GUARD_MARGIN

    def tts_request_budget(self) -> float:
        """合成端點的總體逾時預算（合成 + 降採樣的轉碼）。

        與 heavy_request_budget 分開算：TTS 不經 ASR 與對齊，用同一個預算會讓 guard
        遠晚於 tts_timeout 才觸發，掛住的請求白佔一個併發額度。同樣留餘裕讓內層的
        TtsTimeout 先觸發（→ 504 TTS_TIMEOUT），guard 只當 backstop。
        """
        return (
            self.tts_timeout_seconds + self.ffmpeg_timeout_seconds
        ) * HEAVY_GUARD_MARGIN
