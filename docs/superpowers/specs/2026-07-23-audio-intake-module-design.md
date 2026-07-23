# 設計：音檔輸入共用模組（#4 prefactor）

日期：2026-07-23
關聯：GitHub issue #4；blocked by #1；blocking #5（ASR）、#7（Voice clone）
規格來源：`docs/spec.md`「音檔處理」「安全與資源邊界」「持久化」；ADR-0001。

## 1. 範圍與目標

一個安全的音檔上傳與轉碼共用管線，供 #5 與 #7 復用。本票不面向使用者，不新增消費端或管理平面端點，以模組測試驗證行為。核心產物為 FFmpeg 轉碼包裝與不可信輸入的驗證邊界。

明確不做（延後至 #5／#7）：真實 HTTP 端點、`AsrClient.transcribe`／`TtsClient.synthesize` 接線、HTTP 狀態碼映射的 exception handler 註冊、VibeVoice-ASR 實際取樣率定值。

## 2. 模組邊界與介面

新增套件 `bff/src/vibe_qwen/audio/`，拆為單一職責、可獨立測試的單元：

### 2.1 `sniff.py` — 型別判定（純函式，零依賴）

- `detect_audio_format(header: bytes) -> str | None`
- 以 magic numbers 比對允許容器：wav（`RIFF....WAVE`）、mp3（`ID3` 或 frame sync `0xFFEx`）、flac（`fLaC`）、ogg（`OggS`）、m4a（`....ftyp`）、webm（EBML `0x1A45DFA3`）。
- 不符回 `None`；呼叫端決定拋錯。不採 `python-magic`（Windows 需系統 libmagic，onboarding 摩擦高）；手寫 sniffer 零依賴且對安全敏感路徑可審計。
- 不信副檔名：判定僅依標頭位元組。

### 2.2 `intake.py` — 串流落地 + 驗證 + 共用 facade

- `async def save_upload(chunks: AsyncIterator[bytes], *, temp_dir: Path, max_bytes: int) -> Path`
  - 產生伺服器 UUID 檔名，逐塊寫入 `temp_dir`，不整檔載入記憶體。
  - 首塊先做 `detect_audio_format`；回 `None` 即 raise `UnsupportedAudioFormat`（在寫入其餘內容前，fail fast）。
  - 累計位元組超過 `max_bytes` 即 raise `FileTooLarge`；已寫入的部分檔清除。
- facade：`async def transcoded(chunks, *, sample_rate, channels=1) -> AsyncContextManager[Path]`
  - 內部串流落地 → 轉碼 → yield 輸出 wav 路徑；離開 context 時原始暫存檔與輸出 wav 皆刪除（對應「暫存檔用畢即清」）。
  - 逾時或 HTTP 連線中斷（cancel 傳播為 `CancelledError`）時，仍於 `finally` 清檔並殺子進程。
  - 此即 #5／#7 的唯一共用進入點。

### 2.3 `transcode.py` — FFmpeg 子進程包裝

- `async def transcode_to_wav(src: Path, dst_dir: Path, *, sample_rate: int, channels: int = 1, timeout_s: float, ffmpeg: str = "ffmpeg") -> Path`
- 以 `asyncio.create_subprocess_exec` 呼叫，參數一律 list，絕不經 shell、絕不字串拼接。
- 固定參數：`-nostdin -hide_banner -protocol_whitelist file -i <src> -ar <rate> -ac <channels> -f wav -acodec pcm_s16le <dst>`。
  - `-protocol_whitelist file` 阻斷 concat／http 等協定，防 SSRF 與本機檔案讀取。
  - `src`／`dst` 皆為伺服器 UUID 路徑；使用者提供字串（原始檔名等）永不進參數或路徑。
