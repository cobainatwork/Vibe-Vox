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

遠端連線屬環境相依，測試以 httpx MockTransport 注入假回應。
"""

import json
import logging
import re
from dataclasses import dataclass
from itertools import batched
from pathlib import Path
from typing import Any

import httpx

from vibe_vox.adapters.base import Segment, Word
from vibe_vox.audio.slice import Slice, slice_wav

logger = logging.getLogger(__name__)


class AlignerUnavailable(Exception):
    """對齊服務連不上、回非 2xx 或回傳信封異常。

    **端點層攔下並降級，不映射成狀態碼**：對齊是附加功能，逐字稿有獨立價值，不因
    它失效而一併不可得（ADR-0004 的第二層降級）。回應仍為 200，全段標記未對齊。
    """


class AlignerTimeout(Exception):
    """對齊服務呼叫逾時。降級方式同 AlignerUnavailable，不映射成狀態碼。"""


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


@dataclass(frozen=True)
class _BatchFailure:
    """一批送出失敗的紀錄。order 自 1 起算，僅用於 log 讓人對上是第幾批。"""

    order: int
    segment_count: int
    error: Exception


def _is_alignable(segment: Segment, sliced: Slice) -> bool:
    """該段是否可送出對齊。

    服務端會拒絕空文字（400 `INVALID_ITEMS`）、零長度音訊則使推論失敗（500
    `ALIGN_FAILED`），而兩者都是整批回錯，會連帶毀掉同批正常段落的時間戳。
    空 Content 並非假想情境：模型輸出缺欄位時即補空字串（docs/api/asr.md §6）。

    非語音標記段則是另一類問題：它送得出去、也會回結果，但那個結果是**假的**。
    `qwen-asr` 的 `clean_token` 只留 Unicode 字母數字，方括號被剝除後 `Silence` 成為
    一個 Word，模型會把它對到該段靜音上。第一段若是 `[Silence]`，`speech_start` 會
    變成約 0，等於宣稱沒有開頭沉默，而 ADR-0004 明文要求保留它（#38）。
    """
    return (
        bool(segment.Content.strip())
        and not _NON_SPEECH_MARKER.match(segment.Content)
        and sliced.frames > 0
    )


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


def _log_dropped_batches(failures: list[_BatchFailure], *, total: int) -> None:
    """記下被丟棄的批次。

    部分失敗不 raise，故端點層不會記任何東西；不在此記的話，那些段落會悄悄變成未對齊，
    而 `merge_alignment` 只會說「字級清單為空」，看不出有一批整批失敗（#37）。

    全批失敗時呼叫端只傳入第一批以外的失敗：往上傳的那個由端點層記錄，避免同一件事
    出現兩條訊息，而其餘批次的原因仍須留下（跨批不保證同因）。
    """
    for failure in failures:
        logger.warning(
            "第 %d／%d 批（%d 段）對齊失敗，該批段落降級為未對齊：%s",
            failure.order,
            total,
            failure.segment_count,
            failure.error,
        )


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

    async def align(self, audio: Path, segments: list[Segment]) -> list[list[Word]]:
        sendable = self._prepare_sendable(audio, segments)
        if not sendable:
            # 也涵蓋 segments 為空（音訊有效但完全無語音，docs/api/asr.md §6）：
            # aligner 的 audio 為必填欄位，送零個檔只會換來 400。
            return [[] for _ in segments]

        batches = list(batched(sendable, self._max_batch_items))
        aligned, failures = await self._align_batches(batches)
        if len(failures) == len(batches):
            # 全批失敗即對齊完全不可得，須讓端點層知道以記錄服務層級的原因並降級。
            # 靜默回全空會使 log 只剩逐段的「字級清單為空」，診斷只能靠反推（#37）。
            #
            # 只有一個例外能往上傳，故其餘批次的原因在此記下：跨批不保證同因（服務在
            # 兩次請求之間被重啟時，可能一批逾時、另一批回 503），只 raise 第一個會讓
            # 其他原因完全消失。
            _log_dropped_batches(failures[1:], total=len(batches))
            raise failures[0].error
        _log_dropped_batches(failures, total=len(batches))
        # 未送出與失敗批次的段落留空位，使結果的索引仍與 segments 對齊。
        return [aligned.get(index, []) for index in range(len(segments))]

    def _prepare_sendable(
        self, audio: Path, segments: list[Segment]
    ) -> list[_Sendable]:
        """逐段切片並剔除送不出去的段落，理由見 `_is_alignable`。"""
        sendable: list[_Sendable] = []
        for index, segment in enumerate(segments):
            sliced = slice_wav(
                audio,
                start=segment.Start - self._buffer,
                end=segment.End + self._buffer,
            )
            if _is_alignable(segment, sliced):
                sendable.append(_Sendable(index, segment, sliced))
        return sendable

    async def _align_batches(
        self, batches: list[tuple[_Sendable, ...]]
    ) -> tuple[dict[int, list[Word]], list[_BatchFailure]]:
        """逐批送出，回傳（段索引 → 字級結果）與失敗的批次。

        一批失敗只記錄不中斷：批次級故障隔離，不丟棄其他批已取得的時間戳（#36）。
        """
        aligned: dict[int, list[Word]] = {}
        failures: list[_BatchFailure] = []
        async with self._client() as client:
            for order, batch in enumerate(batches, start=1):
                slices = [item.sliced for item in batch]
                try:
                    payload = await self._post_align(
                        client, [item.segment for item in batch], slices
                    )
                    parsed = _parse(payload, slices)
                except (AlignerTimeout, AlignerUnavailable) as exc:
                    failures.append(_BatchFailure(order, len(batch), exc))
                    continue
                aligned.update(
                    (item.index, words) for item, words in zip(batch, parsed)
                )
        return aligned, failures

    async def _post_align(
        self, client: httpx.AsyncClient, segments: list[Segment], slices: list[Slice]
    ) -> Any:
        """送一批並回傳其 JSON 主體；連線、狀態碼與主體格式的失敗一律轉為本模組的
        例外，使端點層只需認識這兩種（ADR-0004 的第二層降級）。"""
        data, files = _align_request(segments, slices)
        try:
            resp = await client.post("/align", data=data, files=files)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise AlignerUnavailable(_describe_error_response(exc.response)) from exc
        except httpx.TimeoutException as exc:
            raise AlignerTimeout(f"逾時 {self._timeout} 秒未回應") from exc
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
