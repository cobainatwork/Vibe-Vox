"""Origin/Referer 防護：擋下第三方網頁對未認證內網 API 的跨來源狀態變更偽造。

威脅模型見 spec 安全邊界：瀏覽器對跨來源的不安全請求必帶 Origin。故規則為
「來源存在且不在白名單才拒」；缺 Origin 且缺 Referer 者放行，以免擋掉不帶
來源標頭的受信任 server-to-server 消費端（AI_practise）。
"""

from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class OriginGuardMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, allowed_origins: list[str]) -> None:
        super().__init__(app)
        self._allowed = set(allowed_origins)

    async def dispatch(self, request: Request, call_next):
        if request.method not in _SAFE_METHODS:
            origin = self._request_origin(request)
            if origin is not None and not self._is_allowed(origin, request):
                return JSONResponse(
                    status_code=403,
                    content={
                        "error": {
                            "code": "ORIGIN_FORBIDDEN",
                            "message": "跨來源狀態變更請求被拒：請求來源不在允許的前端白名單。",
                        }
                    },
                )
        return await call_next(request)

    def _is_allowed(self, origin: str, request: Request) -> bool:
        if origin in self._allowed:
            return True
        # 同源（前端與 API 經同一 host:port，nginx 以 Host $host 保留）非 CSRF，放行——
        # 支援動態 IP 部署零設定；第三方（Origin ≠ Host）仍被擋。
        host = request.headers.get("host")
        return bool(host and urlsplit(origin).netloc == host)

    @staticmethod
    def _request_origin(request: Request) -> str | None:
        origin = request.headers.get("origin")
        if origin:
            return origin
        referer = request.headers.get("referer")
        if referer:
            parts = urlsplit(referer)
            if parts.scheme and parts.netloc:
                return f"{parts.scheme}://{parts.netloc}"
        return None
