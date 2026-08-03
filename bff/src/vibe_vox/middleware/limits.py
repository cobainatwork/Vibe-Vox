"""重量級請求資源護欄：併發上限與請求逾時（spec 安全與資源邊界）。

spec 將逾時與併發列為「每個重量級端點」層級。此護欄為重量級端點（上傳、
轉碼、辨識、合成）共用的骨架；端點以 `async with guard.slot():` 包住工作：
- 併發達上限時 load-shed 回 503（不排隊）。
- 逾時以 asyncio.timeout 在同一 task 內真正中止工作、釋放資源後回 504。

刻意不用 BaseHTTPMiddleware + asyncio.wait_for：該組合無法取消下游 handler，
逾時只會等 handler 跑完，違背釋放資源的目的。
"""

import asyncio
from contextlib import asynccontextmanager


class HeavyRequestRejected(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message


class HeavyRequestGuard:
    def __init__(self, max_concurrent: int, timeout_seconds: float) -> None:
        self._max = max_concurrent
        self._timeout = timeout_seconds
        self._active = 0

    @asynccontextmanager
    async def slot(self, timeout_seconds: float | None = None):
        # 單執行緒 asyncio：檢查與遞增間無 await，無競態。
        # timeout_seconds 可由端點覆寫預設（spec：逾時為每個重量級端點層級，
        # 例如長時 ASR 需高於預設值）。
        if self._active >= self._max:
            raise HeavyRequestRejected(
                503, "TOO_MANY_REQUESTS", "重量級請求已達同時處理上限，請稍後重試。"
            )
        timeout = self._timeout if timeout_seconds is None else timeout_seconds
        self._active += 1
        try:
            async with asyncio.timeout(timeout):
                yield
        except (asyncio.TimeoutError, TimeoutError):
            raise HeavyRequestRejected(
                504, "REQUEST_TIMEOUT", "請求處理逾時，已中止並釋放資源。"
            )
        finally:
            self._active -= 1
