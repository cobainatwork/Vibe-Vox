"""字級強制對齊串接：HttpAlignerClient 呼叫 aligner 服務（ADR-0004）。

契約見 aligner/README.md。要點：

- multipart `items`（JSON 陣列，每筆 `{"text": ...}`）＋可重複的 `audio` 檔，兩者
  順序一一對應、數量須相同。
- 一次請求即一個 batch。日常負載 2–4 段（單輪對話 1–2 分鐘經 VibeVoice 切分），
  遠低於服務端 32 段的異常防護上限，故不分批。
- 對齊結果的時間基準是**該段切片自身的 0**，本 client 負責加回 offset 還原為
  原音檔的絕對時間。
- 服務端對整個 batch 是全有全無：任一筆不合契約即整批回錯。故退化段落在送出前
  就剔除，否則單段異常會使整檔的字級時間戳一併失效。

遠端連線屬環境相依，測試以 httpx MockTransport 注入假回應。
"""

import json
from pathlib import Path
from typing import Any

import httpx

from vibe_vox.adapters.base import Segment, Word
from vibe_vox.audio.slice import Slice, slice_wav


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


def _is_alignable(segment: Segment, sliced: Slice) -> bool:
    """該段是否可送出對齊。

    服務端會拒絕空文字（400 `INVALID_ITEMS`）、零長度音訊則使推論失敗（500
    `ALIGN_FAILED`），而兩者都是整批回錯，會連帶毀掉同批正常段落的時間戳。
    空 Content 並非假想情境：模型輸出缺欄位時即補空字串（docs/api/asr.md §6）。
    """
    return bool(segment.Content.strip()) and sliced.frames > 0


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


def _parse(data: Any, slices: list[Slice]) -> list[list[Word]]:
    """解析回應並還原為絕對時間；信封不合契約即視為上游不可用。"""
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list) or len(items) != len(slices):
        # 筆數不符時 zip 會靜默截短，使該段之後的 offset 全數錯位且無聲無息。
        raise AlignerUnavailable
    try:
        return [
            _to_absolute(item["words"], sliced.start)
            for item, sliced in zip(items, slices)
        ]
    except (KeyError, TypeError) as exc:
        raise AlignerUnavailable from exc


class HttpAlignerClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 60.0,
        slice_buffer_seconds: float = DEFAULT_SLICE_BUFFER_SECONDS,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url
        self._timeout = timeout
        self._buffer = slice_buffer_seconds
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
        slices = [
            slice_wav(audio, start=s.Start - self._buffer, end=s.End + self._buffer)
            for s in segments
        ]
        sendable = [
            index
            for index, (segment, sliced) in enumerate(zip(segments, slices))
            if _is_alignable(segment, sliced)
        ]
        if not sendable:
            # 也涵蓋 segments 為空（音訊有效但完全無語音，docs/api/asr.md §6）：
            # aligner 的 audio 為必填欄位，送零個檔只會換來 400。
            return [[] for _ in segments]

        sent = [slices[index] for index in sendable]
        data, files = _align_request([segments[index] for index in sendable], sent)
        try:
            async with self._client() as client:
                resp = await client.post("/align", data=data, files=files)
                resp.raise_for_status()
                payload = resp.json()
        except httpx.TimeoutException as exc:
            raise AlignerTimeout from exc
        except httpx.HTTPError as exc:
            raise AlignerUnavailable from exc
        except json.JSONDecodeError as exc:
            # 200 但主體非 JSON（proxy 介入、服務被替換成別的東西）。不攔會冒成
            # 500 使逐字稿一併失效，違反 ADR-0004 的第二層降級。
            raise AlignerUnavailable from exc

        # 未送出的段落留空位，使結果的索引仍與 segments 對齊。
        words: list[list[Word]] = [[] for _ in segments]
        for index, aligned in zip(sendable, _parse(payload, sent)):
            words[index] = aligned
        return words
