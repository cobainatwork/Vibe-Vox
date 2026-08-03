# VibeVoice-ASR 服務：官方 vllm_plugin + 權重 build 時 bake 進 image（開箱即用）。
# 依 microsoft/VibeVoice 的 vllm_plugin/scripts/start_server.py 流程。
FROM vllm/vllm-openai:latest

# HF 權重快取位置；build 時下載至此、隨 image 打包，runtime 不再下載。
ENV HF_HOME=/models

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ffmpeg libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# 官方 VibeVoice + vLLM plugin。
RUN git clone --depth 1 https://github.com/microsoft/VibeVoice.git /app
RUN pip install --no-cache-dir -e "/app[vllm]"

# 模型權重 build 時下載進 image（開箱即用，runtime 零下載、零設定）。
# 若 microsoft/VibeVoice-ASR-HF 為 gated model，build 時帶 --build-arg HF_TOKEN=<你的 token>。
ARG HF_TOKEN=""
ENV HF_TOKEN=${HF_TOKEN}
RUN python3 -c "from huggingface_hub import snapshot_download; snapshot_download('microsoft/VibeVoice-ASR-HF')"

# 官方啟動腳本：處理 tokenizer 生成與 vllm serve（--served-model-name vibevoice
# --trust-remote-code --chat-template-content-format openai 等）；模型已在快取，不再下載。
ENTRYPOINT []
CMD ["python3", "/app/vllm_plugin/scripts/start_server.py", "--model", "microsoft/VibeVoice-ASR-HF", "--port", "8000"]
