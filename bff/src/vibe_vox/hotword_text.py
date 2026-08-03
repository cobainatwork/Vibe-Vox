"""Hotword 文字清洗（spec：Hotword 內容為不可信輸入）。

剝除可能與底層 ASR／LLM 特殊 token 衝突的保留標記（<|...|>，含語者與
時間戳標記）與控制字元，使 term 注入 context 時以字面 tokenize，不致
被解讀為特殊 token 而破壞 prompt template 或 Who/When/What 的 JSON 結構。
"""

import re
import unicodedata


class InvalidHotwordTerm(ValueError):
    """term 清洗後為空，無有效內容可儲存。"""


class ContextBudgetExceeded(Exception):
    """編譯後的 context 估算超出 token 預算上界。"""

    def __init__(self, estimate: int, budget: int) -> None:
        self.estimate = estimate
        self.budget = budget


# <|...|> 特殊 token 標記（語者、時間戳等），整段移除。
_SPECIAL_TOKEN = re.compile(r"<\|.*?\|>")


def sanitize_text(raw: str) -> str:
    without_tokens = _SPECIAL_TOKEN.sub("", raw)
    without_controls = "".join(
        ch for ch in without_tokens if unicodedata.category(ch) != "Cc"
    )
    return without_controls.strip()


def clean_term(raw: str) -> str:
    """清洗 term；若清洗後無有效內容則 raise InvalidHotwordTerm。"""
    term = sanitize_text(raw)
    if not term:
        raise InvalidHotwordTerm
    return term


def compile_context(terms: list[str]) -> str:
    """把啟用中的 term 編譯成一段自由文字（Context prompt），以頓號連接。"""
    return "、".join(terms)


def _is_cjk(ch: str) -> bool:
    return (
        "一" <= ch <= "鿿"  # CJK 統一表意文字
        or "぀" <= ch <= "ヿ"  # 日文假名
        or "가" <= ch <= "힣"  # 韓文音節
    )


def estimate_tokens(text: str) -> int:
    """保守高估的 token 數，作伺服器端預算的安全上界（非精確模型 token）。

    CJK 每字元計 2 token、其餘每 3 字元計 1 token；寧可高估誤擋接近上限者，
    不放過會打爆 GPU 的過大 context。
    """
    cjk = sum(1 for ch in text if _is_cjk(ch))
    other = len(text) - cjk
    return cjk * 2 + (other + 2) // 3


def enforce_context_budget(context: str, budget: int) -> int:
    """context 估算超出預算即 raise ContextBudgetExceeded；否則回傳估算 token 數。"""
    estimate = estimate_tokens(context)
    if estimate > budget:
        raise ContextBudgetExceeded(estimate, budget)
    return estimate
