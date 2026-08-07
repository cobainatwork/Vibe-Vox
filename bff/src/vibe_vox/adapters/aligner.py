"""字級強制對齊串接：HttpAlignerClient 呼叫 aligner 服務（ADR-0004）。

契約見 aligner/README.md。要點：

- multipart `items`（JSON 陣列，每筆 `{"text": ...}`）＋可重複的 `audio` 檔，兩者
  順序一一對應、數量須相同。
- 一次請求即一個 batch，服務端對單次段數有上限（VRAM 保護）。超過即整批回 400，
  故本 client 依 `max_batch_items` 分批送出，見 DEFAULT_MAX_BATCH_ITEMS。
- 對齊結果的時間基準是**該段切片自身的 0**，本 client 負責加回 offset 還原為
  原音檔的絕對時間。
- 服務端對整個 batch 是全有全無：任一筆不合契約即整批回錯。故退化段落在送出前
  就剔除，否則單段異常會使整檔的字級時間戳一併失效。
- 對齊不可得（服務掛掉、逾時、某批失敗、段落根本沒送出）不拋出，逐段以 omission
  說明原因，見 `AlignerClient.align`。本模組因此不記 log：原因隨結果過 seam，由
  `merge_alignment` 連同段號一起記，同因的段落合記一條。

遠端連線屬環境相依，測試以 httpx MockTransport 注入假回應。
"""

import asyncio
import json
import re
from dataclasses import dataclass
from itertools import batched
from pathlib import Path
from typing import Any

import httpx

from vibe_vox.adapters.base import Omission, Segment, SegmentAlignment, Word
from vibe_vox.audio.slice import Slice, slice_wav


class AlignerUnavailable(Exception):
    """對齊服務連不上、回非 2xx 或回傳信封異常。

    **本模組內部用，不跨 seam**：`align` 會把它轉成該批段落的 omission 而不往外拋，
    降級因此是 interface 的保證而非呼叫端的紀律（見 `AlignerClient.align`）。
    """


class AlignerTimeout(Exception):
    """對齊服務呼叫逾時。用途同 AlignerUnavailable，不跨 seam。"""


# 切片左右各留的 buffer（秒）。VibeVoice 的段界是模型自選切點而非發音邊界
# （ADR-0004、docs/api/asr.md §4.3），可能落在某個字的發音中間；buffer 使邊界字
# 的音訊完整落在切片內，否則該字被切成兩半、兩段都對不準。
#
# 0.5 秒是**由單字時長推導的下界，不是切點漂移的實測值**：#26 實測單字時長為
# 0.16–0.40 秒（ADR-0004 Consequences），buffer 至少須覆蓋其上界才能保證邊界字
# 完整。漂移量本身**仍未量測**——那需要真實錄音跑完整鏈路、比對切點與發音邊界，
# 追蹤於 #32。放大的代價是納入更多鄰段語音：強制對齊會把無對應文字的音訊分配給
# 首尾字，使邊界時間戳外擴，進而影響段間間隙（即評分端要用的句間停頓），故不宜
# 在有實測前先行加大。
DEFAULT_SLICE_BUFFER_SECONDS = 0.5

# 單次請求的段數上限，須與服務端的 `VIBE_VOX_ALIGNER_MAX_BATCH_ITEMS` 一致或更小。
#
# **這是跨元件的耦合**：服務端的上限是 VRAM 保護（該卡與 vllm 共用，聚合量無上限時
# CUDA OOM 會波及它們），超過即整批回 400 `BATCH_TOO_LARGE`。呼叫端必須先於送出就
# 遵守它，不能靠撞牆後重試。兩處的預設值由 `test_config.py` 比對，不靠這條註解
# （#35 的教訓：跨元件的邊界值若只靠註解同步，某天其中一邊改了就悄悄失效）。
#
# **8 是實測值，取代原本校準出的 32。** 32 在真機上 CUDA OOM，63 段的會議錄音兩批
# 都失敗（#36 的實跑驗收）。根因是**計數對記憶體的主導變數是盲的**：批次張量會 pad
# 到該批最長的那一段，而 ADR-0004 的校準用的是同一段 34 秒音訊重複 32 次，padding
# 浪費恰好 1.00 倍。真實錄音的段長是 1.77 至 41.29 秒，cap 32 之下 621 秒的實際音訊
# 被 pad 成 1206 秒，接近翻倍；cap 8 則只有 330 秒。
#
# 兩個推論仍未證實，故取值偏保守：校準記錄的 5750 MiB 是累積量測（見
# aligner/scripts/bench_vram.sh），與單批獨立峰值的關係不明；而線性外推只能解釋
# 實測 6799 MiB 中的約 6100 MiB，剩下約 700 MiB 無法歸因。
#
# **8 是這台測試機（單張 48 GB，三個模型共用）的值，不是通用的設計值。** 資料平面是回合制
# 對話的 2–4 段，觸不到任何上限；會撞到的只有管理平面的長音檔測試，而那條路多幾次往返的
# 代價是幾秒。所以在這台機器上調高沒有收益，只是把 aligner 往 vLLM 留下的天花板推。
#
# 規格不同的部署可以調高，但**必須重測**，且不能沿用 ADR-0004 那組數字（它們是在無 padding
# 浪費的合成負載下量的，見上）。
DEFAULT_MAX_BATCH_ITEMS = 8


