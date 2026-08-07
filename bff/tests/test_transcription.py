"""一次辨識：seam 為 Transcriber 的公開介面，直接呼叫。

Transcriber 擁有 intake → 辨識 → 對齊 → 合理性檢查這條鏈路的順序與時序約束。音檔
輸入與兩個模型端以替身注入，故測試離線、不需 ffmpeg 或 GPU。

端點層的 HTTP 關注點（multipart、併發護欄、錯誤信封）不在此，見 test_asr.py。
"""

import asyncio

from fakes import FakeIntake

from vibe_vox.adapters.base import (
    Omission,
    Segment,
    SegmentAlignment,
    AsrResult,
    Word,
)
from vibe_vox.adapters.stub import StubAlignerClient, StubAsrClient
from vibe_vox.transcription import Transcriber

_RATE = 24000


async def _chunks(*parts: bytes):
    for part in parts:
        yield part


def _result(segments: list[Segment], text: str = "你好") -> AsrResult:
    return AsrResult(
        segments=segments,
        raw_text=text,
        transcription_only=text,
    )


def _transcriber(tmp_path, result, *, aligner=None, seconds: float = 2.0):
    return Transcriber(
        intake=FakeIntake(tmp_path, seconds=seconds),
        asr=StubAsrClient(result=result),
        aligner=aligner or StubAlignerClient(),
        sample_rate=_RATE,
    )


def test_audio_duration_comes_from_the_file_not_from_the_segments(tmp_path):
    # audio_duration 是音檔的實際長度，而 Segment 的 End 最大值不含尾端靜音
    # （docs/api/asr.md §4.2）——結尾沉默時長正是由兩者的差算出。
    #
    # 它只能在 intake 的 context 內取（離開時 wav 就被刪了），而彙總發生在 context 外。
    # 這條順序約束是本 module 存在的主要理由：以前它只存在於端點函式的縮排裡，沒有任何
    # 型別或介面表達它，改動時很容易把取值搬到 context 外而在執行期才炸。
    result = _result([Segment(Start=0.0, End=1.0, Speaker="0", Content="你好")])

    transcription = asyncio.run(
        _transcriber(tmp_path, result, seconds=5.0).transcribe(
            _chunks(b"raw"), context=""
        )
    )

    assert transcription.alignment.audio_duration == 5.0  # 尾端 4 秒靜音仍計入


def test_duration_follows_the_segment_bounds_recomputed_from_words(tmp_path):
    # duration 是所有 Segment 的 End 最大值，而段界對齊後重算為末字的 End，故其值隨
    # 對齊改變。這條定義以前有兩份：AsrResult.duration（生產出來但無人消費）
    # 與端點層的重算，而權威版本是後者。現在只有本 module 擁有它。
    result = _result([Segment(Start=0.0, End=1.2, Speaker="0", Content="你好")])
    aligned = StubAlignerClient(
        result=[
            SegmentAlignment(
                words=[
                    Word(Text="你", Start=0.42, End=0.58),
                    Word(Text="好", Start=0.58, End=0.90),
                ],
                bounds=(0.0, 1.7),
            )
        ]
    )

    transcription = asyncio.run(
        _transcriber(tmp_path, result, aligner=aligned).transcribe(
            _chunks(b"raw"), context=""
        )
    )

    assert transcription.segments[0].aligned is True
    assert transcription.duration == 0.90  # 末字 End，不是 ASR 給的切點 1.2
    assert transcription.alignment.audio_duration == 2.0  # 音檔長度，與上者無關


def test_transcript_survives_when_alignment_is_unavailable(tmp_path):
    # ADR-0004 的第二層降級：逐字稿有獨立價值，不因評分這項附加功能失效而一併不可得。
    # 降級本身由 AlignerClient.align 保證（它不拋出），本測試釘的是**本 module 不會把
    # 它搞砸**——例如讓缺漏的段落污染彙總數字。
    result = _result([Segment(Start=0.0, End=1.2, Speaker="0", Content="你好")])
    unavailable = StubAlignerClient(
        result=[
            SegmentAlignment(
                words=[],
                bounds=(0.0, 1.7),
                omission=Omission("batch_failed", "第 1／1 批對齊失敗：連線被拒"),
            )
        ]
    )

    transcription = asyncio.run(
        _transcriber(tmp_path, result, aligner=unavailable).transcribe(
            _chunks(b"raw"), context=""
        )
    )

    assert transcription.transcription_only == "你好"  # 逐字稿照常
    assert transcription.segments[0].aligned is False
    assert transcription.segments[0].words == []
    assert (transcription.segments[0].Start, transcription.segments[0].End) == (0.0, 1.2)
    assert transcription.alignment.speech_start is None  # 未對齊段不得混入彙總
    assert transcription.alignment.aligned_duration == 0.0


def test_context_prompt_reaches_the_asr_client(tmp_path):
    # Context prompt 是 Hotword 唯一作用於模型的途徑（CONTEXT.md）。它由端點層編好後
    # 傳入，本 module 原樣轉交——中途改寫或漏傳都會讓 Hotword 靜默失效。
    result = _result([Segment(Start=0.0, End=1.0, Speaker="0", Content="你好")])
    asr = StubAsrClient(result=result)
    transcriber = Transcriber(
        intake=FakeIntake(tmp_path),
        asr=asr,
        aligner=StubAlignerClient(),
        sample_rate=_RATE,
    )

    asyncio.run(transcriber.transcribe(_chunks(b"raw"), context="李慕梅、王思婷"))

    assert asr.last_context == "李慕梅、王思婷"
