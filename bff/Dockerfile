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

# /data 為持久化目錄，放 SQLite 單檔與音色參考音，由 compose 掛 volume（#33，理由見 docs/spec.md
# 的「持久化」）。**它必須在 image 裡就存在且屬 app**：named volume 首次掛載時從
# image 的對應路徑繼承 owner，路徑不存在則 volume 屬 root，uid 1001 寫不進去，服務
# 會在 sqlite3.connect 建檔時以 Permission denied 啟動失敗。
RUN useradd --uid 1001 --create-home app \
    && mkdir -p /data \
    && chown -R app /app /data
USER app

# 與上面的 mkdir 是同一個決定的兩面，改一邊會壞。暫存音檔刻意不在此（見 config.py
# 的 temp_dir，仍為 /app/var/tmp）：用畢即刪、隨容器銷毀正是預期行為。
ENV VIBE_VOX_DB_PATH=/data/vibe_vox.db
# 參考音是音色的本體，不是快取：掉了音色就不存在了，故與 DB 同置於 volume。
# 子目錄由執行期建立，/data 已屬 app 故 uid 1001 建得起來。
ENV VIBE_VOX_VOICE_DIR=/data/voices

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health').status==200 else 1)"

CMD ["uvicorn", "vibe_vox.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
