"""Aligner 服務設定。各值可經環境變數覆寫，前綴與 BFF 一致。"""

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TypeVar

T = TypeVar("T")


def _env(name: str, default: str, cast: Callable[[str], T]) -> Callable[[], T]:
    """回傳 default_factory：實例化時讀環境變數並轉型，未設則用 default。"""
    return lambda: cast(os.environ.get(name, default))


@dataclass(frozen=True)
class Settings:
    # 權重已 bake 進 image（docker/aligner.Dockerfile），runtime 命中 HF 快取不下載。
    # 改此值會讓 runtime 嘗試連網抓新模型——image 內只有預設那一個，離線即失敗。
    # 要換模型須同步改 Dockerfile 的 snapshot_download。
    model_id: str = field(
        default_factory=_env("VIBE_VOX_ALIGNER_MODEL", "Qwen/Qwen3-ForcedAligner-0.6B", str)
    )
    # device_map 值。正式部署為 cuda:0（compose 只掛一張卡），但設為 cpu 即可在無
    # GPU 的機器上跑完整推論——本模型僅 0.6B 且解碼為單次 NAR forward，CPU 跑得動，
    # 開發機的端到端驗證即靠此（見 aligner/README.md 的驗證狀態）。
    device: str = field(default_factory=_env("VIBE_VOX_ALIGNER_DEVICE", "cuda:0", str))
    # 單筆音訊秒數上限。取 qwen-asr 的 MAX_FORCE_ALIGN_INPUT_SECONDS（inference/utils.py）
    # 之 180，而非 model card 宣稱的 5 分鐘——該常數是套件作者對序列長度上限
    # （max_position_embeddings 8192）的換算，較宣傳值可信。套件本身不強制檢查，
    # 逾限會靜默對歪，故在此擋下。ADR-0004 記載的 300 秒為誤，已於該文修正。
    max_audio_seconds: float = field(
        default_factory=_env("VIBE_VOX_ALIGNER_MAX_AUDIO_SECONDS", "180", float)
    )
    # 同時進 GPU 的請求上限。測試區不建佇列：達上限直接 load-shed 回 503，
    # 與 BFF 的 TOO_MANY_REQUESTS 同模式。prod 併發以加 replica 達成（ADR-0004）。
    max_concurrent_requests: int = field(
        default_factory=_env("VIBE_VOX_ALIGNER_MAX_CONCURRENT_REQUESTS", "1", int)
    )
    # 單一請求的段數上限。單段有秒數上限，但聚合量沒有——61 分鐘音檔約 100 段，
    # 一次送就撞 VRAM，而該卡由 vllm 與 tts 共用，CUDA OOM 會波及它們。
    # 32 已由實測支撐（2026-08-04，34 秒段長）：VRAM 峰值 5750 MiB，在 vLLM 佔
    # 37890 MiB 的當前配置下尚餘 2428 MiB。邊際成本約 108 MiB/段，故 64 段會超出
    # 可用量。量測腳本見 aligner/scripts/，數據見 README——vLLM 的
    # gpu_memory_utilization 若改動，此上限須重測。
    max_batch_items: int = field(
        default_factory=_env("VIBE_VOX_ALIGNER_MAX_BATCH_ITEMS", "32", int)
    )
