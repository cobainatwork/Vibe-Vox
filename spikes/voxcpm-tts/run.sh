#!/usr/bin/env bash
# VoxCPM2 spike via Docker — 遠端機只需 docker + nvidia-container-toolkit，無 host pip。
# 用法：./run.sh <台灣參考音檔> ["參考音逐字稿"]
#   參考音檔請放在本目錄（spikes/voxcpm-tts/）下。
set -euo pipefail
cd "$(dirname "$0")"

REF="${1:?用法: ./run.sh <台灣參考音.wav> [參考音逐字稿]}"
REFTEXT="${2:-}"
[ -f "$REF" ] || { echo "找不到參考音：$REF（請放在 spikes/voxcpm-tts/ 下）"; exit 1; }

echo "==> build image"
docker build -t voxcpm-spike:latest .

echo "==> GPU 煙霧測試（必須印 cuda.is_available = True，否則結果不可信）"
docker run --rm --gpus all --entrypoint python voxcpm-spike:latest \
  -c "import torch; print('cuda.is_available =', torch.cuda.is_available())"

echo "==> run spike"
ARGS=(--ref "/work/$(basename "$REF")")
[ -n "$REFTEXT" ] && ARGS+=(--ref-text "$REFTEXT")
docker run --rm --gpus all \
  -v "$(pwd)":/work \
  -v voxcpm-hf-cache:/hf-cache \
  voxcpm-spike:latest "${ARGS[@]}"

echo "==> 完成，輸出在 $(pwd)/out/（含 24kHz 健檢檔）"
