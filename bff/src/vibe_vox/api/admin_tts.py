"""管理平面的 TTS 輔助端點（/api/admin/tts）。合成本身走消費端契約，見 api/tts.py。

**為什麼需要這個端點**：TN 前處理層唸錯不會回錯誤碼、不進 log，操作者只聽到「唸錯了」而
分不出是前處理錯了還是模型錯了（#46 D9）。把前處理層的輸出攤出來，那個判斷才做得到。

**不另走一條前處理路徑。** 它呼叫 `api/tts.py` 的 `to_utterance`，與合成端點同一個函式——
兩條路各自組一次的話，預覽會變成第二個真相，說對的時候合成仍然可能是錯的，那比沒有預覽
更糟。同一個不變量由 `test_tts.py` 的
test_spoken_form_preview_equals_what_preprocessing_hands_to_the_adapter 守著。

**它停在前處理層，不含 adapter 的字形轉簡**（#51 D1）：送進模型的是簡體，這裡回的是繁體。
那一層是 VoxCPM2 的輸入格式要求而非「台灣人會怎麼唸」，而操作者要讀得懂這段文字才做得了
上面那個判斷。代價是這個回應不再逐字等於送出的字串。
"""

from fastapi import APIRouter, Request
from pydantic import BaseModel

from vibe_vox.api.tts import to_utterance

router = APIRouter()


class SpokenFormRequest(BaseModel):
    """不要 `voice`：預覽不動用音色也不打 GPU，要求一個它用不到的欄位只會讓操作者困惑。"""

    input: str
    instruct: str | None = None


@router.post("/api/admin/tts/spoken-form")
async def preview_spoken_form(body: SpokenFormRequest, request: Request) -> dict:
    """回傳這段文字經前處理層之後的樣子。

    空輸入與過長輸入的行為與合成端點相同（400 `EMPTY_INPUT`／413 `INPUT_TOO_LONG`）：
    預覽要回答「合成會拿到什麼」，而那些輸入的答案是「它不會合成」——回一個空字串會讓
    操作者以為是前處理把內容吃掉了。
    """
    settings = request.app.state.settings
    utterance = to_utterance(
        text=body.input,
        instruct=body.instruct,
        max_chars=settings.tts_max_input_chars,
    )
    return {"data": {"spoken": utterance.text}}
