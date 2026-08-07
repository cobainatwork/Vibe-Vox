"""TTS 合成端點：在 BFF HTTP seam 驗證消費端契約（docs/api/tts.md §5）。

模型呼叫以 StubTtsClient 取代，故測試離線、確定性、無需 GPU。
"""

import asyncio
import io
import wave

from fastapi.testclient import TestClient

from vibe_vox.adapters.base import CONTRACT_SPEC, TtsTimeout, TtsUnavailable
from vibe_vox.adapters.stub import StubTtsClient
from vibe_vox.adapters.vllm_omni_tts import VllmOmniTtsClient
from vibe_vox.audio.wav import PcmAudio
from vibe_vox.config import Settings
from vibe_vox.main import create_app

_RATE = 24000


def _wav_bytes(seconds: float = 5.0) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(_RATE)
        w.writeframes(b"\x00\x00" * int(seconds * _RATE))
    return buf.getvalue()


def _client(tmp_path) -> TestClient:
    return _client_with(tmp_path, StubTtsClient())


def _create_voice(client, name: str = "客戶 A") -> dict:
    resp = client.post(
        "/api/admin/voices/clone",
        data={"name": name, "language": "zh-TW"},
        files={"ref_audio": ("ref.wav", _wav_bytes(), "audio/wav")},
    )
    assert resp.status_code == 201
    return resp.json()["data"]


