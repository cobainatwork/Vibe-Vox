"""消費端 TTS 契約（/api/tts/*）：形狀凍結，不套 {data} 信封（ADR-0003）。

完整規格見 docs/api/tts.md。管理平面的音色 CRUD 見 api/admin_voices.py。
"""

from fastapi import APIRouter, Request

router = APIRouter()

MODEL_NAME = "voxcpm2"


def _to_consumer(v: dict) -> dict:
    """消費端只看得到挑音色需要的四欄，不外露參考音路徑與逐字稿。"""
    return {
        "id": v["id"],
        "name": v["name"],
        "type": v["type"],
        "language": v["language"],
    }


@router.get("/api/tts/models")
async def list_models() -> dict:
    return {"models": [MODEL_NAME]}


@router.get("/api/tts/voices")
async def list_voices(request: Request) -> dict:
    repo = request.app.state.voices
    return {"voices": [_to_consumer(v) for v in repo.list()]}
