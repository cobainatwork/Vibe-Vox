"""VoxCPM2 spike harness — 解決 Task #10，餵養 spike #11（台灣口語化）與 #12（音色相似度）。

在目標 48GB GPU 機上跑。輸出一組 wav + RTF/延遲表，供操作者「聽」判定 #11 / #12。
API 依官方 usage guide：https://voxcpm.readthedocs.io/en/latest/usage_guide.html
評估依據：docs/superpowers/specs/2026-07-24-voxcpm-evaluation.md

⚠️ 需在機上對照官方文件確認的點（評估文件標為「可信度中」，不臆測）：
  1. model.generate() 的回傳型別與原生取樣率（VoxCPM2 應為 48kHz 的 1D numpy 波形；
     0.5B=16k、1.5=44.1k，若你裝的不是 v2 請改 --sr）。
  2. Hi-Fi 模式參數名（本檔用 prompt_wav_path + prompt_text，請對照 usage guide）。

用法：
  python spike_voxcpm.py --ref taiwan_ref.wav --ref-text-file taiwan_ref.txt
  （--ref-text-file 選填，給 Hi-Fi 對照組用；也可用 --ref-text 直接傳字串）
"""

import argparse
import time
from pathlib import Path

# 台灣口語測試句：含「破音字 + 語氣詞 + 中性句」，每句配不同逐句情緒風格標籤，
# 一次驗證 #11（夠不夠台）與 #12（情緒逐句可變 + 音色像不像本人）。
# 台灣/大陸讀音有差的字，以 VoxCPM 音素輸入 {pinyin+聲調} 強制台灣讀音（官方：normalize=False
# 時支援，如 {ni3}{hao3}；本 spike 用預設 normalize=False）。此前無此鎖定時，這些字被念成
# 大陸/錯誤讀音（垃圾、和、企業、星期），ctrl_03 因無此類字而最自然。
# 鎖定：垃圾={le4}{se4}、和(連接詞)={han4}、企業={qi4}{ye4}、星期={xing1}{qi2}。
# 註：正式產品輸入為任意文字，需自動「TW 破音字→拼音」前處理層才能規模化（見 README）。
TAIWAN_SENTENCES = [
    ("(neutral tone)", "麻煩你把這包{le4}{se4}拿去倒一下，謝謝。"),
    ("(cheerful, upbeat tone)", "欸這間店的鹹酥雞真的超好吃啦，你{xing1}{qi2}六一定要來試試看喔！"),
    ("(angry, irritated tone)", "我跟你{han4}他講過多少次了，這種事情不要再犯了齁！"),
    ("(gentle, warm, slower tone)", "沒關係啦，慢慢來就好，我在這邊等你，別緊張。"),
    ("(excited tone)", "欸欸你看那個！這家{qi4}{ye4}也太扯了吧，我整個嚇到！"),
]

NEUTRAL_FOR_HIFI = "麻煩你把這包{le4}{se4}拿去倒一下，謝謝。"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", required=True, help="台灣人聲參考音（5-30s，wav/flac/mp3）")
    ap.add_argument("--ref-text", default=None, help="參考音逐字稿字串（Hi-Fi 對照組，選填）")
    ap.add_argument("--ref-text-file", default=None,
                    help="參考音逐字稿檔（.txt，UTF-8；優先於 --ref-text）")
    ap.add_argument("--out", default="out", help="輸出資料夾")
    ap.add_argument("--model", default="openbmb/VoxCPM2")
    ap.add_argument("--sr", type=int, default=48000, help="模型原生取樣率（VoxCPM2=48000）")
    args = ap.parse_args()

    ref_text = args.ref_text
    if args.ref_text_file:
        # utf-8-sig：容忍 Windows 記事本存的 UTF-8 BOM，避免 ﻿ 混入逐字稿。
        ref_text = Path(args.ref_text_file).read_text(encoding="utf-8-sig").strip()

    import numpy as np
    import soundfile as sf
    from voxcpm import VoxCPM

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    # optimize=False：跳過 torch.compile（官方文件：optimize 需 CUDA + Triton，Triton 執行期
    # 要 C 編譯器編 kernel，而 runtime image 無 gcc）。走 eager 模式，本 spike 重品質不重速度；
    # 代價：RTF 較 compiled 偏慢，正式效能量測需另備含 build 工具的 image 再開 optimize。
    model = VoxCPM.from_pretrained(args.model, load_denoiser=False, optimize=False)

    rows = []

    def run(tag, mode, gen_kwargs, fname):
        t0 = time.perf_counter()
        wav = model.generate(**gen_kwargs)  # ⚠️ 假設回 1D numpy float 波形 @ args.sr
        dt = time.perf_counter() - t0
        wav = np.asarray(wav, dtype="float32").reshape(-1)
        sf.write(str(out / fname), wav, args.sr)
        dur = len(wav) / args.sr
        rtf = dt / dur if dur else float("nan")
        rows.append((tag, mode, f"{dt:.2f}s", f"{dur:.2f}s", f"{rtf:.2f}", fname))

    # === Controllable 模式（#11 台灣口語 + #12 情緒有效性）：逐句不同情緒 ===
    for i, (style, text) in enumerate(TAIWAN_SENTENCES):
        run(
            style, "controllable",
            dict(text=f"{style}{text}", reference_wav_path=args.ref,
                 cfg_value=2.0, inference_timesteps=10, normalize=False),
            f"ctrl_{i:02d}.wav",
        )

    # === Hi-Fi 對照組（#12 音色相似度上限）：同參考音、無風格 ===
    if ref_text:
        run(
            "(hi-fi baseline, no style)", "hifi",
            dict(text=NEUTRAL_FOR_HIFI, prompt_wav_path=args.ref, prompt_text=ref_text,
                 cfg_value=2.0, inference_timesteps=10, normalize=False),
            "hifi_00.wav",
        )
    else:
        print("[note] 無逐字稿，略過 Hi-Fi 對照組；#12 的 Hi-Fi vs Controllable 對比需要它。")

    # === adapter 取樣率健檢：把第一個輸出降採樣成 24kHz/mono/16-bit（對齊 /api/tts/speech）===
    try:
        import librosa
        y, sr = sf.read(str(out / "ctrl_00.wav"))
        if getattr(y, "ndim", 1) > 1:
            y = y.mean(axis=1)
        y24 = librosa.resample(y.astype("float32"), orig_sr=sr, target_sr=24000)
        sf.write(str(out / "ctrl_00_24k_mono_16bit.wav"), y24, 24000, subtype="PCM_16")
        print("[ok] 24kHz/mono/16-bit 降採樣健檢：out/ctrl_00_24k_mono_16bit.wav")
    except Exception as e:
        print(f"[warn] 24kHz 降採樣健檢略過：{e}")

    print("\n風格標籤 | 模式 | 生成時間 | 音長 | RTF | 檔案")
    print("-" * 72)
    for r in rows:
        print(" | ".join(r))
    print("\nRTF<1 = 快於實時（延遲 fog 的數據）。")
    print("逐句聽 out/ctrl_*.wav：#11 判夠不夠台、#12 判情緒有沒有逐句變 + 像不像本人。")
    print("Hi-Fi vs Controllable 音色相似度：hifi_00.wav vs ctrl_00.wav。")


if __name__ == "__main__":
    main()
