"""消費端 Hotword 契約（/api/hotwords）：約束性 {id, word} 形狀（ADR-0003）。

僅暴露 AI_practise 依賴的最小子集：word 即 term，形狀凍結、不套 {data}
信封，且只回 enabled 者（消費端無 enabled 欄位，只應取得會套用的詞彙）。
管理平面完整 CRUD 見 api/admin_hotwords.py。
"""

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter()


class HotwordWordCreate(BaseModel):
    word: str


def _to_word(h: dict) -> dict:
    return {"id": h["id"], "word": h["term"]}


@router.get("/api/hotwords")
async def list_hotwords(request: Request) -> list[dict]:
    repo = request.app.state.hotwords
    return [_to_word(h) for h in repo.list_enabled()]


@router.post("/api/hotwords", status_code=201)
async def create_hotword(body: HotwordWordCreate, request: Request) -> dict:
    repo = request.app.state.hotwords
    return _to_word(repo.create(term=body.word))


@router.delete("/api/hotwords/{hid}")
async def delete_hotword(hid: str, request: Request) -> dict:
    repo = request.app.state.hotwords
    repo.delete(hid)
    return {"success": True}
