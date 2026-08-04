"""BFF 應用工廠。模型呼叫一律經注入的 AsrClient/TtsClient adapter。"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

from vibe_vox.adapters.aligner import HttpAlignerClient
from vibe_vox.adapters.base import AlignerClient, AsrClient, TtsClient
from vibe_vox.adapters.stub import (
    DEFAULT_STUB_ASR_RESULT,
    StubAlignerClient,
    StubAsrClient,
    StubTtsClient,
)
from vibe_vox.adapters.vllm_asr import AsrTimeout, AsrUnavailable, VllmAsrClient
from vibe_vox.api.admin_hotwords import router as admin_hotwords_router
from vibe_vox.api.asr import InvalidExtraTerms, router as asr_router
from vibe_vox.api.health import router as health_router
from vibe_vox.api.hotwords import router as hotwords_router
from vibe_vox.audio.errors import (
    FileTooLarge,
    TranscodeError,
    TranscodeTimeout,
    UnsupportedAudioFormat,
)
from vibe_vox.audio.intake import AudioIntake
from vibe_vox.config import Settings
from vibe_vox.files.cleanup import cleanup_expired_temp_files
from vibe_vox.hotword_io import ImportLimitExceeded, ImportParseError
from vibe_vox.hotword_text import ContextBudgetExceeded, InvalidHotwordTerm
from vibe_vox.middleware.limits import HeavyRequestGuard, HeavyRequestRejected
from vibe_vox.middleware.origin import OriginGuardMiddleware
from vibe_vox.persistence.hotwords import HotwordNotFound, HotwordRepository


def _default_asr_client(settings: Settings) -> AsrClient:
    """dev（無 GPU）用 stub 回假結果；否則接遠端 vLLM。"""
    if settings.use_stub_models:
        return StubAsrClient(result=DEFAULT_STUB_ASR_RESULT)
    return VllmAsrClient(
        settings.asr_base_url,
        settings.asr_served_name,
        timeout=settings.asr_timeout_seconds,
    )


def _default_aligner_client(settings: Settings) -> AlignerClient:
    """dev（無 GPU）用 stub 回全段未對齊；否則接 aligner 服務。"""
    if settings.use_stub_models:
        return StubAlignerClient()
    return HttpAlignerClient(
        settings.aligner_base_url,
        timeout=settings.aligner_timeout_seconds,
        slice_buffer_seconds=settings.aligner_slice_buffer_seconds,
        max_batch_items=settings.aligner_max_batch_items,
    )


def create_app(
    asr_client: AsrClient | None = None,
    tts_client: TtsClient | None = None,
    settings: Settings | None = None,
    audio_intake: AudioIntake | None = None,
    aligner_client: AlignerClient | None = None,
) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        cleanup_expired_temp_files(settings.temp_dir, settings.temp_max_age_seconds)
        yield

    app = FastAPI(title="Vibe-Vox BFF", lifespan=lifespan)
    app.state.settings = settings
    app.state.asr_client = asr_client or _default_asr_client(settings)
    app.state.aligner_client = aligner_client or _default_aligner_client(settings)
    app.state.tts_client = tts_client or StubTtsClient()
    app.state.audio_intake = audio_intake or AudioIntake(
        temp_dir=settings.temp_dir,
        max_bytes=settings.audio_max_bytes,
        timeout_seconds=settings.ffmpeg_timeout_seconds,
    )
    app.state.hotwords = HotwordRepository(settings.db_path)
    app.state.heavy_guard = HeavyRequestGuard(
        max_concurrent=settings.max_concurrent_heavy_requests,
        timeout_seconds=settings.request_timeout_seconds,
    )

    async def _on_heavy_rejected(request, exc: HeavyRequestRejected) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    app.add_exception_handler(HeavyRequestRejected, _on_heavy_rejected)

    async def _on_invalid_term(request, exc: InvalidHotwordTerm) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "INVALID_HOTWORD_TERM",
                    "message": "Hotword term 清洗後無有效內容，請提供有效詞彙。",
                }
            },
        )

    app.add_exception_handler(InvalidHotwordTerm, _on_invalid_term)

    async def _on_context_budget(request, exc: ContextBudgetExceeded) -> JSONResponse:
        return JSONResponse(
            status_code=413,
            content={
                "error": {
                    "code": "CONTEXT_BUDGET_EXCEEDED",
                    "message": f"context 估算 {exc.estimate} tokens 超過上限 {exc.budget}，請停用部分 Hotword。",
                }
            },
        )

    app.add_exception_handler(ContextBudgetExceeded, _on_context_budget)

    async def _on_import_limit(request, exc: ImportLimitExceeded) -> JSONResponse:
        return JSONResponse(
            status_code=413,
            content={"error": {"code": "IMPORT_LIMIT_EXCEEDED", "message": exc.message}},
        )

    app.add_exception_handler(ImportLimitExceeded, _on_import_limit)

    async def _on_import_parse(request, exc: ImportParseError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={"error": {"code": "IMPORT_PARSE_ERROR", "message": exc.message}},
        )

    app.add_exception_handler(ImportParseError, _on_import_parse)

    async def _on_hotword_not_found(request, exc: HotwordNotFound) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "HOTWORD_NOT_FOUND", "message": "找不到指定的 Hotword。"}},
        )

    app.add_exception_handler(HotwordNotFound, _on_hotword_not_found)

    async def _on_validation_error(request, exc: RequestValidationError) -> JSONResponse:
        # spec：格式或欄位驗證失敗回 400，並統一為 {error:{code,message}} 信封。
        return JSONResponse(
            status_code=400,
            content={"error": {"code": "VALIDATION_ERROR", "message": "請求內容驗證失敗。"}},
        )

    app.add_exception_handler(RequestValidationError, _on_validation_error)

    # 端點層例外 → 統一 HTTP 信封（#4 音檔模組例外、ASR extra_terms 驗證）。
    def _error_handler(status: int, code: str, message: str):
        async def handler(request, exc) -> JSONResponse:
            return JSONResponse(
                status_code=status,
                content={"error": {"code": code, "message": message}},
            )

        return handler

    app.add_exception_handler(
        FileTooLarge, _error_handler(413, "FILE_TOO_LARGE", "上傳音檔超過大小上限。")
    )
    app.add_exception_handler(
        UnsupportedAudioFormat,
        _error_handler(400, "UNSUPPORTED_AUDIO_FORMAT", "不支援的音檔格式。"),
    )
    app.add_exception_handler(
        TranscodeError,
        _error_handler(400, "TRANSCODE_ERROR", "音檔轉碼失敗，可能非有效音訊。"),
    )
    app.add_exception_handler(
        TranscodeTimeout,
        _error_handler(504, "TRANSCODE_TIMEOUT", "音檔轉碼逾時。"),
    )
    app.add_exception_handler(
        InvalidExtraTerms,
        _error_handler(400, "INVALID_EXTRA_TERMS", "extra_terms 需為 JSON 字串陣列。"),
    )
    app.add_exception_handler(
        AsrTimeout, _error_handler(504, "ASR_TIMEOUT", "語音辨識服務回應逾時。")
    )
    app.add_exception_handler(
        AsrUnavailable,
        _error_handler(502, "ASR_UNAVAILABLE", "語音辨識服務暫時無法使用。"),
    )

    app.add_middleware(OriginGuardMiddleware, allowed_origins=settings.allowed_origins)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,  # 顯式清單，不用萬用字元 *
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health_router)
    app.include_router(hotwords_router)
    app.include_router(admin_hotwords_router)
    app.include_router(asr_router)
    return app
