"""#5 ASR 語音轉文字：消費端契約 `POST /api/asr/transcribe`。

回合制批次辨識。回應為消費端契約形狀（不套管理平面的 {data} 信封，見 ADR-0003）。
"""

import json

from fastapi import APIRouter, File, Form, Request, UploadFile

from vibe_qwen.adapters.base import AsrClient
from vibe_qwen.hotword_text import compile_context, enforce_context_budget, sanitize_text

router = APIRouter()

_CHUNK_BYTES = 1 << 16


class InvalidExtraTerms(Exception):
    """本次臨時 extra_terms 非 JSON 字串陣列（端點層映射 → 400）。"""


async def _stream(file: UploadFile):
    while data := await file.read(_CHUNK_BYTES):
        yield data


def _parse_extra_terms(raw: str | None) -> list[str]:
    """解析本次臨時 term；非 JSON 字串陣列即 raise InvalidExtraTerms。"""
    try:
        parsed = json.loads(raw or "[]")
    except json.JSONDecodeError as exc:
        raise InvalidExtraTerms from exc
    if not isinstance(parsed, list) or not all(isinstance(x, str) for x in parsed):
        raise InvalidExtraTerms
    return [t for t in (sanitize_text(x) for x in parsed) if t]


@router.post("/api/asr/transcribe")
async def transcribe(
    request: Request,
    file: UploadFile = File(...),
    extra_terms: str | None = Form(None),
    replace_context: bool = Form(False),
) -> dict:
    settings = request.app.state.settings
    intake = request.app.state.audio_intake
    asr: AsrClient = request.app.state.asr_client
    repo = request.app.state.hotwords

    extra = _parse_extra_terms(extra_terms)
    enabled = [h["term"] for h in repo.list_enabled()]
    context = compile_context(extra if replace_context else enabled + extra)
    enforce_context_budget(context, settings.hotword_context_token_budget)

    guard = request.app.state.heavy_guard
    # guard 涵蓋轉碼 + 辨識，上限設為兩者之和，讓 client 端辨識逾時（→ 504 ASR_TIMEOUT）
    # 先觸發，guard 為總體 backstop。
    async with guard.slot(
        timeout_seconds=settings.asr_timeout_seconds + settings.ffmpeg_timeout_seconds
    ):
        async with intake.transcoded(
            _stream(file), sample_rate=settings.asr_sample_rate
        ) as wav:
            result = await asr.transcribe(wav, context=context)

    return result.model_dump() | {"applied_context": context}
