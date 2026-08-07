"""#5 ASR 語音轉文字：消費端契約 `POST /api/asr/transcribe`。

回合制批次辨識。回應為消費端契約形狀（不套管理平面的 {data} 信封，見 ADR-0003）。

#28 起附字級時間戳（ADR-0004）。對齊是附加功能，其失效不得使逐字稿一併不可得——該
降級由 `AlignerClient.align` 保證（它不拋出，逐段以 omission 說明原因），故本層沒有
對齊相關的例外處理，也**不往上映射成 502／504**。
"""

import json

from fastapi import APIRouter, File, Form, Request, UploadFile

from vibe_vox.adapters.base import AlignerClient, AsrClient
from vibe_vox.alignment import merge_alignment
from vibe_vox.audio.slice import wav_duration
from vibe_vox.hotword_text import compile_context, enforce_context_budget, sanitize_text

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
    aligner: AlignerClient = request.app.state.aligner_client
    repo = request.app.state.hotwords

    extra = _parse_extra_terms(extra_terms)
    enabled = [h["term"] for h in repo.list_enabled()]
    context = compile_context(extra if replace_context else enabled + extra)
    enforce_context_budget(context, settings.hotword_context_token_budget)

    guard = request.app.state.heavy_guard
    # guard 涵蓋轉碼 + 辨識 + 對齊，且含餘裕（見 config.HEAVY_GUARD_MARGIN），讓各
    # client 自身的逾時（→ 504 ASR_TIMEOUT／對齊降級）先觸發，guard 為總體 backstop。
    # 預算由 Settings 計算而非在此相加，測試用同一個方法比對 nginx 的逾時（#35）。
    async with guard.slot(timeout_seconds=settings.heavy_request_budget()):
        async with intake.transcoded(
            _stream(file), sample_rate=settings.asr_sample_rate
        ) as wav:
            result = await asr.transcribe(wav, context=context)
            audio_duration = wav_duration(wav)
            # 對齊不可得不會拋出：降級是 AlignerClient 的保證，端點層無須攔截，
            # 也無從攔截——降級後仍要每段的切片範圍，而那只有 adapter 算得出來。
            alignments = await aligner.align(wav, result.segments)

    segments, alignment = merge_alignment(
        result.segments, alignments, audio_duration=audio_duration
    )
    return result.model_dump() | {
        "segments": [s.model_dump() for s in segments],
        # duration 的定義不變（所有 Segment 的 End 最大值），但段界已重算為末字 End，
        # 故其值隨對齊改變。
        "duration": max((s.End for s in segments), default=0.0),
        "alignment": alignment.model_dump(),
        "applied_context": context,
    }
