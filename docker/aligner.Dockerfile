# 字級強制對齊服務（ADR-0004 的第四部署單元）：Qwen3-ForcedAligner + 薄 HTTP 層。
#
# 與 vllm 容器完全隔離——不共用 image、不動 docker/vllm.Dockerfile。該 image pin
# vLLM v0.14.1、bake 7B 權重、附官方 plugin，是系統最脆弱且重建最慢的部分，而官方
# forced aligner benchmark 本身即以 transformers 執行，併入無收益。
#
# base image 提供 torch 與 CUDA，省去自行對齊 wheel 與 driver 版本。CUDA 12.8 與
# vllm 容器同代（vLLM v0.14.1 亦為 12.8），故宿主 driver 相容性已由既有部署實證；
# torch 2.11 是 12.8 系列的最後一版（2.12 起只出 12.6 與 13.x）。取 runtime 而非
# devel：不編譯 flash-attn。
FROM pytorch/pytorch:2.11.0-cuda12.8-cudnn9-runtime

# HF 權重快取位置（與 vllm image 同慣例）；build 時下載至此、隨 image 打包。
ENV HF_HOME=/models
ENV PYTORCH_ALLOC_CONF=expandable_segments:True

# 系統音訊依賴，對齊官方 docker/Dockerfile-qwen3-asr-cu128：libsndfile 供 soundfile，
# ffmpeg 供 librosa 的後備解碼路徑。
# python3.12-venv：Debian 把 venv 的 ensurepip 拆成獨立套件，base image 未裝，
# 缺它 `python -m venv` 會失敗。版本號可硬編是因為 base image tag 已釘死，
# 其 Python 版本隨之固定；換 base image 時需同步改此處。
RUN apt-get update \
    && apt-get install -y --no-install-recommends libsndfile1 ffmpeg python3.12-venv \
    && rm -rf /var/lib/apt/lists/*

# base image 的 Python 是 Debian 系統 Python，帶 PEP 668 的 EXTERNALLY-MANAGED
# 標記，pip 不得直接寫入（舊版 pytorch image 走 conda，無此限制）。改建 venv：
# --system-site-packages 讓它沿用 image 內既有的 torch，不必重裝 2 GB 級的 CUDA
# wheel；且在 venv 內安裝會遮蔽系統套件而非嘗試移除，順帶避開官方 Dockerfile
# 用 `apt remove python3-blinker` 處理的那個 flask 依賴衝突。
# venv 上 PATH 的做法與 bff/Dockerfile 一致。
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN python -m venv --system-site-packages "$VIRTUAL_ENV"

# 官方 qwen-asr 套件。版本釘死以保 build 可重現——它自身亦釘死 transformers==4.57.6。
# 不裝 [vllm] extra：本服務走 transformers backend（ADR-0004），且 qwen-asr-serve
# 只是 vLLM serve 的包裝，不提供對齊端點。
RUN pip install --no-cache-dir qwen-asr==0.0.6

# build 時就確認 venv 看得到 base image 的 torch 且 CUDA 版本如預期。
# 這條若失敗，代表 --system-site-packages 沒生效——在此中斷遠優於 runtime 才發現。
RUN python -c "import torch, qwen_asr; print('torch', torch.__version__, 'cuda', torch.version.cuda)"

# 權重 build 時下載進 image（開箱即用、runtime 零下載），與 vllm image 同做法。
# 選非 -hf 變體：-hf 走 transformers 的 AutoModelForTokenClassification，但在官方
# Transformers release 納入前需 `pip install git+https://github.com/huggingface/transformers`，
# 版本釘不住、build 不可重現（VibeVoice 的 -HF 變體亦曾在此類問題上踩坑）。
# qwen-asr 這條是官方 model card 的首選範例，且支援 batch 與 (np.ndarray, sr) 輸入。
# 不設 HF_TOKEN（vllm image 需要它是因為 VibeVoice-ASR 可能為 gated model）：
# 本模型為公開的 Apache-2.0，無需認證，且 ENV 會把 token 烙進 image 層。
RUN python -c "from huggingface_hub import snapshot_download; snapshot_download('Qwen/Qwen3-ForcedAligner-0.6B')"

# 本服務以 pip 而非 uv 安裝：torch 由 base image 提供，用 uv sync 依 lock 重建環境
# 會與它衝突。lock 檔只鎖 HTTP 層的輕依賴，供 CI 用（見 .github/workflows/ci.yml）。
WORKDIR /app
COPY aligner/pyproject.toml ./pyproject.toml
COPY aligner/src ./src
RUN pip install --no-cache-dir .

# 不切非 root：與 vllm 容器一致，且 HF_HOME 下的權重層若 chown 會整份複製一次。
# 代價要講清楚：本服務以 libsndfile 解碼外部來源的音訊（使用者上傳、經 BFF 轉碼），
# 而 libsndfile 有緩衝區溢位的 CVE 史，以 root 執行等於把該類漏洞的後果放大到 root。
# 目前的緩解是該容器不對外（僅 compose 內部網路供 BFF 呼叫）且輸入已先經 ffmpeg
# 正規化。若要收斂此風險，做法是建非 root 使用者並以其身分執行 snapshot_download
# （避免 chown 整層權重），需重新 build 驗證。

EXPOSE 9100
# start-period 需涵蓋權重載入至 GPU 的時間；未就緒時 /health 回 503，urlopen 拋錯
# 即判定 unhealthy。
HEALTHCHECK --interval=30s --timeout=5s --start-period=300s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:9100/health').status==200 else 1)"

# 單 worker：多 worker 會各載入一份權重。prod 要併發改以加 replica（ADR-0004）。
CMD ["uvicorn", "vibe_vox_aligner.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "9100"]
