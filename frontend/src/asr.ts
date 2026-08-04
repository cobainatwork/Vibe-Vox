// ASR 消費端 client：POST /api/asr/transcribe。
// 與管理平面不同，此端點回應直接是結果物件，不套 {data} 信封（ADR-0003）。

// Forced alignment 的最小單位：中文為單一漢字，不是詞。標點不產生 Word，故一段的
// 數量不等於其 Content 字元數（CONTEXT.md 的 Word 詞條）。
export type AsrWord = {
  Text: string;
  Start: number;
  End: number;
};

export type AsrSegment = {
  // 語義隨 aligned 改變：true 時為首字與末字的實際發音邊界，false 時退回模型自選的
  // 切點（docs/api/asr.md §4.3）。混用會得到錯誤結果。
  Start: number;
  End: number;
  Speaker: string;
  Content: string;
  // 顯式標記，不可用 words.length === 0 代替判斷——空陣列與「該段沒有字」語義不同。
  aligned: boolean;
  words: AsrWord[];
};

// 供消費端組評分分母的四個數字。管理平面不顯示它們（ADR-0004 的最小驗證範圍），
// 但型別須完整反映契約，且「匯出 JSON」會帶出。
export type AsrAlignment = {
  audio_duration: number;
  speech_start: number | null;
  speech_end: number | null;
  aligned_duration: number;
};

export type AsrResult = {
  segments: AsrSegment[];
  raw_text: string;
  transcription_only: string;
  duration: number;
  applied_context: string;
  alignment: AsrAlignment;
};

export type TranscribeOptions = {
  extraTerms?: string[];
  replaceContext?: boolean;
};

// 音檔長度的建議上限（秒）。由 BFF 的 asr_timeout（300 秒）決定而非模型的 61 分鐘：
// max_tokens = 秒數×10 + 100，實測生成速度約 50 tokens/s，故最壞情況下 300 秒只容得下
// 約 1490 秒。此處取 1200 留餘裕給 base64 編碼、prefill 與傳輸：設在打平點的警示等於
// 不警示（#35）。
export const MAX_AUDIO_SECONDS = 20 * 60;

async function errorMessage(resp: Response, fallback: string): Promise<string> {
  const body = await resp.json().catch(() => null);
  const message = body?.error?.message ?? `${fallback}：HTTP ${resp.status}`;
  if (resp.status !== 504) return message;
  // 逾時的後端訊息不含「音檔太長」這個脈絡，操作者看到「逾時」不會想到要裁切。
  // 而超過上限的音檔正是永遠拿不到成功回應的那些，事後的長度警示救不到它們。
  const limit = Math.round(MAX_AUDIO_SECONDS / 60);
  return `${message}（音檔超過約 ${limit} 分鐘時常見此結果，請裁切後重試）`;
}

export async function transcribe(
  file: File,
  opts: TranscribeOptions = {},
): Promise<AsrResult> {
  const form = new FormData();
  form.append("file", file);
  if (opts.extraTerms) {
    form.append("extra_terms", JSON.stringify(opts.extraTerms));
  }
  if (opts.replaceContext) {
    form.append("replace_context", "true");
  }

  const resp = await fetch("/api/asr/transcribe", { method: "POST", body: form });
  if (!resp.ok) {
    throw new Error(await errorMessage(resp, "辨識失敗"));
  }
  return (await resp.json()) as AsrResult;
}
