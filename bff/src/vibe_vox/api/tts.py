"""消費端 TTS 契約（/api/tts/*）：形狀凍結，不套 {data} 信封（ADR-0003）。

完整規格見 docs/api/tts.md。管理平面的音色 CRUD 見 api/admin_voices.py。
"""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import Response
from pydantic import BaseModel

from vibe_vox.adapters.base import CONTRACT_SPEC, Utterance
from vibe_vox.audio.reference import unusable_reason
from vibe_vox.audio.wav import wrap_pcm
from vibe_vox.persistence.voices import VoiceNotFound
from vibe_vox.tts_g2p import lock_taiwan_readings
from vibe_vox.tts_text import to_speakable
from vibe_vox.tts_tn import to_spoken_form

router = APIRouter()

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


class VoiceUnusable(Exception):
    """音色存在，但它的參考音不可用（讀不到，或時長超出模型端的硬界）。

    reason 是給人看的具體原因，來自 `audio/reference.py`。往上帶而非在映射層寫死一句
    籠統的話：消費端只能換音色，但那條訊息是排查時唯一的線索。
    """

    def __init__(self, vid: str, reason: str) -> None:
        super().__init__(vid, reason)
        self.vid = vid
        self.reason = reason


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
        "409": {"description": "VOICE_UNUSABLE"},
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
async def list_models(request: Request) -> dict:
    # 由 settings 導出而非寫死：這個字串同時是 adapter 送給 tts 服務的 `model`，兩者
    # 分家的症狀是每次合成 502（tts 服務註冊的是別的 ID）。同一個不變量在部署側由
    # bff/tests/test_config.py 的 test_tts_serves_the_model_id_the_bff_asks_for 守著。
    return {"models": [request.app.state.settings.tts_served_name]}


@router.get("/api/tts/voices")
async def list_voices(request: Request) -> dict:
    repo = request.app.state.voices
    return {"voices": [_to_consumer(v) for v in repo.list()]}


def _reject_unsupported_options(body: SpeechRequest, *, model_name: str) -> None:
    """擋掉本服務產不出來的請求，在動用音色與 GPU 之前。

    stream 先驗：契約 §6 的 STREAM_FORMAT_UNSUPPORTED 列明寫「目前 stream: true 一律
    先撞上 STREAM_UNSUPPORTED」，若順序倒過來，stream+mp3 會回 UNSUPPORTED_RESPONSE_FORMAT
    而與契約表格不符。

    `model_name` 由呼叫端傳入而非在此讀設定：接受什麼與 `GET /api/tts/models` 報什麼
    必須是同一個值，讓它們各自取一次就有機會分家。
    """
    if body.stream:
        raise StreamUnsupported()
    if body.model is not None and body.model != model_name:
        raise UnsupportedModel(body.model)
    if body.response_format not in _CONTENT_TYPES:
        raise UnsupportedResponseFormat(body.response_format)


def to_utterance(*, text: str, instruct: str | None, max_chars: int) -> Utterance:
    """把請求的文字欄位清洗成一句可合成的內容。

    收欄位而非收 `SpeechRequest`：管理平面的預覽端點沒有 `voice` 可給（見
    api/admin_tts.py），而它必須走這同一個函式，否則預覽與合成會各自組出一份字串。

    一次請求只承載一種語氣（契約 §5.2），故整段共用同一個 instruct；切句尚未實作。

    本層只做「文字層的事實 → HTTP 狀態碼」的映射。中性化、判空與 instruct 的正規化
    都不在此：前兩者由 `to_speakable` 一併完成（分開就會量錯順序），後者是 Utterance
    自己的不變量（見 adapters/base.py）。

    **長度上限量在展開之前。** 上限是「語音長度」的代理值（用途是擋整篇文章佔住 GPU），
    而 TN 不改變語音長度——`NT$1,250` 與「新臺幣一千二百五十元」唸起來一樣長，字元數卻
    從 8 變成 10。量在展開後的話，消費端會為一個它算得出來是合法的長度收到 413，而它
    無法預測展開會膨脹多少（契約 §5.1）。
    """
    speakable = to_speakable(text)
    if speakable is None:
        raise EmptyInput()
    # 量的是中性化後的長度：被移除的字元不該算進額度（見 to_speakable）。
    if len(speakable) > max_chars:
        raise InputTooLong(len(speakable), max_chars)
    # 順序不可倒置：TN 會把 `{le4}` 的聲調數字展開成 `{le四}`（#46 D7 實測），故鎖讀音
    # 必須在 TN 之後。
    return Utterance(
        text=lock_taiwan_readings(to_spoken_form(speakable)), instruct=instruct
    )


@router.post("/api/tts/speech", response_class=Response, openapi_extra=_OPENAPI_RESPONSES)
async def synthesize_speech(body: SpeechRequest, request: Request) -> Response:
    """合成語音，回二進位音訊。**成功不是 JSON**，錯誤才是（docs/api/tts.md §6）。"""
    settings = request.app.state.settings
    _reject_unsupported_options(body, model_name=settings.tts_served_name)

    voice = request.app.state.voices.get(body.voice)
    if voice is None:
        raise VoiceNotFound(body.voice)
    # 參考音的可用性是 Voice 建立時的不變量（audio/reference.py），但那只涵蓋建立路徑：
    # 該不變量之前建立的音色未經驗證，而 DB 還原、volume 換掛與人工刪檔也都在它之外。
    #
    # **用同一組判準而非只檢查檔案存在。** 只檢查存在的話，超界的既有音色照樣被送出去，
    # 而模型端對超界參考音回的是 ValueError 的文字，adapter 只能翻成 502 TTS_UNAVAILABLE
    # ——契約 §6 把該碼標為可重試，消費端於是退避重試一個永久失敗（#44）。同時管理平面
    # 會標它不可用，兩邊各自說一套。
    #
    # 代價是每次合成多一次時長量測：wav 只讀標頭（不起子進程），非 wav 付一次 ffprobe。
    # 相對一次合成本身（實測 0.66 秒）可接受，換掉的是消費端無止盡的重試。
    reference_audio = Path(voice["ref_audio_path"])
    reason = await unusable_reason(reference_audio)
    if reason is not None:
        raise VoiceUnusable(body.voice, reason)
    utterance = to_utterance(
        text=body.input,
        instruct=body.instruct,
        max_chars=settings.tts_max_input_chars,
    )

    # 只有合成進 guard：查音色與驗欄位相對合成是輕量的，佔一個併發額度沒有意義。**上面
    # 的可用性檢查是個例外**——非 wav 的參考音會在 guard 之外起一個 ffprobe 子進程，故
    # 那道檢查的併發不受 max_concurrent_heavy_requests 約束。它不吃 GPU，量的是檔頭。額度與 ASR
    # 共用（契約 §5.5），因為兩者搶的是同一張卡。預算另計，理由見 tts_request_budget。
    guard = request.app.state.heavy_guard
    async with guard.slot(timeout_seconds=settings.tts_request_budget()):
        audio = await request.app.state.tts_client.synthesize(
            [utterance], reference_audio=reference_audio
        )

    # 容器化在此決定：adapter 回帶規格的 PCM，pcm 直出裸資料、wav 才包標頭。
    body_bytes = audio.frames if body.response_format == "pcm" else wrap_pcm(audio)
    return Response(content=body_bytes, media_type=_CONTENT_TYPES[body.response_format])
