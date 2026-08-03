# VibeVoice-ASR 服務：官方 vllm_plugin + 權重 build 時 bake 進 image（開箱即用）。
# 依官方 docs/vibevoice-vllm-asr.md：務必用 vLLM v0.14.1（支援 transformers v4；
# VibeVoice 要 transformers>=4.51.3,<5.0.0，與 vLLM latest 的 v5 需求互斥）。
FROM vllm/vllm-openai:v0.14.1

# HF 權重快取位置；build 時下載至此、隨 image 打包，runtime 命中快取不再下載。
ENV HF_HOME=/models
# 官方建議的執行期環境變數。
ENV VIBEVOICE_FFMPEG_MAX_CONCURRENCY=64
ENV PYTORCH_ALLOC_CONF=expandable_segments:True

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ffmpeg libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# 官方 VibeVoice + vLLM plugin（entry point vllm.general_plugins:vibevoice）。
# pyproject 無 [vllm] extra，安裝 base 即註冊 plugin。
RUN git clone --depth 1 https://github.com/microsoft/VibeVoice.git /app
WORKDIR /app
RUN pip install --no-cache-dir -e /app

# 模型權重 build 時下載進 image（開箱即用、runtime 零下載）。
# 官方 vLLM plugin 用 microsoft/VibeVoice-ASR（非 -HF；-HF 是 transformers v5.3.0+ 直接用的格式，
# vLLM plugin 的 transformers v4 不認得）。若為 gated model，build 時帶 --build-arg HF_TOKEN=<token>。
ARG HF_TOKEN=""
ENV HF_TOKEN=${HF_TOKEN}
RUN python3 -c "from huggingface_hub import snapshot_download; snapshot_download('microsoft/VibeVoice-ASR')"

# 官方啟動腳本：生成 tokenizer files 並以官方參數 vllm serve
# （--served-model-name vibevoice --trust-remote-code --chat-template-content-format openai 等）；
# 模型已在快取，不再下載。
ENTRYPOINT []
CMD ["python3", "/app/vllm_plugin/scripts/start_server.py"]