- 逾時以 `asyncio.timeout` 包住 `proc.communicate()`；逾時或 cancel 時於 `finally` 呼叫 `proc.kill()`（Linux 為 SIGKILL、Windows 為 TerminateProcess）並 `await proc.wait()` 回收，raise `TranscodeTimeout`。
- 非零退出碼 raise `TranscodeError`（涵蓋無法解碼的檔案，映射為 400）。

### 2.4 例外類別

置於 `audio/errors.py`：`FileTooLarge`、`UnsupportedAudioFormat`、`TranscodeError`、`TranscodeTimeout`。#4 僅定義並於模組測試斷言；HTTP 狀態碼映射（超限 413、偽造/非音訊 400、無法解碼 400/422）待 #5 接真實端點時於 `main.py` 註冊，避免現在掛上無端點觸發的死 handler。

## 3. 設定（`config.py` 新增）

- `VIBE_QWEN_AUDIO_MAX_BYTES`：單檔上限（bytes），超過回 413 語意。預設 `26214400`（25 MiB，對齊常見 ASR 上傳量級的參考值），可經環境變數覆寫。
- `VIBE_QWEN_ASR_SAMPLE_RATE`：ASR 目標取樣率預設。此值正確性於 #5 接 VibeVoice-ASR 時確認；`transcode_to_wav` 的 `sample_rate` 為必填參數，模組本身不硬編。
- `VIBE_QWEN_FFMPEG_TIMEOUT_SECONDS`：轉碼子進程逾時。
- 沿用既有 `temp_dir`／`temp_max_age_seconds`。

## 4. 資料流

上傳串流（chunks）→ `save_upload`：首塊 sniff（不符→400）、逐塊寫 UUID 暫存檔（超限→413）→ `transcode_to_wav`：ffmpeg 轉為 wav（pcm_s16le/目標率/mono，失敗→400、逾時→504 語意）→ facade yield wav 路徑供呼叫端使用 → context 離開清除兩個暫存檔。

## 5. 測試策略（模組直測）

依 spec 第 176 行（轉檔器以小型樣本檔直測）授權，本票不建投機 HTTP 端點；#5 接真實 `/api/asr/transcribe` 時於 HTTP seam 驗證 413/400 映射。

不需 ffmpeg（處處可跑）：
- sniff：各允許格式標頭回正確型別；偽造/非音訊標頭回 `None`。
- `save_upload`：偽造 magic number → `UnsupportedAudioFormat`；超限的分塊 async iterator → `FileTooLarge`，且於超過上限即停止、不繼續耗盡輸入串流（斷言 iterator 未被完全消費，佐證逐塊串流而非整檔載入）；落地檔名為 UUID、不含使用者輸入。

需 ffmpeg（`@pytest.mark.skipif(shutil.which("ffmpeg") is None)`）：
- 轉碼取樣率正確：以 ffmpeg 生成樣本（`-f lavfi -i sine`）→ 轉為目標率 → 解析輸出 WAV 標頭斷言取樣率（零依賴，不需 ffprobe）。
- 逾時 kill：對會掛起的轉碼設極短 timeout → 斷言 raise `TranscodeTimeout` 且子進程已終止、無孤兒程序。
- facade context 離開後暫存檔與輸出檔皆已刪除。

## 6. CI 與部署變更

- `.github/workflows/ci.yml`：BFF job 新增 ffmpeg 安裝步驟（`apt-get install -y ffmpeg` 或 setup-ffmpeg action），使需 ffmpeg 的測試在 CI 實際執行而非全數 skip。
- `bff/Dockerfile`：安裝 ffmpeg（prod 執行期依賴）。
- 本機（Windows，未裝 ffmpeg）：需 ffmpeg 的測試 skip；其餘照跑。開發者若需在本機跑完整轉碼測試，自行安裝 ffmpeg。

## 7. 對後續票的介面承諾

#5／#7 只需：`async with audio_intake.transcoded(request.stream(), sample_rate=<rate>) as wav_path: ...`，取得已正規化的 wav 路徑，用畢自動清檔。驗證與資源清理封裝於模組內，呼叫端不重複處理。
