#!/usr/bin/env bash
# VoxCPM2 spike via Docker — 遠端機只需 docker + nvidia-container-toolkit，無 host pip。
# 用法：把台灣參考音（.wav）放進本目錄，直接 ./run.sh 即可（自動找）。
#   逐字稿選填：存成與參考音同名的 .txt（taiwan_ref.wav → taiwan_ref.txt），自動抓。
#   多個 .wav 時再明確指定：./run.sh <檔名.wav>
set -euo pipefail
cd "$(dirname "$0")"

# 參考音：有給就用；沒給就自動抓目錄下唯一的 .wav。
REF="${1:-}"
if [ -z "$REF" ]; then
  set -- *.wav
  if [ "$#" -eq 1 ] && [ -f "$1" ]; then
    REF="$1"
  else
    echo "請把單一台灣參考音（.wav）放進 spikes/voxcpm-tts/，或指定：./run.sh <檔名.wav>"
    exit 1
  fi
fi
[ -f "$REF" ] || { echo "找不到參考音：$REF"; exit 1; }

# 逐字稿：自動找同名 .txt（選填；有才跑 Hi-Fi 對照組）。
REFTEXT_ARGS=()
TXT="${REF%.*}.txt"
if [ -f "$TXT" ]; then
  echo "==> 找到逐字稿 $TXT（會跑 Hi-Fi 對照組）"
  REFTEXT_ARGS=(--ref-text-file "/work/$(basename "$TXT")")
else
  echo "==> 沒有 $TXT，略過 Hi-Fi 對照組（僅 Controllable，不影響 #11 判定）"
fi

echo "==> build image"
docker build -t voxcpm-spike:latest .

echo "==> GPU 煙霧測試（必須印 cuda.is_available = True，否則結果不可信）"
docker run --rm --gpus all --entrypoint python voxcpm-spike:latest \
  -c "import torch; print('cuda.is_available =', torch.cuda.is_available())"

echo "==> run spike"
ARGS=(--ref "/work/$(basename "$REF")")
[ "${#REFTEXT_ARGS[@]}" -gt 0 ] && ARGS+=("${REFTEXT_ARGS[@]}")
docker run --rm --gpus all \
  -v "$(pwd)":/work \
  -v voxcpm-hf-cache:/hf-cache \
  voxcpm-spike:latest "${ARGS[@]}"

echo "==> 完成，輸出在 $(pwd)/out/（含 24kHz 健檢檔）"
