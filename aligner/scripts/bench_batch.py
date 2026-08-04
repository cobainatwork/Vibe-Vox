"""送 N 段對齊請求，供 bench_vram.sh 量測各 batch 級數的 VRAM 峰值。

段長與文字量刻意貼近 VibeVoice 實際切出的段落（約 34 秒、104 字）：用官方那個
4.2 秒的測試音訊直接量會嚴重低估，因為 activation 隨音訊長度成長。

用法：python bench_batch.py <段數>
"""

import io
import json
import os
import sys
import time
import urllib.request

import numpy as np
import requests
import soundfile as sf

ENDPOINT = "http://127.0.0.1:9100/align"
AUDIO_CACHE = "/tmp/asr_zh.wav"
AUDIO_URL = "https://qianwen-res.oss-cn-beijing.aliyuncs.com/Qwen3-ASR-Repo/asr_zh.wav"
UNIT_TEXT = "甚至出現交易幾乎停滯的情況"  # 13 字，對應該音訊的 4.204 秒
REPEAT = 8  # 4.204 x 8 = 33.6 秒、104 字，落在 VibeVoice 的 30-40 秒段長內


def _prepare_segment() -> tuple[bytes, str, float]:
    """合成一個貼近實際段長的段落，回 (wav bytes, 對應文字, 秒數)。"""
    if not os.path.exists(AUDIO_CACHE):
        with urllib.request.urlopen(AUDIO_URL, timeout=60) as resp:
            open(AUDIO_CACHE, "wb").write(resp.read())

    waveform, sample_rate = sf.read(AUDIO_CACHE, dtype="float32", always_2d=False)
    buffer = io.BytesIO()
    sf.write(buffer, np.tile(waveform, REPEAT), sample_rate, format="WAV")
    return buffer.getvalue(), UNIT_TEXT * REPEAT, len(waveform) * REPEAT / sample_rate


def main() -> int:
    batch_size = int(sys.argv[1])
    payload, text, seconds = _prepare_segment()

    # 兩格縮排是刻意的：本腳本的輸出嵌在 bench_vram.sh 的級數標題之下。
    print(
        f"  segment {seconds:.1f}s / {len(text)} chars / "
        f"{len(payload) / 2**20:.2f} MiB, batch={batch_size}"
    )

    started = time.time()
    resp = requests.post(
        ENDPOINT,
        data={"items": json.dumps([{"text": text}] * batch_size, ensure_ascii=False)},
        files=[("audio", (f"seg{i}.wav", payload, "audio/wav")) for i in range(batch_size)],
        timeout=1800,
    )
    print(f"  HTTP {resp.status_code} in {time.time() - started:.1f}s")

    if resp.status_code != 200:
        print("  " + resp.text[:300])
        return 1

    items = resp.json()["items"]
    print(f"  items={len(items)}, words={[len(i['words']) for i in items[:3]]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
