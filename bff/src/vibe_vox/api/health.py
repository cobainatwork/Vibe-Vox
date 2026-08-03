"""健康檢查：回報 ASR 與 TTS 模型服務的就緒狀態（User Story 47）。"""

import asyncio

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/api/health")
async def health(request: Request) -> dict:
    asr = request.app.state.asr_client
    tts = request.app.state.tts_client
    # 兩個探測互不相依，併發執行以免輪詢熱路徑延遲加倍。
    asr_ready, tts_ready = await asyncio.gather(asr.health(), tts.health())
    return {"data": {"asr": {"ready": asr_ready}, "tts": {"ready": tts_ready}}}
