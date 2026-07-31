# VoxCPM2 spike — GPU 容器。遠端機只需 docker + nvidia-container-toolkit，無需 host pip。
# 所有 Python 依賴（voxcpm / torch / torchaudio 等）都在 build 時裝進 image，不碰 host 環境。
# 註：此為一次性 spike 容器（非正式 TTS 服務），刻意從簡；正式服務採用時再做安全強化。
FROM pytorch/pytorch:2.4.1-cuda12.1-cudnn9-runtime

# 系統依賴：libsndfile1（soundfile）、ffmpeg（librosa/torchaudio 解非 wav）、git（部分套件/權重需要）。
RUN apt-get update && apt-get install -y --no-install-recommends \
        libsndfile1 ffmpeg git \
    && rm -rf /var/lib/apt/lists/*

# base image 已含 CUDA 版 torch；只補 voxcpm 與 harness 用的音訊套件。
# ⚠️ 版本敏感：若 voxcpm 硬性 pin 了不同 torch 而把 GPU 版蓋成 CPU 版，見 README「版本排錯」。
# build 期無 GPU，故此處只印版本、不做 cuda.is_available()（那在 run --gpus 時測）。
RUN pip install --no-cache-dir voxcpm soundfile librosa \
    && python -c "import torch, torchaudio; print('torch', torch.__version__, 'torchaudio', torchaudio.__version__)"

# HF 權重快取指向可掛載目錄，避免每次重下約 8GB。
ENV HF_HOME=/hf-cache

WORKDIR /work
COPY spike_voxcpm.py /opt/spike/spike_voxcpm.py
ENTRYPOINT ["python", "/opt/spike/spike_voxcpm.py"]
