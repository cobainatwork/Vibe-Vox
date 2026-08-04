#!/usr/bin/env bash
# 在 GPU 宿主上執行，量測 aligner 各 batch 級數的 VRAM 峰值。
#
# **本腳本產生的數字曾被用來定 batch 上限 32，而那個上限在真機上 CUDA OOM**（2026-08-05，
# #36）。原因在 bench_batch.py：它把**同一段音訊重複 N 次**。批次張量會 pad 到該批最長
# 的段落，均勻輸入的 padding 浪費恰好 1.00 倍，真實錄音接近 2 倍，故本腳本會系統性低估
# 真實負載。用它重測前請先讓 bench_batch.py 吃真實錄音的段長分布，否則會再得到同樣誤導
# 的數字。詳見 aligner/README.md 的「上限已由 32 降為 8」。
#
# 為何在宿主而非容器內跑：nvidia-smi 的 used_memory 是 per-process，要量的是
# uvicorn 那個行程。docker exec 開的新 process 讀 torch.cuda.max_memory_allocated()
# 只會讀到自己（永遠是 0），這是量測 GPU 服務時最容易踩的坑。
#
# 級數由小而大，任一級失敗即停——那一級極可能是 CUDA OOM，而該卡由 vllm 與 tts
# 共用，繼續往上加只會波及它們。量測期間請勿讓 ASR 有流量。
#
# 用法：./bench_vram.sh [容器名] [級數]
#   ./bench_vram.sh
#   ./bench_vram.sh vibe-vox-aligner-1 "1 4 16 64"
# 刻意不用 set -e：某一級失敗是預期的結果（那就是答案），需要繼續執行到印出
# 「stopping」與最終的 GPU 狀態，而非在該處直接中止。失敗由迴圈內的 if 處理。
set -uo pipefail

CONTAINER="${1:-vibe-vox-aligner-1}"
LEVELS="${2:-1 2 4 8 16 32}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

docker cp "$SCRIPT_DIR/bench_batch.py" "$CONTAINER:/tmp/bench_batch.py" >/dev/null

PID="$(docker inspect -f '{{.State.Pid}}' "$CONTAINER")"

read_vram() {
  nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader \
    | awk -F', ' -v p="$PID" '$1==p {print $2}'
}

echo "container $CONTAINER, host PID $PID, idle $(read_vram)"

# $LEVELS 刻意不加引號：要的正是空白分詞，把 "1 2 4" 展開成三個級數。
# shellcheck disable=SC2086
for n in $LEVELS; do
  echo "=== batch $n ==="
  if docker exec "$CONTAINER" python /tmp/bench_batch.py "$n"; then
    echo ">>> VRAM after $n: $(read_vram)"
  else
    echo ">>> batch $n FAILED (very likely CUDA OOM), stopping"
    break
  fi
done

echo "=== all GPU processes ==="
nvidia-smi --query-compute-apps=pid,gpu_bus_id,used_memory --format=csv

cat <<'NOTE'

PyTorch 的 caching allocator 不會把 VRAM 還回去，故最後那個數字即為峰值。
要把它釋放給同卡的其他服務，需 docker compose restart aligner。
NOTE
