// ASR 消費端 client：POST /api/asr/transcribe。
// 與管理平面不同，此端點回應直接是結果物件，不套 {data} 信封（ADR-0003）。

export type AsrSegment = {
  Start: number;
  End: number;
  Speaker: string;
  Content: string;
};

export type AsrResult = {
  segments: AsrSegment[];
  raw_text: string;
  transcription_only: string;
  duration: number;
  applied_context: string;
};

export type TranscribeOptions = {
  extraTerms?: string[];
  replaceContext?: boolean;
};

async function errorMessage(resp: Response, fallback: string): Promise<string> {
  const body = await resp.json().catch(() => null);
  return body?.error?.message ?? `${fallback}：HTTP ${resp.status}`;
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
