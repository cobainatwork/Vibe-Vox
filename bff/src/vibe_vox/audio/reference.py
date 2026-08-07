"""參考音的可用性：Voice 的不變量在此判定（#44、#45）。

**這條不變量只有一個 owner。** 參考音在建立音色時就固定了，故可用性驗一次就夠；放在
合成路徑等於每次請求都算一遍，而那時已經沒有人能修正它——操作者早就離開了那個畫面。
合成路徑因此可以假設它成立，見 `adapters/base.py` 的 `TtsClient.synthesize`。

「可用」是三件事的合集：容器合法（`save_upload` 讀檔頭 magic 判定）、可解碼、時長在
模型端的硬界內。後兩者由本模組的時長量測一併涵蓋——量不到秒數的檔案就是解不開的檔案。
"""

from collections.abc import AsyncIterator
from pathlib import Path

from vibe_vox.audio.duration import DurationUnavailable, probe_duration
from vibe_vox.audio.intake import save_upload

# vLLM-Omni 的 /v1/audio/speech 對 ref_audio 強制的範圍（serving_speech.py 的
# _REF_AUDIO_MIN_DURATION／_REF_AUDIO_MAX_DURATION，見 docs/superpowers/specs/
# 2026-08-05-voxcpm2-serving-transport.md §3.2.1）。超界時端點回的是 ValueError 的文字
# 而非音訊。
#
# 不進 config：這是模型端的硬限制而非部署可調的值，改它只會讓超界的請求改在模型端失敗，
# 而那裡回的錯誤碼是可重試的 502。#43 的錄音介面要用同一組數字。
REF_AUDIO_MIN_SECONDS = 1.0
REF_AUDIO_MAX_SECONDS = 30.0


class RefAudioUnusable(Exception):
    """參考音不能作為 Voice 的身分錨點。

    **訊息本身就是給操作者看的原因**，不是給開發者看的除錯字串：建立路徑把它當成 400 的
    message（`main.py`），音色清單把它當成該列的不可用說明（`api/admin_voices.py`）。
    兩處共用同一句話，否則同一件事會有兩種措辭而其中一種遲早過期。
    """


class RefAudioDurationOutOfRange(RefAudioUnusable):
    def __init__(self, seconds: float) -> None:
        super().__init__(
            f"參考音時長 {seconds:.1f} 秒不在允許範圍 "
            f"{REF_AUDIO_MIN_SECONDS:.1f} 至 {REF_AUDIO_MAX_SECONDS:.1f} 秒內，"
            "請裁剪後再上傳。"
        )
        self.seconds = seconds


class RefAudioUnreadable(RefAudioUnusable):
    def __init__(self) -> None:
        super().__init__("無法讀取參考音的時長，該檔可能已遺失或不是可解碼的音訊。")


async def save_reference_audio(
    chunks: AsyncIterator[bytes], *, temp_dir: Path, max_bytes: int
) -> Path:
    """落地上傳的參考音並判定其可用性，回暫存檔路徑；不合格即清檔並拋。

    不轉碼：參考音原樣落地是既有決策（`docs/spec.md` 持久化段，同 design 音色定版存
    48 kHz 原生取樣率的理由），且降採樣對 voice cloning 品質的影響未量測過。
    """
    path = await save_upload(chunks, temp_dir=temp_dir, max_bytes=max_bytes)
    try:
        await _ensure_usable(path)
    except BaseException:
        # 不合格的上傳當場清掉，不留給清理程序：那條路徑要等寬限期，而這裡已經確定
        # 這個檔不會有任何人引用。
        path.unlink(missing_ok=True)
        raise
    return path


async def _ensure_usable(path: Path) -> None:
    """參考音可用即通過，否則拋 `RefAudioUnusable` 的子類。"""
    try:
        seconds = await probe_duration(path)
    except DurationUnavailable as exc:
        raise RefAudioUnreadable() from exc
    if not REF_AUDIO_MIN_SECONDS <= seconds <= REF_AUDIO_MAX_SECONDS:
        raise RefAudioDurationOutOfRange(seconds)


async def unusable_reason(path: Path) -> str | None:
    """回該參考音不可用的原因，可用則 None。

    供音色清單標示既有音色——本票之前建立的音色未經任何驗證，可能超界或檔案已遺失
    （DB 還原、volume 換掛、人工刪檔）。用同一個 `_ensure_usable` 而非另寫一組判準：
    分成兩份的話，清單說可用而合成失敗的組合遲早出現。
    """
    try:
        await _ensure_usable(path)
    except RefAudioUnusable as exc:
        return str(exc)
    return None