# 非錯誤信封時保留的主體字元數。夠長到能認出反向代理的錯誤頁或服務被替換，又不會
# 把整份 HTML 灌進 log。
_ERROR_BODY_PREFIX_CHARS = 200


# VibeVoice 對非語音區段輸出的標記，整段只有一個方括號記號：`[Silence]`、`[Music]`、
# `[Unintelligible Speech]`。
#
# **不以 `Speaker` 為空作判準**：實測資料中兩者一致（標記段的 Speaker 皆為空），但空
# 語者是伴隨現象，而方括號模式直接表達「這不是語音內容」。
_NON_SPEECH_MARKER = re.compile(r"^\s*\[[^\]]*\]\s*$")


@dataclass(frozen=True)
class _Sendable:
    """一個要送出對齊的段落。

    `index` 是它在原 segments 中的位置，用來把結果放回原位：退化段落與非語音標記段不
    送出，故送出序列與原序列的索引不一致，靠平行清單對應極易錯位（#27 的 offset 錯位
    正是這類問題）。
    """

    index: int
    segment: Segment
    sliced: Slice


def _why_not_sent(segment: Segment, sliced: Slice) -> Omission | None:
    """該段不送出對齊的理由；可送出則回 `None`。

    回理由而非布林：三種情形在下游一律表現為「這段沒有字」，看不出是哪一種，而它們
    的意義完全不同——非語音標記段是正常結果，切片為零長度則是模型的時間戳幻覺。

    服務端會拒絕空文字（400 `INVALID_ITEMS`）、零長度音訊則使推論失敗（500
    `ALIGN_FAILED`），而兩者都是整批回錯，會連帶毀掉同批正常段落的時間戳。
    空 Content 並非假想情境：模型輸出缺欄位時即補空字串（docs/api/asr.md §6）。

    非語音標記段則是另一類問題：它送得出去、也會回結果，但那個結果是**假的**。
    `qwen-asr` 的 `clean_token` 只留 Unicode 字母數字，方括號被剝除後 `Silence` 成為
    一個 Word，模型會把它對到該段靜音上。第一段若是 `[Silence]`，`speech_start` 會
    變成約 0，等於宣稱沒有開頭沉默，而 ADR-0004 明文要求保留它（#38）。
    """
    if not segment.Content.strip():
        return Omission("empty_content", "段落文字為空，未送出對齊")
    if _NON_SPEECH_MARKER.match(segment.Content):
        return Omission(
            "non_speech_marker",
            f"非語音標記段（{segment.Content.strip()}），未送出對齊",
        )
    if sliced.frames <= 0:
        return Omission(
            "empty_slice", "切片為零長度（段落時間戳落在音檔外），未送出對齊"
        )
    return None


def _align_request(
    segments: list[Segment], slices: list[Slice]
) -> tuple[dict, list[tuple]]:
    """組 /align 的表單欄位與檔案清單，回傳 (data, files) 兩者供 httpx 分別帶入。

    檔名只為滿足 multipart 格式，服務端不看它——配對純靠順序。
    """
    data = {
        "items": json.dumps(
            [{"text": s.Content} for s in segments], ensure_ascii=False
        )
    }
    files = [
        ("audio", (f"{index}.wav", sliced.wav, "audio/wav"))
        for index, sliced in enumerate(slices)
    ]
    return data, files


def _to_absolute(words: list[dict], offset: float) -> list[Word]:
    """把切片內的相對時間戳加回 offset，還原為原音檔的絕對時間。

    取三位小數與 qwen-asr 的輸出精度一致（模型輸出毫秒、套件已取三位），
    避免相加的浮點尾數外洩至消費端契約。
    """
    return [
        Word(
            Text=w["text"],
            Start=round(w["start"] + offset, 3),
            End=round(w["end"] + offset, 3),
        )
        for w in words
    ]


