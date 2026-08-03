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

在能跑 `nvidia-smi` 的遠端 GPU 機上（測 #10 / #11 那台）。**零設定**：VibeVoice-ASR 權重與官方 vllm_plugin 已 bake 進 vllm image，不必填任何模型 id 或路徑。

1. 拉 code：`git pull`（main）。
2. 啟動（首次 build vllm image：clone 官方 VibeVoice、裝 vllm plugin、把 `microsoft/VibeVoice-ASR-HF` 權重下載打包進 image，故首次較久；TTS 未做已置於 profile 自動跳過，日後要一起起才加 `--profile tts`）：
   ```bash
   docker compose up --build
   ```
3. 等 vllm 起來（首次久、之後快），確認就緒：
   ```bash
   docker compose logs -f vllm            # 看到 vllm serve 就緒
   curl http://localhost/api/health       # 應回 {"data":{"asr":{"ready":true},...}}
   ```
4. 瀏覽器開 `http://<遠端機 IP>`（正式版前端在 port 80）→「ASR 測試」分頁 → 上傳音檔 → 送出。

## 測不通時，把這些給我 debug

- `docker compose logs vllm`：vLLM / VibeVoice plugin 啟動問題。
- `docker compose logs bff`：串接 / 解析錯誤。
- 前端「原始」視圖顯示的辨識回傳內容：驗證輸出格式、對齊解析的關鍵。

## 待遠端驗證（測試目的）

vLLM 對 VibeVoice-ASR 的支援已確認（用官方 `vllm_plugin`）；client 請求格式（`audio_url` data URL、要求 Start/End/Speaker/Content 的 prompt、model=vibevoice）已對齊官方 demo。剩下真跑才能確認的：

1. **模型實際輸出的 JSON 形狀** vs `_parse`（現吃「`{segments:[...]}`」或「直接 array」，欄位 Start/End/Speaker/Content）。若不符，把前端「原始」視圖的實際輸出給我，我對齊 `_parse`。
2. **共卡記憶體**：TTS 未上線前 ASR 獨佔 GPU；TTS 上線時需調 `gpu_memory_utilization`（見 ADR-0001）。
