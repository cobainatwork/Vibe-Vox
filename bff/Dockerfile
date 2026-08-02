# BFF：uv 建置、非 root、含 healthcheck。
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /bin/uv

# FFmpeg：音檔轉碼的執行期依賴（#4 audio 模組）。
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH"

# 先裝依賴（不含專案原始碼）以最佳化 layer 快取。
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# 再放入原始碼並安裝專案本身。
COPY src ./src
RUN uv sync --frozen --no-dev

RUN useradd --uid 1001 --create-home app && chown -R app /app
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health').status==200 else 1)"

CMD ["uvicorn", "vibe_qwen.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
