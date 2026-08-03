"""管理平面 Hotword CRUD（/api/admin/hotwords）：完整形狀與啟用切換、搜尋。

消費端契約端點另見 api/hotwords.py，不與此前綴混用。
"""

from fastapi import APIRouter, File, Query, Request, UploadFile
from pydantic import BaseModel
from starlette.responses import JSONResponse, Response

from vibe_vox.hotword_io import (
    ImportLimitExceeded,
    parse_import,
    to_csv,
    to_export_rows,
)
from vibe_vox.hotword_text import compile_context, enforce_context_budget
from vibe_vox.persistence.hotwords import HotwordNotFound

router = APIRouter()


class HotwordCreate(BaseModel):
    term: str
    note: str | None = None


class EnabledPatch(BaseModel):
    enabled: bool


@router.post("/api/admin/hotwords", status_code=201)
async def create_hotword(body: HotwordCreate, request: Request) -> dict:
    repo = request.app.state.hotwords
    return {"data": repo.create(term=body.term, note=body.note)}


@router.get("/api/admin/hotwords")
async def list_hotwords(request: Request, q: str | None = None) -> dict:
    repo = request.app.state.hotwords
    return {"data": repo.list_all(query=q)}


@router.get("/api/admin/hotwords/export")
async def export_hotwords(
    request: Request, fmt: str = Query("json", alias="format")
) -> Response:
    repo = request.app.state.hotwords
    rows = to_export_rows(repo.list_all())
    if fmt == "csv":
        return Response(
            to_csv(rows),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=hotwords.csv"},
        )
    return JSONResponse(
        rows, headers={"Content-Disposition": "attachment; filename=hotwords.json"}
    )


@router.post("/api/admin/hotwords/import")
async def import_hotwords(
    request: Request,
    fmt: str = Query("json", alias="format"),
    file: UploadFile = File(...),
) -> dict:
    settings = request.app.state.settings
    max_bytes = settings.hotword_import_max_bytes
    oversize = f"匯入檔案超過上限 {max_bytes} bytes。"
    if file.size is not None and file.size > max_bytes:
        raise ImportLimitExceeded(oversize)
    content = await file.read()
    if len(content) > max_bytes:
        raise ImportLimitExceeded(oversize)
    rows = parse_import(content, fmt)
    if len(rows) > settings.hotword_import_max_rows:
        raise ImportLimitExceeded(
            f"匯入筆數 {len(rows)} 超過上限 {settings.hotword_import_max_rows}。"
        )
    created, updated = request.app.state.hotwords.upsert_many(rows)
    return {"data": {"created": created, "updated": updated}}


@router.get("/api/admin/hotwords/context/preview")
async def preview_context(request: Request) -> dict:
    repo = request.app.state.hotwords
    budget = request.app.state.settings.hotword_context_token_budget
    context = compile_context([h["term"] for h in repo.list_enabled()])
    estimate = enforce_context_budget(context, budget)
    return {
        "data": {
            "context": context,
            "token_estimate": estimate,
            "token_budget": budget,
        }
    }


@router.put("/api/admin/hotwords/{hid}")
async def update_hotword(hid: str, body: HotwordCreate, request: Request) -> dict:
    repo = request.app.state.hotwords
    updated = repo.update(hid, term=body.term, note=body.note)
    if updated is None:
        raise HotwordNotFound
    return {"data": updated}


@router.patch("/api/admin/hotwords/{hid}/enabled")
async def set_hotword_enabled(hid: str, body: EnabledPatch, request: Request) -> dict:
    repo = request.app.state.hotwords
    updated = repo.set_enabled(hid, body.enabled)
    if updated is None:
        raise HotwordNotFound
    return {"data": updated}


@router.delete("/api/admin/hotwords/{hid}")
async def delete_hotword(hid: str, request: Request) -> dict:
    repo = request.app.state.hotwords
    if not repo.delete(hid):
        raise HotwordNotFound
    return {"data": {"success": True}}
