# 部署與診斷

遠端 GPU 機的操作知識。每一條都來自實際踩過的失效，不是推測。

---

## 1. 遠端機

**`http://10.2.66.102:8088`**，路徑 `/api/` 由 nginx 反向代理至 BFF。對外只開這一個 HTTP 埠，模型服務的埠**不**對外。

**有 SSH。** `~/.ssh/config` 的 `qwen-gpu` 別名（`User root`，金鑰 `qwen_gpu_id_ed25519`），repo 在 `/opt/Vibe-Vox`，主機名 `vm-02-ubuntu24`。本檔曾寫「無 shell 存取」，那是錯的，且代價具體：#46 的 D7 因此改用「讀 vllm-omni 原始碼」代替規格指定的實機量測，而部署跑的是另一個版本，結論只能附帶 caveat 交付。**要在容器內量測時，用它。**

```
ssh qwen-gpu 'docker exec -i vibe-vox-bff-1 python -' <<'PY'
...
PY
```

這條路徑能到模型服務（`http://tts:8000`、`http://vllm:8000`），nginx 8088 到不了。腳本經 stdin 送進容器的 python，中文用不用 escape 都可以，但**別讓腳本經過本機 PowerShell 的雙引號**（`$` 會被吃掉）。

機器有兩張卡。**GPU 1 被非本專案的 gpustack 工作負載動態佔用，其餘裕不可假設穩定**，故 GPU 服務全釘在 `device_ids: ["0"]`。

---

## 2. 部署

```
git pull && docker compose up -d --build bff frontend
```

改了 GPU 服務的設定時要帶 profile 與強制重建：

```
docker compose --profile tts up -d --force-recreate tts
```

**兩件事都不會報錯，只會讓你以為改了：**

- `docker compose up -d tts` 不帶 `--profile tts` 時 compose 根本不認得 tts 服務。
- 改了 `command` 或 `environment` 而不帶 `--force-recreate`，容器照舊跑舊設定。

部署前以 export 端點備份 Hotword（備份檔的位置見 `.gitignore`）。

---

## 3. 部署的 image 落後於 repo，沒有任何機制會發現

**這是這份文件最重要的一條。** 曾經的症狀：`POST /api/admin/voices/clone` 回 `{"detail":"Not Found"}`，而本機同一路徑回 201。原因是遠端 bff 容器的 image build 在音色 CRUD 實作之前。

**沒有任何東西會提示這件事：**

- 容器「Up 30 minutes」講的是**啟動時間**，不是 image 的建置時間。
- `/api/health` 照回 200，因為它探測的是模型服務，不是 BFF 自己的版本。

診斷起手式，除了 `curl -sS http://localhost:8088/api/health` 之外加這一行，它列出 BFF 實際認得的路徑：

```
docker exec vibe-vox-bff-1 python -c 'import urllib.request,json;print("\n".join(sorted(json.load(urllib.request.urlopen("http://127.0.0.1:8000/openapi.json"))["paths"])))'
```

**`/openapi.json` 不在 `/api/` 底下，經 nginx 8088 拿到的是 `index.html` 不是 JSON**，必須從容器內部打。

---

## 4. 診斷 HTTP 時的兩個陷阱

**`curl -s` 會把連線層的錯誤一起吞掉。** 曾經浪費一輪診斷：請求「完全沒反應」看起來像新症狀，其實是 `-s` 靜默了 curl 自己的錯誤。**一律用 `-sS` 並帶 `-w '\nHTTP %{http_code}\n'`。**

**兩處必須同時改的設定。** `VIBE_VOX_TTS_SERVED_NAME` 的同一個值寫在 compose 的 bff 與 tts 兩處（tts 的 `--served-model-name`、bff 的環境變數）。單邊改的症狀是每次合成在 0.03 秒內回 502。該不變量由 `bff/tests/test_config.py` 實際讀 `docker-compose.yml` 守著。

---

## 5. 比較兩筆 VRAM 讀數之前，先確認條件相同

曾經拿相隔近兩小時的兩筆讀數比較，推論出「utilization 設定沒生效」並據此質疑操作者的正確陳述。實情是那兩筆來自**不同的容器**（`docker ps` 的 `CreatedAt` 差 51 分鐘），且一筆跑過 ASR 長音檔、一筆沒有——差的 9 GB 是 PyTorch 配置後不歸還的推論快取。

**量測要記「跑過什麼」而不只是「幾點量的」。** 比較之前先跑：

```
docker ps --format '{{.Names}}\t{{.CreatedAt}}'
```

**`ASR 跑過一次長音檔會讓 vLLM 佔用長高約 9 GB 且不歸還**，所以「GPU 0 還剩多少」在跑過辨識之後是假象。

VRAM 的實測數字記在 #31，不記在本檔——數字會變，而本檔講的是方法。

---

## 6. 執行期依賴的環境事實

- **BFF image 裝 ffmpeg**（`bff/Dockerfile`），它同時提供 ffprobe。兩者都是子進程依賴，`uv sync` 不會發現它們不見了，故由 `bff/tests/test_config.py` 實際讀 Dockerfile 斷言。ffprobe 是硬依賴：參考音的時長驗證只有 wav 走 stdlib，其餘容器全靠它。
- **本機（Windows）無 ffmpeg**，故 ffmpeg-gated 測試在本機 skip、在 CI 跑。
- **`bff/.venv` 是 Python 3.14**，而 image 是 `python:3.13-slim`。選相依時要查它的 wheel 覆蓋範圍：`wetext` 就是因為相依 `kaldifst`（只發到 cp312）而在兩邊都裝不起來。
