# ASR 端對端測試環境

兩種環境，看你要測什麼。

## A. 本機看前端 + 走完流程（無 GPU，假辨識）

不需顯示卡。bff 以 stub 啟動、回固定假辨識，讓你看前端畫面、走完「上傳 → 辨識 → 三視圖」流程，驗證前端與串接邏輯。

```bash
docker compose -f docker-compose.dev.yml up --build
```

- 開 http://localhost:5173
- 上「ASR 測試」分頁 → 上傳任意音檔 → 送出辨識 → 看到假辨識結果（分段/純文字/原始三視圖、複製、匯出）。

不想用 docker 也可以直接跑（兩個終端機）：

```bash
# 終端 1：bff（stub 模式）
cd bff
VIBE_QWEN_USE_STUB_MODELS=true uv run uvicorn vibe_qwen.main:create_app --factory --port 8000

# 終端 2：前端
cd frontend
npm run dev
```

## B. 遠端 GPU 真實辨識（連真 vLLM）

在能跑 `nvidia-smi` 的遠端 GPU 機上（測 #10 / #11 那台）。

1. 拉 code：`git pull`（main）。
2. 設定環境變數：
   ```bash
   cp .env.example .env
   ```
   編輯 `.env`，填入 `VIBE_QWEN_ASR_MODEL`＝VibeVoice-ASR 的模型 id（HuggingFace 路徑或掛載的本地路徑）。這是唯一必填項。
3. 啟動（只起 ASR 相關；TTS 未做，已置於 profile 自動跳過，不會擋）：
   ```bash
   docker compose up --build
   ```
   （日後 TTS 做好、要一起起，才加 `--profile tts`。）
4. 等 vllm 載入模型（首次會下載權重，較久），確認就緒：
   ```bash
   docker compose logs -f vllm            # 看到模型載入完成
   curl http://localhost/api/health       # 應回 {"data":{"asr":{"ready":true},...}}
   ```
5. 瀏覽器開 `http://<遠端機 IP>`（正式版前端在 port 80）→「ASR 測試」分頁 → 上傳音檔 → 送出。

## 測不通時，把這些給我 debug

- `docker compose logs vllm`：vLLM 起不來、或不支援這個模型的 audio input。
- `docker compose logs bff`：串接/解析錯誤。
- 前端「原始」視圖顯示的辨識回傳內容：這是驗證 VibeVoice-ASR 實際輸出格式、對齊解析的關鍵。

## 待遠端驗證的關鍵未知（測試目的）

1. **vLLM 能否 serve VibeVoice-ASR 並吃 audio input**（chat completions 的 `input_audio`）。若不支援，需改 serving 方式（非 vLLM 或不同端點）。
2. **模型實際輸出的 JSON 格式** vs 現行解析假設（`bff/src/vibe_qwen/adapters/vllm_asr.py` 的 `_parse` 假設「含 segments 的物件」）。不符則調 `_parse`——把「原始」視圖的實際輸出給我，我來對齊。
