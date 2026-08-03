"""Hotword 匯入匯出：欄位契約固定為 term、note、enabled。"""

import csv
import io
import json

FIELDS = ["term", "note", "enabled"]


class ImportLimitExceeded(Exception):
    """匯入檔案大小或筆數超過可設定上限。"""

    def __init__(self, message: str) -> None:
        self.message = message


class ImportParseError(Exception):
    """匯入內容格式或標頭不合契約（非法 JSON、非陣列、缺 term 欄等）。"""

    def __init__(self, message: str) -> None:
        self.message = message


def parse_import(content: bytes, fmt: str) -> list[dict]:
    """把上傳內容解析成正規化的 {term, note, enabled} 列（enabled 缺省 true）。"""
    try:
        if fmt == "csv":
            reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
            return [_normalize(row) for row in reader]
        data = json.loads(content)
        if not isinstance(data, list):
            raise ImportParseError("JSON 匯入須為物件陣列。")
        return [_normalize(item) for item in data]
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ImportParseError(f"匯入內容無法解析：{exc}") from exc
    except (KeyError, TypeError) as exc:
        raise ImportParseError(f"匯入資料缺少必要欄位或格式錯誤：{exc}") from exc


def _normalize(item: dict) -> dict:
    return {
        "term": item["term"],
        "note": item.get("note") or None,
        "enabled": _parse_enabled(item.get("enabled")),
    }


def _parse_enabled(value: object) -> bool:
    if value is None or value == "":
        return True  # 缺省 true
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes", "y")


def to_export_rows(hotwords: list[dict]) -> list[dict]:
    """取匯入匯出契約的欄位子集，捨棄 id 與 timestamps。"""
    return [{field: h[field] for field in FIELDS} for h in hotwords]


def to_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(FIELDS)
    for r in rows:
        writer.writerow([r["term"], r["note"] or "", "true" if r["enabled"] else "false"])
    return buf.getvalue()
