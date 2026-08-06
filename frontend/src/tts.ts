// 消費端 TTS API client（/api/tts/*）。**不套 {data} 信封**——那是管理平面
// （/api/admin/*）的形狀，消費端契約由 ADR-0003 凍結為裸物件。
//
// 管理面板走的是同一組端點而非另開一套：測試頁要驗的正是 AI_practise 會拿到的東西，
// 若各走各的，這裡試聽正常而消費端壞掉時沒有人會發現。

export type TtsVoice = {
  id: string;
  name: string;
  type: "clone" | "design";
  language: string;
};

export type SpeechRequest = {
  input: string;
  voice: string;
  instruct?: string;
};

async function errorMessage(resp: Response, fallback: string): Promise<string> {
  // 成功是二進位音訊、錯誤才是 JSON（契約 §6），故只在 !ok 時解析。
  const body = await resp.json().catch(() => null);
  return body?.error?.message ?? `${fallback}：HTTP ${resp.status}`;
}

export async function listTtsVoices(): Promise<TtsVoice[]> {
  const resp = await fetch("/api/tts/voices");
  if (!resp.ok) {
    throw new Error(await errorMessage(resp, "音色清單載入失敗"));
  }
  const body = (await resp.json()) as { voices: TtsVoice[] };
  return body.voices;
}

export async function synthesizeSpeech(req: SpeechRequest): Promise<Blob> {
  const body: SpeechRequest = { input: req.input, voice: req.voice };
  // 空字串不進 body：後端會把它組成「(  )」前綴，而括號原樣進模型的 text token 串。
  if (req.instruct?.trim()) body.instruct = req.instruct;

  const resp = await fetch("/api/tts/speech", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!resp.ok) {
    throw new Error(await errorMessage(resp, "合成失敗"));
  }
  return resp.blob();
}
