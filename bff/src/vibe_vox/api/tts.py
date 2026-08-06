"""消費端 TTS 契約（/api/tts/*）：形狀凍結，不套 {data} 信封（ADR-0003）。

完整規格見 docs/api/tts.md。管理平面的音色 CRUD 見 api/admin_voices.py。
"""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import Response
from pydantic import BaseModel

from vibe_vox.adapters.base import CONTRACT_SPEC, Utterance
from vibe_vox.audio.wav import wrap_pcm
from vibe_vox.persistence.voices import VoiceNotFound
from vibe_vox.tts_text import has_speakable_content

router = APIRouter()

MODEL_NAME = "voxcpm2"

# 契約 §5.3 的三個容器中，mp3 尚未實作（需要編碼器；#6 未列為驗收項）。它留在契約裡
# 但在此拒絕，而不是回一段標成 audio/mpeg 的 wav——後者會讓消費端拿到解不開的資料。
#
# L16 的 rate 參數由 CONTRACT_SPEC 導出而非寫死：RFC 2586 把 rate 列為 required，
# 裸的 audio/L16 不合規，而裸 PCM 沒有標頭可讓消費端讀出取樣率——那個數字只能從
# Content-Type 來。
_CONTENT_TYPES = {
    "wav": "audio/wav",
    "pcm": f"audio/L16;rate={CONTRACT_SPEC.sample_rate}",
}


class EmptyInput(Exception):
    """input 清洗後無可合成的內容。"""


class UnsupportedModel(Exception):
    """model 不在 GET /api/tts/models 的清單中。"""


class UnsupportedResponseFormat(Exception):
    """response_format 非本服務可產出的容器。"""


class InputTooLong(Exception):
    """input 超過單次合成的長度上限。"""

    def __init__(self, length: int, limit: int) -> None:
        super().__init__(length, limit)
        self.length = length
        self.limit = limit


class StreamUnsupported(Exception):
    """要求分塊串流，但本服務尚未實作。"""


class SpeechRequest(BaseModel):
    input: str
    voice: str
    model: str | None = None
    response_format: str = "wav"
    instruct: str | None = None
    # 宣告 stream 是為了能拒絕它。省略此欄位的話 pydantic 預設會靜默丟棄，消費端拿到
    # 一整包卻以為在串流，依契約 §9 的 chunk 閒置逾時判斷會把正常的回合判成失敗。
    stream: bool = False


# 明寫 responses 而非讓 FastAPI 推導：成功回的是二進位音訊，預設會標成
# application/json，用 /openapi.json 產 client 的人會得到一個把 wav body 當 JSON 解的
# client。**已知殘留**：FastAPI 對每個帶 body 的端點都自動宣告 422，而 main.py 把
# RequestValidationError 一律轉成 400，那個 422 永遠不會發生；openapi_extra 是合併不是
# 取代，拿不掉它。
_OPENAPI_RESPONSES = {
    "responses": {
        "200": {
            "description": "合成後的音訊",
            "content": {
                content_type: {"schema": {"type": "string", "format": "binary"}}
                for content_type in _CONTENT_TYPES.values()
            },
        },
        "400": {
            "description": "VALIDATION_ERROR／UNSUPPORTED_MODEL／"
            "UNSUPPORTED_RESPONSE_FORMAT／STREAM_UNSUPPORTED／EMPTY_INPUT"
        },
        "404": {"description": "VOICE_NOT_FOUND"},
        "413": {"description": "INPUT_TOO_LONG"},
        "502": {"description": "TTS_UNAVAILABLE"},
        "503": {"description": "TOO_MANY_REQUESTS"},
        "504": {"description": "TTS_TIMEOUT／REQUEST_TIMEOUT"},
    }
}


def _to_consumer(v: dict) -> dict:
    """消費端只看得到挑音色需要的四欄，不外露參考音路徑與逐字稿。"""
    return {
        "id": v["id"],
        "name": v["name"],
        "type": v["type"],
        "language": v["language"],
    }


@router.get("/api/tts/models")
async def list_models() -> dict:
    return {"models": [MODEL_NAME]}


@router.get("/api/tts/voices")
async def list_voices(request: Request) -> dict:
    repo = request.app.state.voices
    return {"voices": [_to_consumer(v) for v in repo.list()]}


def _reject_unsupported_options(body: SpeechRequest) -> None:
    """擋掉本服務產不出來的請求，在動用音色與 GPU 之前。

    stream 先驗：契約 §6 的 STREAM_FORMAT_UNSUPPORTED 列明寫「目前 stream: true 一律
    先撞上 STREAM_UNSUPPORTED」，若順序倒過來，stream+mp3 會回 UNSUPPORTED_RESPONSE_FORMAT
    而與契約表格不符。
    """
    if body.stream:
        raise StreamUnsupported()
    if body.model is not None and body.model != MODEL_NAME:
        raise UnsupportedModel(body.model)
    if body.response_format not in _CONTENT_TYPES:
        raise UnsupportedResponseFormat(body.response_format)


def _to_utterance(body: SpeechRequest, *, max_chars: int) -> Utterance:
    """把請求的文字欄位清洗成一句可合成的內容。

    一次請求只承載一種語氣（契約 §5.2），故整段共用同一個 instruct；切句尚未實作。
    控制語法的中性化不在此處——那是 Utterance 自己的不變量（見 adapters/base.py）。
    """
    text = body.input.strip()
    if not has_speakable_content(text):
        raise EmptyInput()
    if len(text) > max_chars:
        raise InputTooLong(len(text), max_chars)

    # 純空白的 instruct 視同沒給：adapter 會把它組成「(   )」前綴，而括號不被剝除，
    # 模型會把空前綴當成要處理的內容，而非依契約 §5.2 退回音色本身的語氣。
    instruct = (body.instruct or "").strip()
    return Utterance(text=text, instruct=instruct or None)


@router.post("/api/tts/speech", response_class=Response, openapi_extra=_OPENAPI_RESPONSES)
async def synthesize_speech(body: SpeechRequest, request: Request) -> Response:
    """合成語音，回二進位音訊。**成功不是 JSON**，錯誤才是（docs/api/tts.md §6）。"""
    _reject_unsupported_options(body)

    voice = request.app.state.voices.get(body.voice)
    if voice is None:
        raise VoiceNotFound(body.voice)

    settings = request.app.state.settings
    utterance = _to_utterance(body, max_chars=settings.tts_max_input_chars)

    # 只有合成進 guard：查音色與驗欄位是輕量的，佔一個併發額度沒有意義。額度與 ASR
    # 共用（契約 §5.5），因為兩者搶的是同一張卡。預算另計，理由見 tts_request_budget。
    guard = request.app.state.heavy_guard
    async with guard.slot(timeout_seconds=settings.tts_request_budget()):
        audio = await request.app.state.tts_client.synthesize(
            [utterance], reference_audio=Path(voice["ref_audio_path"])
        )

    # 容器化在此決定：adapter 回帶規格的 PCM，pcm 直出裸資料、wav 才包標頭。
    body_bytes = audio.frames if body.response_format == "pcm" else wrap_pcm(audio)
    return Response(content=body_bytes, media_type=_CONTENT_TYPES[body.response_format])
