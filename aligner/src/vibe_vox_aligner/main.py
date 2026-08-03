"""字級強制對齊服務（ADR-0004 的第四部署單元）。

無狀態：模型為唯讀共享，請求間不留任何資料。併發以 load-shed 控制而非佇列，
prod 要併發即加 replica。
"""

import io
import json
import logging
import threading
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

import numpy as np
import soundfile as sf
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.exceptions import RequestValidationError
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from vibe_vox_aligner.aligner import Aligner, QwenAligner, Word
from vibe_vox_aligner.config import Settings
from vibe_vox_aligner.errors import AlignerError

logger = logging.getLogger(__name__)

# 送進來的一律是 ASR 的中文逐字稿，故語言不開放呼叫端指定：多一個沒人會用的
# 參數就多兩條測試路徑，理由同 ADR-0004 否決「words 設為可選開關」。
_LANGUAGE = "Chinese"


@dataclass(frozen=True)
class _Item:
    """一筆待對齊的段落。音訊與其文字綁在同一個物件裡，不靠平行清單的索引維繫。"""

    audio: bytes
    text: str


def _build_batch(raw: str, payloads: list[bytes], max_items: int) -> list[_Item]:
    """解析 items 並與音訊逐筆配對。"""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AlignerError(400, "INVALID_ITEMS", "items 不是合法 JSON。") from exc
    if not isinstance(parsed, list):
        raise AlignerError(400, "INVALID_ITEMS", "items 需為陣列。")
    if len(parsed) != len(payloads):
        # 靜默截短會讓消費端的段落 offset 全數錯位，且錯得無聲無息。
        raise AlignerError(
            400,
            "BATCH_SIZE_MISMATCH",
            f"items 有 {len(parsed)} 筆、音訊有 {len(payloads)} 個，數量須相同。",
        )
    if len(parsed) > max_items:
        # 撞 VRAM 只會得到 CUDA OOM，且會波及共用同卡的 vllm 與 tts。
        raise AlignerError(
            400,
            "BATCH_TOO_LARGE",
            f"單次 {len(parsed)} 段超過上限 {max_items} 段，請分批送。",
        )

    batch: list[_Item] = []
    for item, audio in zip(parsed, payloads):
        if not isinstance(item, dict):
            raise AlignerError(400, "INVALID_ITEMS", "items 的每筆需為物件。")
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            raise AlignerError(400, "INVALID_ITEMS", "items 的每筆需有非空的 text。")
        batch.append(_Item(audio=audio, text=text))
    return batch


def _decode_within_limit(raw: bytes, max_seconds: float) -> tuple[np.ndarray, int]:
    """解 wav 為 float32 波形並擋下逾長輸入。

    不轉單聲道、不重取樣——align() 內部一律轉 mono 16k。
    """
    try:
        waveform, sample_rate = sf.read(io.BytesIO(raw), dtype="float32", always_2d=False)
    except (sf.LibsndfileError, ValueError) as exc:
        raise AlignerError(
            400, "AUDIO_DECODE_ERROR", "音訊無法解碼，需為 libsndfile 可讀的格式。"
        ) from exc

    seconds = len(waveform) / sample_rate if sample_rate else 0.0
    if seconds > max_seconds:
        raise AlignerError(
            400,
            "AUDIO_TOO_LONG",
            f"單筆音訊 {seconds:.1f} 秒超過對齊上限 {max_seconds:.0f} 秒。",
        )
    return np.asarray(waveform, dtype=np.float32), int(sample_rate)


def _decode_and_align(
    aligner: Aligner, batch: list[_Item], max_seconds: float
) -> list[list[Word]]:
    """解碼與推論都是阻塞工作，整段在 threadpool 執行以免佔住 event loop。"""
    waveforms = [_decode_within_limit(item.audio, max_seconds) for item in batch]
    # 拆成平行清單只在此處，因為那是 qwen-asr 的 align() 的形狀。
    return aligner.align(
        waveforms, [item.text for item in batch], [_LANGUAGE] * len(batch)
    )


def _load_qwen_aligner(settings: Settings) -> Aligner:
    aligner = QwenAligner(settings.model_id, settings.device)
    aligner.load()
    return aligner


def _require_aligner(app: FastAPI) -> Aligner:
    """取當前模型。未就緒時明確回 503，而非讓請求撞上 None 拿到 500。"""
    aligner = app.state.aligner
    if aligner is None:
        raise AlignerError(503, "ALIGNER_NOT_READY", "對齊模型尚未就緒。")
    return aligner


def create_app(
    aligner: Aligner | None = None,
    load_aligner: Callable[[], Aligner] | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    settings = settings or Settings()
    loader = load_aligner or (lambda: _load_qwen_aligner(settings))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if app.state.aligner is None:
            try:
                app.state.aligner = loader()
            except Exception:
                # 無 GPU 或權重缺失時不讓容器 crash-loop：服務照常起，
                # 由 /health 回報未就緒，故障狀態可觀測。
                logger.exception("對齊模型載入失敗，服務將回報未就緒。")
        yield

    app = FastAPI(title="Vibe-Vox Aligner", lifespan=lifespan)
    app.state.aligner = aligner
    # 進程內的 GPU 名額，不是跨請求佇列：滿了就拒，不累積待辦。
    app.state.gpu_slots = threading.BoundedSemaphore(settings.max_concurrent_requests)

    async def _on_aligner_error(request, exc: AlignerError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    app.add_exception_handler(AlignerError, _on_aligner_error)

    async def _on_validation_error(request, exc: RequestValidationError) -> JSONResponse:
        # 與 BFF 同慣例：欄位驗證失敗回 400，且統一為 {error:{code,message}} 信封。
        return JSONResponse(
            status_code=400,
            content={"error": {"code": "VALIDATION_ERROR", "message": "請求內容驗證失敗。"}},
        )

    app.add_exception_handler(RequestValidationError, _on_validation_error)

    @app.get("/health")
    async def health() -> JSONResponse:
        # 非 2xx 才能讓 BFF 的狀態碼探測與 docker HEALTHCHECK 一併判定未就緒。
        ready = app.state.aligner is not None
        return JSONResponse(status_code=200 if ready else 503, content={"ready": ready})

    @app.post("/align")
    async def align(
        items: str = Form(...),
        audio: list[UploadFile] = File(...),
    ) -> dict:
        model = _require_aligner(app)
        payloads = [await file.read() for file in audio]
        batch = _build_batch(items, payloads, settings.max_batch_items)

        if not app.state.gpu_slots.acquire(blocking=False):
            raise AlignerError(503, "TOO_MANY_REQUESTS", "對齊服務忙碌中，請退避後重試。")
        try:
            results = await run_in_threadpool(
                _decode_and_align, model, batch, settings.max_audio_seconds
            )
        except AlignerError:
            raise  # 請求本身的問題（解碼失敗、逾長），不可偽裝成推論失敗。
        except Exception as exc:
            logger.exception("對齊推論失敗。")
            raise AlignerError(500, "ALIGN_FAILED", "對齊推論失敗。") from exc
        finally:
            app.state.gpu_slots.release()

        return {"items": [{"words": words} for words in results]}

    return app
