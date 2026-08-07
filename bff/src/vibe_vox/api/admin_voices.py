"""管理平面音色 CRUD（/api/admin/voices）。消費端只讀清單，見 api/tts.py。

建立為跨元件操作（檔案落地加 DB 寫入）。順序為「先產物後落庫」：參考音先落到
正式路徑，確認成功才寫 DB；DB 失敗則刪除該檔。這個方向使不變量成立——不會有
指向不存在檔案的 DB 列（spec Voice 音色段）。反向殘留的孤兒檔由清理程序回收。

落地檔名一律為伺服器生成的 UUID，不由 name 或原始檔名推導（spec 持久化決策）。
"""

import asyncio
import shutil
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, Form, Request, UploadFile
from pydantic import BaseModel

from vibe_vox.audio.reference import save_reference_audio, unusable_reason

router = APIRouter()

# name 是操作者辨識音色的唯一依據，且進 UI 表格與下拉選單。上限取 200 字元：
# 足夠描述角色（「客戶-中年男性-謹慎-第二次拜訪」約 20 字），又擋掉把整段文字
# 當名稱貼進來撐爆版面的情況。
MAX_VOICE_NAME_CHARS = 200


class InvalidVoiceName(Exception):
    """name 清洗後為空，或超過長度上限。"""


def clean_voice_name(raw: str) -> str:
    """去除首尾空白與控制字元；空白或過長即 raise。"""
    name = "".join(ch for ch in raw if ch.isprintable()).strip()
    if not name or len(name) > MAX_VOICE_NAME_CHARS:
        raise InvalidVoiceName(raw)
    return name


class VoiceRename(BaseModel):
    name: str

_CHUNK_BYTES = 1024 * 1024


async def _stream(file: UploadFile):
    while data := await file.read(_CHUNK_BYTES):
        yield data


@router.post("/api/admin/voices/clone", status_code=201)
async def create_clone_voice(
    request: Request,
    name: str = Form(...),
    language: str = Form(...),
    ref_audio: UploadFile = File(...),
    ref_text: str | None = Form(None),
) -> dict:
    """以上傳的參考音建立 clone 音色。

    ref_text 為選填的管理用 metadata，**不進合成路徑**：送了會讓 VoxCPM2 落到
    Hi-Fi 模式並靜默忽略 Instruction（docs/api/tts.md §5.2）。
    """
    settings = request.app.state.settings
    repo = request.app.state.voices
    name = clean_voice_name(name)

    # 走 save_reference_audio 而非 save_upload：參考音的可用性（容器、可解碼、時長）
    # 是 Voice 的不變量，在此判定一次，合成路徑不再重算（audio/reference.py）。
    temp = await save_reference_audio(
        _stream(ref_audio),
        temp_dir=settings.temp_dir,
        max_bytes=settings.voice_ref_audio_max_bytes,
    )

    voice_dir = Path(settings.voice_dir)
    voice_dir.mkdir(parents=True, exist_ok=True)
    final = voice_dir / uuid4().hex
    # shutil.move 而非 Path.replace：暫存區與音色目錄在正式部署是不同的檔案系統
    # （/app/var/tmp 在容器可寫層、/data/voices 在 volume），os.replace 跨檔案系統
    # 會 EXDEV。move 在同檔案系統時仍走 rename，不付複製成本。
    shutil.move(str(temp), str(final))

    try:
        created = repo.create(
            name=name,
            type="clone",
            language=language,
            ref_audio_path=final,
            ref_text=ref_text,
        )
    except BaseException:
        final.unlink(missing_ok=True)
        raise

    return {"data": created}


async def _with_usability(voices: list[dict]) -> list[dict]:
    """為每個音色附上 `unusable_reason`：可用則 None，否則是給操作者看的一句原因。

    **併發量測而非逐列 await。** wav 只讀標頭、不起子進程，但非 wav 的音色各要一次
    ffprobe（單次上限 30 秒）；序列化的話幾個損壞的音色就能讓這個端點撐過反向代理的
    逾時，操作者拿到 HTML 錯誤頁而不是清單。
    """
    reasons = await asyncio.gather(
        *(unusable_reason(Path(v["ref_audio_path"])) for v in voices)
    )
    return [v | {"unusable_reason": r} for v, r in zip(voices, reasons, strict=True)]


@router.get("/api/admin/voices")
async def list_voices(request: Request) -> dict:
    """音色清單，附帶參考音的可用性。

    建立時的驗證只對新音色生效。該不變量之前建立的音色未經任何檢查，而參考音也可能在
    建立之後才失效（DB 還原、volume 換掛、人工刪檔）。清單是操作者唯一看得到音色的地方，
    不標出來的話他只會看到某個音色試聽失敗，而錯誤訊息出現在別的畫面上。

    消費端的 `GET /api/tts/voices` 不帶這個欄位——它的形狀是凍結的契約（ADR-0003），
    且消費端對此無能為力（它只會在合成時收到 409 `VOICE_UNUSABLE`）。
    """
    return {"data": await _with_usability(request.app.state.voices.list())}


@router.put("/api/admin/voices/{vid}")
async def rename_voice(vid: str, body: VoiceRename, request: Request) -> dict:
    return {"data": request.app.state.voices.rename(vid, clean_voice_name(body.name))}


@router.delete("/api/admin/voices/{vid}")
async def delete_voice(vid: str, request: Request) -> dict:
    """移除 DB 紀錄；實體參考音檔留給清理程序回收，見 repository.delete 的理由。"""
    request.app.state.voices.delete(vid)
    return {"success": True}
