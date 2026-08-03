"""對齊模型的注入邊界。此檔之後才有 GPU，之前沒有。

沿用 ADR-0001 的 stub 邊界模式：HTTP 層只認識 Aligner 這個 Protocol，
真實推論由 QwenAligner 實作，torch 與 qwen_asr 於 load() 內才 import。
"""

from typing import Any, Protocol, runtime_checkable

import numpy as np
from pydantic import BaseModel


class Word(BaseModel):
    """一個對齊單位的時間戳，秒。

    中文為單一漢字而非語意上的詞（CONTEXT.md 的 Word 詞條）。此性質由
    qwen-asr 的 Qwen3ForceAlignProcessor.split_segment_with_chinese 決定：
    CJK 字元逐字切出，連續的拉丁字母與數字成一個單位。標點與其他符號在
    clean_token 被剝除，故不會有對應的 Word。
    """

    text: str
    start: float
    end: float


@runtime_checkable
class Aligner(Protocol):
    def align(
        self,
        waveforms: list[tuple[np.ndarray, int]],
        texts: list[str],
        languages: list[str],
    ) -> list[list[Word]]:
        """對齊一批（音訊, 文字），回每筆的字級時間戳，順序與輸入一一對應。

        languages 保留在此是因為模型 API 需要它，儘管 HTTP 層恆傳 Chinese
        （不開放呼叫端指定，見 main.py 的 _LANGUAGE）。
        """
        ...


class QwenAligner:
    """Qwen3-ForcedAligner 的包裝。

    選 qwen-asr 套件而非 -hf 變體走 transformers：後者在官方 Transformers
    release 納入前需 `pip install git+...`，版本釘不住、build 不可重現；
    qwen-asr 0.0.6 釘死 transformers==4.57.6，且官方 model card 的首選範例
    即此路徑。兩者皆為 transformers backend（非 vLLM），符 ADR-0004。

    torch 與 qwen_asr 於 load() 內 import，故無 GPU 的環境（CI、開發機）
    仍能載入本模組並測試 HTTP 層。
    """

    def __init__(self, model_id: str, device: str) -> None:
        self._model_id = model_id
        self._device = device
        self._model: Any | None = None

    def load(self) -> None:
        import torch
        from qwen_asr import Qwen3ForcedAligner

        # bfloat16 為官方範例與 config.json 的 dtype；RTX 6000 Ada（sm_89）原生支援。
        self._model = Qwen3ForcedAligner.from_pretrained(
            self._model_id, dtype=torch.bfloat16, device_map=self._device
        )

    def align(
        self,
        waveforms: list[tuple[np.ndarray, int]],
        texts: list[str],
        languages: list[str],
    ) -> list[list[Word]]:
        if self._model is None:
            raise RuntimeError("QwenAligner.load() 未先呼叫。")
        # align() 內部一律轉 mono 16k float32，故取樣率不必在此對齊；
        # 傳 (ndarray, sr) 而非路徑，免落地暫存檔。
        results = self._model.align(audio=waveforms, text=texts, language=languages)
        return [
            [Word(text=item.text, start=item.start_time, end=item.end_time) for item in result]
            for result in results
        ]
