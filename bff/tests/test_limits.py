"""Phase A：重量級請求資源護欄 HeavyRequestGuard（併發上限 + 請求逾時）。

護欄為重量級端點共用；#1 尚無重量級端點，故以測試專用路由驗證。逾時以
asyncio.timeout 在同一 task 內真正中止工作（非 BaseHTTPMiddleware+wait_for
的假逾時），故以耗時斷言確認工作被中止而非等其跑完。
"""

import asyncio
import time

import httpx
from httpx import ASGITransport

from vibe_vox.config import Settings
from vibe_vox.main import create_app


def _heavy_app(**settings_kw):
    app = create_app(settings=Settings(**settings_kw))
    release = asyncio.Event()

    async def heavy():
        async with app.state.heavy_guard.slot():
            await asyncio.sleep(3.0)
            return {"data": "ok"}

    async def blocking():
        async with app.state.heavy_guard.slot():
            await release.wait()
            return {"data": "ok"}

    app.add_api_route("/__heavy__", heavy, methods=["GET"])
    app.add_api_route("/__block__", blocking, methods=["GET"])
    return app, release


async def _timeout_scenario():
    app, _ = _heavy_app(request_timeout_seconds=0.3, max_concurrent_heavy_requests=8)
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        t0 = time.perf_counter()
        resp = await client.get("/__heavy__")
        elapsed = time.perf_counter() - t0

    assert resp.status_code == 504
    assert resp.json()["error"]["code"]
    assert elapsed < 1.5  # 逾時真正中止 sleep(3.0)，未等其跑完


def test_heavy_request_timeout_aborts_and_returns_504():
    asyncio.run(_timeout_scenario())


async def _concurrency_scenario():
    app, release = _heavy_app(max_concurrent_heavy_requests=2, request_timeout_seconds=5.0)
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        held = [asyncio.create_task(client.get("/__block__")) for _ in range(2)]
        await asyncio.sleep(0.1)  # 讓兩請求進入並佔滿名額

        shed = await client.get("/__block__")  # 第 3 個應被 load-shed
        assert shed.status_code == 503
        assert shed.json()["error"]["code"]

        release.set()
        done = await asyncio.gather(*held)
        assert [r.status_code for r in done] == [200, 200]


def test_heavy_request_concurrency_limit_sheds_excess_with_503():
    asyncio.run(_concurrency_scenario())


async def _override_scenario():
    # 護欄預設逾時 30s，端點以 slot(timeout_seconds=0.3) 覆寫（spec：每端點逾時）。
    app = create_app(
        settings=Settings(request_timeout_seconds=30.0, max_concurrent_heavy_requests=8)
    )

    async def slow():
        async with app.state.heavy_guard.slot(timeout_seconds=0.3):
            await asyncio.sleep(3.0)
            return {"data": "ok"}

    app.add_api_route("/__override__", slow, methods=["GET"])
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        t0 = time.perf_counter()
        resp = await client.get("/__override__")
        elapsed = time.perf_counter() - t0

    assert resp.status_code == 504
    assert elapsed < 1.5  # 覆寫的 0.3s 生效，非預設 30s


def test_heavy_request_per_call_timeout_override():
    asyncio.run(_override_scenario())