def test_speech_returns_wav_audio(tmp_path):
    client = _client(tmp_path)
    voice = _create_voice(client)

    resp = client.post(
        "/api/tts/speech",
        json={"input": "您好，我想了解一下這張保單的內容。", "voice": voice["id"]},
    )

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "audio/wav"
    with wave.open(io.BytesIO(resp.content), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getframerate() == _RATE


def test_models_endpoint_reports_the_name_the_adapter_sends(tmp_path):
    # `GET /api/tts/models` 宣告的名稱必須就是 adapter 送給 tts 服務的 `model`。
    # 寫死時，用 .env 覆寫 VIBE_VOX_TTS_SERVED_NAME 會讓兩者分家：清單仍報 voxcpm2，
    # 消費端照清單送 voxcpm2 被端點接受、再被 vLLM 以 4xx 拒絕（502），而送真正註冊
    # 的名字反而在端點就被 400 UNSUPPORTED_MODEL 擋下。
    client = _client_with(tmp_path, StubTtsClient(), tts_served_name="voxcpm2-tw")

    assert client.get("/api/tts/models").json() == {"models": ["voxcpm2-tw"]}


def test_speech_accepts_the_model_name_the_list_reports(tmp_path):
    client = _client_with(tmp_path, StubTtsClient(), tts_served_name="voxcpm2-tw")
    voice = _create_voice(client)

    resp = client.post(
        "/api/tts/speech",
        json={"input": "測試", "voice": voice["id"], "model": "voxcpm2-tw"},
    )

    assert resp.status_code == 200


def test_speech_unknown_voice_returns_404(tmp_path):
    client = _client(tmp_path)

    resp = client.post("/api/tts/speech", json={"input": "測試", "voice": "nope"})

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "VOICE_NOT_FOUND"


def test_speech_with_out_of_range_reference_audio_is_not_reported_as_retryable(tmp_path):
    """超界的既有音色不能報成可重試的 502。**這是 #44 的原始缺陷本身。**

    模型端對超界的參考音回 ValueError 的文字而非音訊，adapter 只能把它翻成 502
    TTS_UNAVAILABLE，而契約 §6 把該碼標為可重試——消費端於是依契約退避重試一個永久失敗。
    建立時的驗證擋不到本票之前建立的音色，故合成路徑的 backstop 要用同一組判準，不能只
    檢查檔案存在：否則管理平面說某個音色不可用，合成路徑照樣把它送出去。
    """
    client = _client(tmp_path)
    voice = _create_voice(client)
    # 把參考音換成 40 秒的檔案，等同未經驗證就建立的既有音色。
    for f in (tmp_path / "voices").iterdir():
        f.write_bytes(_wav_bytes(40.0))

    resp = client.post("/api/tts/speech", json={"input": "測試", "voice": voice["id"]})

    assert resp.status_code == 409
    body = resp.json()["error"]
    assert body["code"] == "VOICE_UNUSABLE"
    assert "40" in body["message"]  # 訊息要說出實際的問題，否則排查只能靠猜


def test_speech_with_unreadable_reference_audio_stays_within_the_contract(tmp_path):
    """參考音檔在伺服器上讀不到時回契約內的錯誤，不是 500、也不是靜默成功。

    建立時的不變量只涵蓋建立路徑（audio/reference.py）；DB 還原、volume 換掛、人工刪檔
    都在它之外，而那三者在測試區都會發生。契約 §6 的錯誤表沒有 500 這一列——消費端拿到
    非契約形狀的回應，它的錯誤處理分支涵蓋不到。

    碼不是可重試的 502：重試同一個音色永遠不會成功，能修的只有操作者。也不是 404——
    音色還在清單裡，回 404 會讓依契約重拉清單的消費端看到它仍在而再送一次。
    """
    client = _client(tmp_path)
    voice = _create_voice(client)
    for f in (tmp_path / "voices").iterdir():
        f.unlink()

    resp = client.post("/api/tts/speech", json={"input": "測試", "voice": voice["id"]})

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "VOICE_UNUSABLE"


class _FailingTtsClient:
    """合成一律拋指定例外，驗證 adapter 的失敗如何映射成消費端錯誤碼。"""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def health(self) -> bool:
        return True

    async def synthesize(self, utterances, *, reference_audio):
        raise self._exc


def _client_with(tmp_path, tts_client, **settings_kw) -> TestClient:
    return TestClient(
        create_app(
            tts_client=tts_client,
            settings=Settings(
                db_path=tmp_path / "t.db", voice_dir=tmp_path / "voices", **settings_kw
            ),
        )
    )


def test_speech_upstream_timeout_returns_504(tmp_path):
    client = _client_with(tmp_path, _FailingTtsClient(TtsTimeout()))
    voice = _create_voice(client)

    resp = client.post("/api/tts/speech", json={"input": "測試", "voice": voice["id"]})

    assert resp.status_code == 504
    assert resp.json()["error"]["code"] == "TTS_TIMEOUT"


def test_speech_upstream_unavailable_returns_502(tmp_path):
    client = _client_with(tmp_path, _FailingTtsClient(TtsUnavailable()))
    voice = _create_voice(client)

    resp = client.post("/api/tts/speech", json={"input": "測試", "voice": voice["id"]})

    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "TTS_UNAVAILABLE"


def test_concurrent_synthesis_beyond_limit_is_load_shed(tmp_path):
    # 契約 §5.5：併發額度與 ASR 共用，達上限直接 503 不排隊。合成佔 GPU，排隊只會讓
    # 後到的請求等到逾時。
    from httpx import ASGITransport, AsyncClient

    release = asyncio.Event()

    class _BlockingTts:
        async def health(self):
            return True

        async def synthesize(self, utterances, *, reference_audio):
            await release.wait()
            return PcmAudio(b"\x00\x00", CONTRACT_SPEC)

    app = create_app(
        settings=Settings(
            db_path=tmp_path / "t.db",
            voice_dir=tmp_path / "voices",
            max_concurrent_heavy_requests=1,
        ),
        tts_client=_BlockingTts(),
    )
    voice = _create_voice(TestClient(app))
    body = {"input": "測試", "voice": voice["id"]}

    async def scenario():
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as ac:
            first = asyncio.create_task(ac.post("/api/tts/speech", json=body))
            await asyncio.sleep(0.05)  # 讓 first 占住唯一 slot
            # 逾時保護：guard 不存在時 second 會跟著卡在同一個 event 上，那是死鎖而
            # 非失敗，測試會掛住而不是紅燈。
            second = await asyncio.wait_for(
                ac.post("/api/tts/speech", json=body), timeout=5.0
            )
            release.set()
            return await first, second

    first_resp, second = asyncio.run(scenario())

    assert second.status_code == 503
    assert second.json()["error"]["code"] == "TOO_MANY_REQUESTS"
    assert first_resp.status_code == 200


def test_openapi_describes_a_binary_response_not_json(tmp_path):
    # 成功回應是二進位音訊、錯誤才是 JSON（契約 §6 開頭的警告）。OpenAPI 若把 200 標成
    # application/json，用它產 client 的人會拿到一個把 wav body 當 JSON 解的 client。
    #
    # **已知未修**：FastAPI 對每個帶 body 的端點都自動宣告 422，而 main.py 把
    # RequestValidationError 一律轉成 400，故那個 422 永遠不會發生。`openapi_extra`
    # 是合併不是取代，拿不掉它；要修得對所有端點動手，不屬本票。
    app = create_app(
        settings=Settings(db_path=tmp_path / "t.db", voice_dir=tmp_path / "voices"),
        tts_client=StubTtsClient(),
    )

    responses = app.openapi()["paths"]["/api/tts/speech"]["post"]["responses"]

    assert set(responses["200"]["content"]) == {"audio/wav", "audio/L16;rate=24000"}


def test_tts_client_selection_follows_stub_setting(tmp_path):
    stub_app = create_app(settings=Settings(db_path=tmp_path / "s.db", use_stub_models=True))
    assert isinstance(stub_app.state.tts_client, StubTtsClient)

    real_app = create_app(settings=Settings(db_path=tmp_path / "r.db", use_stub_models=False))
    assert isinstance(real_app.state.tts_client, VllmOmniTtsClient)


def test_speech_empty_input_rejected(tmp_path):
    client = _client(tmp_path)
    voice = _create_voice(client)

    resp = client.post("/api/tts/speech", json={"input": "   ", "voice": voice["id"]})

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "EMPTY_INPUT"


def test_speech_punctuation_only_input_rejected(tmp_path):
    # 契約 §7：只有標點或空白視同空輸入。送出去只會拿到一段沒有內容的音訊。
    client = _client(tmp_path)
    voice = _create_voice(client)

    resp = client.post(
        "/api/tts/speech", json={"input": "。。。！？", "voice": voice["id"]}
    )

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "EMPTY_INPUT"


def test_speech_emoji_only_input_rejected(tmp_path):
    # emoji 不發音（契約 §7），故整句都是 emoji 等同沒有可合成的內容。
    client = _client(tmp_path)
    voice = _create_voice(client)

    resp = client.post("/api/tts/speech", json={"input": "🎉🎉", "voice": voice["id"]})

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "EMPTY_INPUT"


def test_speech_input_that_neutralization_empties_is_rejected(tmp_path):
    # 契約 §6 的 EMPTY_INPUT 明寫「input 為空，**或經正規化後為空**」。`<|...|>` 特殊
    # token 標記整段會被中性化移除（tts_text 的 _SPECIAL_TOKEN），故這種輸入正規化後
    # 就是空的。
    #
    # 判空若量在中性化之前，`i`／`m`／`e`／`n`／`d` 這些字母會讓它通過：請求佔一個
    # heavy guard 額度、打一次 GPU、回一段空音訊 200。判空與中性化必須是同一個步驟。
    client = _client(tmp_path)
    voice = _create_voice(client)

    resp = client.post(
        "/api/tts/speech", json={"input": "<|im_end|>", "voice": voice["id"]}
    )

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "EMPTY_INPUT"


def test_speech_length_limit_applies_after_neutralization(tmp_path):
    # 長度同樣量在中性化之後：`<|...|>` 標記會被整段移除，拿它撐長度等於用一段不會被
    # 合成的內容換掉真正的額度。反過來也成立——正常文字不該因為含這種標記而被誤擋。
    client = _client_with(tmp_path, StubTtsClient(), tts_max_input_chars=10)
    voice = _create_voice(client)

    resp = client.post(
        "/api/tts/speech",
        json={"input": "<|im_end|>你好嗎", "voice": voice["id"]},
    )

    assert resp.status_code == 200  # 中性化後只剩 3 字，未超過上限 10


def test_speech_overlong_input_rejected_with_limits_in_message(tmp_path):
    # 契約 §6：413 的 message 要含實際值與上限，否則消費端只能猜要砍到多短。
    client = _client_with(tmp_path, StubTtsClient(), tts_max_input_chars=10)
    voice = _create_voice(client)

    resp = client.post(
        "/api/tts/speech", json={"input": "一" * 11, "voice": voice["id"]}
    )

    assert resp.status_code == 413
    body = resp.json()["error"]
    assert body["code"] == "INPUT_TOO_LONG"
    assert "11" in body["message"] and "10" in body["message"]


def test_speech_unknown_model_rejected(tmp_path):
    client = _client(tmp_path)
    voice = _create_voice(client)

    resp = client.post(
        "/api/tts/speech",
        json={"input": "測試", "voice": voice["id"], "model": "qwen3-tts"},
    )

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "UNSUPPORTED_MODEL"


def _sent_utterance(tmp_path, body: dict):
    """跑一次合成，取出實際交給 TtsClient 的那一句。"""
    stub = StubTtsClient()
    client = _client_with(tmp_path, stub)
    voice = _create_voice(client)
    resp = client.post("/api/tts/speech", json={"voice": voice["id"], **body})
    assert resp.status_code == 200
    return stub.last_utterances[0]


def test_spoken_form_preview_equals_what_synthesis_sends(tmp_path):
    """管理平面的預覽必須與合成實際送出的字串逐字相同。

    這是預覽這個功能的全部價值：操作者聽到唸錯時要能分辨是前處理錯了還是模型錯了。兩者
    各走一條路的話，預覽會變成第二個真相——它說對的時候合成仍然可能是錯的，而那比沒有
    預覽更糟。
    """
    stub = StubTtsClient()
    client = _client_with(tmp_path, stub)
    voice = _create_voice(client)
    text = "重量 3kg，總價 NT$1,250，2026/8/5 交貨"

    preview = client.post("/api/admin/tts/spoken-form", json={"input": text})
    assert preview.status_code == 200

    assert client.post(
        "/api/tts/speech", json={"input": text, "voice": voice["id"]}
    ).status_code == 200

    assert preview.json()["data"]["spoken"] == stub.last_utterances[0].text


def test_spoken_form_preview_rejects_empty_input_like_synthesis_does(tmp_path):
    # 預覽要回答「合成會拿到什麼」，而這種輸入的答案是「它不會合成」。回一個空字串會讓
    # 操作者以為前處理把內容吃掉了。
    client = _client(tmp_path)

    resp = client.post("/api/admin/tts/spoken-form", json={"input": "<|im_end|>"})

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "EMPTY_INPUT"


def test_input_reaches_the_model_in_taiwan_spoken_form(tmp_path):
    # 契約 §7 逐字承諾了這件事（`3kg` → 三公斤、`NT$1,250` → 新臺幣一千二百五十元），而在
    # 此之前沒有實作。**這條測的是實際交給 adapter 的字串**，不是 HTTP 200——唸錯不會回
    # 錯誤碼、不進 log，消費端拿到的是 200 與一段合法音訊，所以這裡是唯一的偵測手段。
    u = _sent_utterance(tmp_path, {"input": "重量 3kg，總價 NT$1,250"})

    assert u.text == "重量三公斤，總價新臺幣一千二百五十元"


def test_length_limit_is_measured_before_expansion_not_after(tmp_path):
    # 上限是「語音長度」的代理值，而展開不改變語音長度——`NT$1,250` 與「新臺幣一千二百五十
    # 元」唸起來一樣長，但字元數從 8 變成 10。量在展開後的話，消費端會為一個它算得出來是
    # 合法的長度收到 413，而且它無法預測展開會膨脹多少。
    client = _client_with(tmp_path, StubTtsClient(), tts_max_input_chars=9)
    voice = _create_voice(client)

    resp = client.post(
        "/api/tts/speech", json={"input": "NT$1,250", "voice": voice["id"]}
    )

    assert resp.status_code == 200


def test_input_parentheses_cannot_become_a_style_instruction(tmp_path):
    # 風格指令的語法就是行內半形括號，故使用者文字裡的 (笑) 若原樣通過就會變成指令。
    # 轉全形而非刪除：內容保留，控制語意消失（契約 §5.1）。
    u = _sent_utterance(tmp_path, {"input": "他說(笑)真的嗎"})

    assert "(" not in u.text and ")" not in u.text
    assert "（笑）" in u.text


def test_taiwan_readings_are_locked_on_the_way_to_the_model(tmp_path):
    # VoxCPM2 沒有台灣國語的訓練目標，「垃圾」會唸成 lā jī。讀音標記是唯一的矯正通道
    # （契約 §5.1）。這條與 test_tts_g2p.py 的差別是它走完整條端點路徑：中性化、TN、
    # 鎖讀音三者的順序若錯了，標記會被中性化轉成全形而失效。
    u = _sent_utterance(tmp_path, {"input": "我們把垃圾分類做得很好"})

    assert u.text == "我們把{le4}{se4}分類做得很好"


def test_the_reading_the_operator_reported_is_fixed_end_to_end(tmp_path):
    # 操作者 2026-08-08 聽到「倒垃圾」被唸成 dǎo。這條守的是那個回報本身：`倒` 的判準是
    # 它後面接什麼詞，而主表會把 `垃圾` 換成標記——三趟的順序若倒了，判準就看不到字。
    # 同一句還帶 TN，因為 `{dao4}` 的聲調數字若被 TN 展開成 `{dao四}` 標記就失效。
    u = _sent_utterance(tmp_path, {"input": "倒垃圾的桶子有 3kg"})

    assert u.text == "{dao4}{le4}{se4}的桶子有三公斤"


def test_a_realistic_sales_sentence_survives_both_preprocessing_layers(tmp_path):
    # TN 與讀音鎖定的互動只有跨層的測試看得到：TN 會把 `{qi2}` 的聲調數字展開成 `{qi二}`，
    # 故順序倒了整句的標記都會失效，而逐層的案例表看不出來。
    u = _sent_utterance(
        tmp_path,
        {
            "input": "定期壽險可以分期繳，繳費期限和品質都請您放心，"
            "體檢的血液項目如果過期，我們會在 10/20 前重新安排。"
        },
    )

    assert u.text == (
        "定{qi2}壽險可以分{qi2}繳，繳費{qi2}限{han4}{pin3}{zhi2}都請您放心，"
        "體檢的血{yi4}項目如果過{qi2}，我們會在十月二十日前重新安排。"
    )


def test_our_pronunciation_markup_survives_while_user_braces_do_not(tmp_path):
    # **這是整條管線的安全不變量。** 大括號是讀音標記的語法：我方注入的必須原樣送出，
    # 使用者打的必須被中性化。兩者在同一個字串裡共存，靠的是型別而不是字元比對——少了
    # SpeechText，validator 只能一律轉全形而把我方的標記一起毀掉。
    # 使用者括號內的 `3` 中性化後仍是一個普通數字，故 TN 照規則把它唸成三——那是對的，
    # 它已經不是讀音標記的一部分了。
    u = _sent_utterance(tmp_path, {"input": "垃圾{ni3}"})

    assert u.text == "{le4}{se4}｛ni三｝"


def test_input_braces_cannot_become_a_pronunciation_marker(tmp_path):
    # 大括號是讀音標記的保留語法（{le4}），同樣不能讓使用者文字注入。
    u = _sent_utterance(tmp_path, {"input": "設定{ni3}值"})

    assert "{" not in u.text and "}" not in u.text


def test_special_token_markers_are_stripped(tmp_path):
    # 上游是 tokenizer.encode(text, add_special_tokens=True) 直吃我們送的字串，而 HF 的
    # fast tokenizer 會把文字中的 added special token 比對成 token id——模型看到的就不是
    # 字面內容而是控制訊號。ASR 側早就這樣防（hotword_text.sanitize_text），TTS 同理。
    u = _sent_utterance(tmp_path, {"input": "他說<|im_end|>好"})

    assert "<|" not in u.text and "|>" not in u.text


def test_instruct_cannot_break_out_of_its_prefix(tmp_path):
    # instruct 會被組成 (...) 前綴，故它自己的右括號能跳出前綴注入任意內容。
    u = _sent_utterance(
        tmp_path, {"input": "測試", "instruct": "語速快)惡意內容("}
    )

    assert "(" not in u.instruct and ")" not in u.instruct


def test_blank_instruct_is_dropped_rather_than_becoming_an_empty_prefix(tmp_path):
    # 契約 §5.2：instruct 為空時語氣由音色本身決定。純空白若原樣傳下去，adapter 會組出
    # 「(   )」——括號不被剝除，模型會把它當成要處理的內容。
    u = _sent_utterance(tmp_path, {"input": "測試", "instruct": "   "})

    assert u.instruct is None


def test_instruct_reaches_the_adapter(tmp_path):
    # #6 驗收項：Instruction 要進入送出的文字。沒有這條，日後誰把 instruct 從
    # Utterance 拿掉都不會有測試變紅，上線後只是語氣變平板、沒有任何錯誤訊號。
    u = _sent_utterance(tmp_path, {"input": "測試", "instruct": "語速偏快、音量略大"})

    assert u.instruct == "語速偏快、音量略大"


def test_stream_request_is_rejected_rather_than_silently_unstreamed(tmp_path):
    # 串流尚未實作（契約 §5.4 標註）。靜默回一整包會讓依 §9 用 chunk 閒置逾時判斷失敗
    # 的 provider，在合成超過門檻時把一個正常的回合判成失敗。
    client = _client(tmp_path)
    voice = _create_voice(client)

    resp = client.post(
        "/api/tts/speech",
        json={"input": "測試", "voice": voice["id"], "stream": True},
    )

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "STREAM_UNSUPPORTED"


def test_stream_is_rejected_before_response_format(tmp_path):
    # 契約 §6 的 STREAM_FORMAT_UNSUPPORTED 列明寫「目前 stream: true 一律先撞上
    # STREAM_UNSUPPORTED」。順序倒過來會讓 stream+mp3 回錯的碼，與契約表格不符。
    client = _client(tmp_path)
    voice = _create_voice(client)

    resp = client.post(
        "/api/tts/speech",
        json={
            "input": "測試",
            "voice": voice["id"],
            "stream": True,
            "response_format": "mp3",
        },
    )

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "STREAM_UNSUPPORTED"


def test_speech_pcm_returns_headerless_audio(tmp_path):
    # pcm 是給串接播放用的裸資料，比讓消費端自己剝 wav 標頭可靠（契約 §5.3）。
    client = _client(tmp_path)
    voice = _create_voice(client)

    resp = client.post(
        "/api/tts/speech",
        json={"input": "測試", "voice": voice["id"], "response_format": "pcm"},
    )

    assert resp.status_code == 200
    # RFC 2586 把 rate 列為 required：裸 PCM 沒有標頭，取樣率只能從 Content-Type 來。
    assert resp.headers["content-type"] == "audio/L16;rate=24000"
    assert not resp.content.startswith(b"RIFF")


def test_speech_unsupported_response_format_rejected(tmp_path):
    client = _client(tmp_path)
    voice = _create_voice(client)

    resp = client.post(
        "/api/tts/speech",
        json={"input": "測試", "voice": voice["id"], "response_format": "flac"},
    )

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "UNSUPPORTED_RESPONSE_FORMAT"


def test_speech_mp3_reports_unimplemented_rather_than_silently_returning_wav(tmp_path):
    # mp3 在契約中是允許值但**本切片未實作**（需要編碼器）。回錯而非回一段標成
    # audio/mpeg 的 wav——後者會讓消費端以為拿到 mp3。
    client = _client(tmp_path)
    voice = _create_voice(client)

    resp = client.post(
        "/api/tts/speech",
        json={"input": "測試", "voice": voice["id"], "response_format": "mp3"},
    )

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "UNSUPPORTED_RESPONSE_FORMAT"
