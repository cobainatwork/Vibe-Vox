# VoxCPM2 服務：官方 vLLM-Omni image + 權重 build 時 bake 進 image（開箱即用）。
#
# **版本與 ASR 側刻意不同。** vllm.Dockerfile 釘死 vLLM v0.14.1 是 VibeVoice 對
# transformers v4 的硬需求，而 VoxCPM2 走的是另一個容器、另一套依賴樹，兩者沒有共用
# runtime 的理由——這正是 ADR-0001「解耦模型服務」要換到的東西。
#
# v0.24.0 是 Docker Hub 上實際存在的最新版本 tag（2026-07-07）。官方安裝文件的範例寫
# v0.26.0，但該 tag 尚未發布，文件領先了 registry。**不要用浮動 tag**：vllm-omni 移動
# 很快（傳輸 findings §4 記載 pushed_at 與撰寫日同天）。
FROM vllm/vllm-omni:v0.24.0

# HF 權重快取位置；build 時下載至此、隨 image 打包，runtime 命中快取不再下載。
ENV HF_HOME=/models

# recipe 的 Prerequisites。**在 build 時裝而非 runtime**：#41 記載上游腳本在啟動時跑
# pip install 的後果——執行期依賴網路、image 不可重現、啟動變慢。
# ninja 必須在 PATH 上，flashinfer 的 JIT 編譯要用它。
RUN pip install --no-cache-dir voxcpm soundfile ninja

# 模型權重 build 時下載進 image（開箱即用、runtime 零下載），與 vllm.Dockerfile 一致。
ARG HF_TOKEN=""
ENV HF_TOKEN=${HF_TOKEN}
RUN python3 -c "from huggingface_hub import snapshot_download; snapshot_download('openbmb/VoxCPM2')"

# 啟動參數在 docker-compose.yml 的 command，理由同 vllm 服務：寫在 image 裡的值連
# grep 都找不到。
ENTRYPOINT []