def _describe_error_response(resp: httpx.Response) -> str:
    """把服務端的錯誤回應濃縮成一行，供 log 直接說出原因而非讓人反推（#37）。

    aligner 的錯誤信封是 `{"error": {"code", "message"}}`，README 定義九個碼。非該
    形狀時（反向代理的 HTML 錯誤頁、服務被替換成別的東西）退回主體前綴，仍留線索。
    """
    try:
        error = resp.json()["error"]
        return f"HTTP {resp.status_code} {error['code']}：{error['message']}"
    except (json.JSONDecodeError, KeyError, TypeError):
        return f"HTTP {resp.status_code}：{resp.text[:_ERROR_BODY_PREFIX_CHARS]}"


def _parse(data: Any, slices: list[Slice]) -> list[list[Word]]:
    """解析回應並還原為絕對時間；信封不合契約即視為上游不可用。"""
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise AlignerUnavailable(f"回應缺 items 陣列（得到 {type(items).__name__}）")
    if len(items) != len(slices):
        # 筆數不符時 zip 會靜默截短，使該段之後的 offset 全數錯位且無聲無息。
        raise AlignerUnavailable(
            f"回應筆數不符：送出 {len(slices)} 筆，回 {len(items)} 筆"
        )
    try:
        return [
            _to_absolute(item["words"], sliced.start)
            for item, sliced in zip(items, slices)
        ]
    except (KeyError, TypeError) as exc:
        raise AlignerUnavailable(f"回應筆內欄位不合契約：{exc!r}") from exc


def _partition_sendable(
    segments: list[Segment], slices: list[Slice]
) -> tuple[list[_Sendable], dict[int, Omission]]:
    """分成送得出去的段落與送不出去的（後者附理由，見 `_why_not_sent`）。"""
    sendable: list[_Sendable] = []
    omissions: dict[int, Omission] = {}
    for index, (segment, sliced) in enumerate(zip(segments, slices)):
        if (why := _why_not_sent(segment, sliced)) is not None:
            omissions[index] = why
        else:
            sendable.append(_Sendable(index, segment, sliced))
    return sendable, omissions


def _batch_failed(order: int, total: int, exc: Exception, *, budget: float) -> Omission:
    """一批送出後失敗的原因。

    批號讓同批的段落在下游被認出是同一件事而合記一條。逾時另外描述：`asyncio.timeout`
    拋的 `TimeoutError` 本身沒有訊息，而預算是所有批次共用的，故要說出是在哪一批耗盡。
    """
    cause = (
        f"逾時（對齊的 {budget} 秒預算內未回應）"
        if isinstance(exc, TimeoutError)  # AlignerTimeout 不繼承它，兩者不會混淆
        else str(exc)
    )
    return Omission("batch_failed", f"第 {order}／{total} 批對齊失敗：{cause}")


def _budget_spent(order: int, total: int, *, budget: float) -> Omission:
    """一批因預算用盡而未送出的原因。"""
    return Omission(
        "budget_spent", f"第 {order}／{total} 批未送出：對齊的 {budget} 秒預算已用盡"
    )


def _to_alignments(
    aligned: dict[int, list[Word]],
    slices: list[Slice],
    omissions: dict[int, Omission],
) -> list[SegmentAlignment]:
    """把逐批取得的字級結果攤回原段序。

    未送出與失敗批次的段落得到空的 words 與一個原因，但仍帶自己的切片範圍：索引因此
    始終與 segments 對齊，而落界判準對每一段都有可比對的範圍。
    """
    return [
        SegmentAlignment(
            words=aligned.get(index, []),
            bounds=sliced.bounds,
            omission=omissions.get(index),
        )
        for index, sliced in enumerate(slices)
    ]


class HttpAlignerClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 60.0,
        slice_buffer_seconds: float = DEFAULT_SLICE_BUFFER_SECONDS,
        max_batch_items: int = DEFAULT_MAX_BATCH_ITEMS,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url
        self._timeout = timeout
        self._buffer = slice_buffer_seconds
        self._max_batch_items = max_batch_items
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url, timeout=self._timeout, transport=self._transport
        )

    async def health(self) -> bool:
        try:
            async with self._client() as client:
                resp = await client.get("/health")
                return resp.status_code == 200
        except httpx.HTTPError:
            return False

    async def align(
        self, audio: Path, segments: list[Segment]
    ) -> list[SegmentAlignment]:
        slices = self._slice_each(audio, segments)
        sendable, omissions = _partition_sendable(segments, slices)
        if not sendable:
            # 也涵蓋 segments 為空（音訊有效但完全無語音，docs/api/asr.md §6）：
            # aligner 的 audio 為必填欄位，送零個檔只會換來 400。
            return _to_alignments({}, slices, omissions)

        batches = list(batched(sendable, self._max_batch_items))
        aligned, failed = await self._align_batches(batches)
        # 全批失敗也不拋出：降級是本層的保證（見 AlignerClient.align）。批次原因逐段
        # 帶出去，故跨批不同因時每個原因都留著——服務在兩次請求之間被重啟的話，可能
        # 一批逾時、另一批回 503，只往上傳一個會讓其他原因完全消失。
        return _to_alignments(aligned, slices, omissions | failed)

    def _slice_each(self, audio: Path, segments: list[Segment]) -> list[Slice]:
        """逐段切片。**每段都切**，包含送不出去的段落（理由見 `_why_not_sent`）：
        它們的字級結果雖為空，切片範圍仍是回傳結果的一部分。"""
        return [
            slice_wav(
                audio,
                start=segment.Start - self._buffer,
                end=segment.End + self._buffer,
            )
            for segment in segments
        ]

    async def _align_batches(
        self, batches: list[tuple[_Sendable, ...]]
    ) -> tuple[dict[int, list[Word]], dict[int, Omission]]:
        """逐批送出，回傳（段索引 → 字級結果）與（段索引 → 該批沒有結果的原因）。

        一批失敗只記下原因不中斷：批次級故障隔離，不丟棄其他批已取得的時間戳（#36）。

        **逾時是所有批次共用的預算，不是每批各有一份。** 分批之後「批數 × 每批逾時」會
        超過端點 guard 分給對齊的那一份（`config.heavy_request_budget` 只加一次），使
        guard 先於內層觸發並回 504，逐字稿一併喪失——ADR-0004 的第二層降級要避免的正是
        這件事。預算用盡後剩餘批次直接不送，已取得的結果留著。

        預算以 `asyncio.timeout` 施加而非只把剩餘秒數交給 httpx：httpx 的 timeout 是
        connect／read／write／pool 各自的上限，而 read 量的是「兩次讀取之間」而非整批
        耗時，慢速滴流的回應能一路超出預算。整體上限只有在這一層才真正成立。
        """
        aligned: dict[int, list[Word]] = {}
        omissions: dict[int, Omission] = {}
        total = len(batches)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._timeout
        async with self._client() as client:
            for order, batch in enumerate(batches, start=1):
                remaining = deadline - loop.time()
                if remaining <= 0:
                    why = _budget_spent(order, total, budget=self._timeout)
                    omissions.update((item.index, why) for item in batch)
                    continue
                slices = [item.sliced for item in batch]
                try:
                    async with asyncio.timeout(remaining):
                        payload = await self._post_align(
                            client, [item.segment for item in batch], slices
                        )
                    parsed = _parse(payload, slices)
                except (AlignerUnavailable, AlignerTimeout, TimeoutError) as exc:
                    why = _batch_failed(order, total, exc, budget=self._timeout)
                    omissions.update((item.index, why) for item in batch)
                    continue
                aligned.update(
                    (item.index, words) for item, words in zip(batch, parsed)
                )
        return aligned, omissions

    async def _post_align(
        self, client: httpx.AsyncClient, segments: list[Segment], slices: list[Slice]
    ) -> Any:
        """送一批並回傳其 JSON 主體；連線、狀態碼與主體格式的失敗一律轉為本模組的
        兩種例外，由呼叫端轉成該批段落的 omission。

        整批的時間上限由呼叫端以 `asyncio.timeout` 施加（見 `_align_batches`）。client
        自身的逾時仍在，但只是同一份預算的下界，不是每批各有一份。
        """
        data, files = _align_request(segments, slices)
        try:
            resp = await client.post("/align", data=data, files=files)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise AlignerUnavailable(_describe_error_response(exc.response)) from exc
        except httpx.TimeoutException as exc:
            raise AlignerTimeout(
                f"逾時（對齊的 {self._timeout} 秒預算內未回應）"
            ) from exc
        except httpx.HTTPError as exc:
            # 連線層失敗（拒絕連線、DNS、TLS）沒有回應體可讀，型別本身即線索。
            raise AlignerUnavailable(f"{type(exc).__name__}：{exc}") from exc
        try:
            return resp.json()
        except json.JSONDecodeError as exc:
            # 200 但主體非 JSON（proxy 介入、服務被替換成別的東西）。不攔會冒成 500
            # 使逐字稿一併失效，違反 ADR-0004 的第二層降級。
            raise AlignerUnavailable(
                f"HTTP {resp.status_code} 但主體非 JSON："
                f"{resp.text[:_ERROR_BODY_PREFIX_CHARS]}"
            ) from exc
