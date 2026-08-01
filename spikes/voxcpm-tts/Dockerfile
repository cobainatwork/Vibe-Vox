# VoxCPM2 spike — GPU 容器。遠端機只需 docker + nvidia-container-toolkit，無需 host pip。
# 所有 Python 依賴（voxcpm / torch / torchaudio 等）都在 build 時裝進 image，不碰 host 環境。
# 註：此為一次性 spike 容器（非正式 TTS 服務），刻意從簡；正式服務採用時再做安全強化。
FROM pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime

# 系統依賴：libsndfile1（soundfile）、ffmpeg（librosa/torchaudio 解非 wav）、git（部分套件/權重需要）。
RUN apt-get update && apt-get install -y --no-install-recommends \
        libsndfile1 ffmpeg git \
    && rm -rf /var/lib/apt/lists/*

# base image 的 torch 2.6.0+cu124 已滿足 voxcpm 的 torch>=2.5.0，且 cu124 相容 driver>=12.4
# （目標機 driver 為 CUDA 12.8）。故 voxcpm 安裝不會再升級 torch、不會換成需要更新 driver 的
# CUDA build（原症狀 driver too old / cuda.is_available False 即因舊 base torch<2.5 被升級）。
# build 期無 GPU，只印版本；若此處 +cuda 的數字 > 目標機 driver 的 CUDA，代表 torch 又被升級了。
RUN pip install --no-cache-dir voxcpm soundfile librosa \
    && python -c "import torch, torchaudio; print('torch', torch.__version__, '+cuda', torch.version.cuda, 'torchaudio', torchaudio.__version__)"

# HF 權重快取指向可掛載目錄，避免每次重下約 8GB。
ENV HF_HOME=/hf-cache

WORKDIR /work
COPY spike_voxcpm.py /opt/spike/spike_voxcpm.py
ENTRYPOINT ["python", "/opt/spike/spike_voxcpm.py"]
