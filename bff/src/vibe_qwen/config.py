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


@dataclass(frozen=True)
class Settings:
    # 嚴格 CORS/Origin 白名單：僅允許前端 Origin，不開放萬用字元。
    allowed_origins: list[str] = field(
        default_factory=_env("VIBE_QWEN_FRONTEND_ORIGINS", "http://localhost:5173", _parse_origins)
    )
    # SQLite 單檔資料庫路徑（Hotword 與後續 Voice metadata）。
    db_path: Path = field(default_factory=_env("VIBE_QWEN_DB_PATH", "var/vibe_qwen.db", Path))
    # Hotword context 的 token 預算上界（64K 為 context＋音訊共用，留裕度給音訊）。
    hotword_context_token_budget: int = field(
        default_factory=_env("VIBE_QWEN_HOTWORD_CONTEXT_TOKEN_BUDGET", "8000", int)
    )
    # Hotword 匯入上限：檔案大小（bytes）與筆數，超過回 413。
    hotword_import_max_bytes: int = field(
        default_factory=_env("VIBE_QWEN_HOTWORD_IMPORT_MAX_BYTES", "1048576", int)
    )
    hotword_import_max_rows: int = field(
        default_factory=_env("VIBE_QWEN_HOTWORD_IMPORT_MAX_ROWS", "10000", int)
    )
    # 暫存資料夾與孤兒檔保留期（秒）；啟動序列據此回收過期檔。
    temp_dir: Path = field(default_factory=_env("VIBE_QWEN_TEMP_DIR", "var/tmp", Path))
    temp_max_age_seconds: float = field(
        default_factory=_env("VIBE_QWEN_TEMP_MAX_AGE_SECONDS", "86400", float)
    )
    # 重量級請求資源護欄：單一請求逾時（秒）與同時處理上限。
    request_timeout_seconds: float = field(
        default_factory=_env("VIBE_QWEN_REQUEST_TIMEOUT_SECONDS", "60", float)
    )
    max_concurrent_heavy_requests: int = field(
        default_factory=_env("VIBE_QWEN_MAX_CONCURRENT_HEAVY_REQUESTS", "8", int)
    )
    # 音檔上傳單檔上限（bytes）；超過回 413 語意。25 MiB 對齊常見 ASR 上傳量級。
    audio_max_bytes: int = field(
        default_factory=_env("VIBE_QWEN_AUDIO_MAX_BYTES", "26214400", int)
    )
    # ASR 目標取樣率預設（provisional）；正確值於 #5 接 VibeVoice-ASR 時確認。
    # transcode_to_wav 的 sample_rate 為必填參數，模組本身不硬編此值。
    asr_sample_rate: int = field(
        default_factory=_env("VIBE_QWEN_ASR_SAMPLE_RATE", "16000", int)
    )
    # 遠端語音轉文字模型（vLLM）位址；預設對齊 compose 的 vllm 服務（同機部署內部位址）。
    asr_base_url: str = field(
        default_factory=_env("VIBE_QWEN_ASR_BASE_URL", "http://vllm:8000", str)
    )
    # vLLM serve 的 ASR 模型 ID（chat completions 的 model 參數）；對齊 compose。
    asr_model: str = field(
        default_factory=_env("VIBE_QWEN_ASR_MODEL", "PLACEHOLDER_ASR_MODEL_ID", str)
    )
    # 呼叫遠端 ASR 的逾時（秒）：涵蓋網路 + 模型推論，較 ffmpeg 寬。
    asr_timeout_seconds: float = field(
        default_factory=_env("VIBE_QWEN_ASR_TIMEOUT_SECONDS", "120", float)
    )
    # 無 GPU 環境（dev）以 stub adapter 回假結果啟動，不連真實模型服務。
    use_stub_models: bool = field(
        default_factory=_env(
            "VIBE_QWEN_USE_STUB_MODELS", "false", lambda v: v.lower() == "true"
        )
    )
    # FFmpeg 轉碼子進程逾時（秒）。
    ffmpeg_timeout_seconds: float = field(
        default_factory=_env("VIBE_QWEN_FFMPEG_TIMEOUT_SECONDS", "60", float)
    )
