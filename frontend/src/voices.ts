// 管理平面音色 API client 與型別（/api/admin/voices，{data} 信封）。
// 狀態變更一律送 application/json 或 multipart 以觸發 CORS 預檢（spec 安全邊界）。

export type VoiceType = "clone" | "design";

export type Voice = {
  id: string;
  name: string;
  type: VoiceType;
  language: string;
  ref_audio_path: string;
  ref_text: string | null;
  instruct: string | null;
  created_at: string;
  updated_at: string;
};

const JSON_HEADERS = { "Content-Type": "application/json" };

async function errorMessage(resp: Response, fallback: string): Promise<string> {
  const body = await resp.json().catch(() => null);
  return body?.error?.message ?? `${fallback}：HTTP ${resp.status}`;
}

async function unwrap<T>(resp: Response): Promise<T> {
  if (!resp.ok) {
    throw new Error(await errorMessage(resp, "音色請求失敗"));
  }
  const body = (await resp.json()) as { data: T };
  return body.data;
}

export async function listVoices(): Promise<Voice[]> {
  return unwrap<Voice[]>(await fetch("/api/admin/voices"));
}

export async function createCloneVoice(
  name: string,
  language: string,
  refAudio: File,
  refText?: string,
): Promise<Voice> {
  const form = new FormData();
  form.append("name", name);
  form.append("language", language);
  form.append("ref_audio", refAudio);
  // ref_text 為管理用 metadata，不進合成路徑（docs/api/tts.md §5.2）。
  if (refText) form.append("ref_text", refText);
  return unwrap<Voice>(await fetch("/api/admin/voices/clone", { method: "POST", body: form }));
}

export async function renameVoice(id: string, name: string): Promise<Voice> {
  const resp = await fetch(`/api/admin/voices/${id}`, {
    method: "PUT",
    headers: JSON_HEADERS,
    body: JSON.stringify({ name }),
  });
  return unwrap<Voice>(resp);
}

export async function deleteVoice(id: string): Promise<void> {
  const resp = await fetch(`/api/admin/voices/${id}`, { method: "DELETE" });
  if (!resp.ok) {
    throw new Error(await errorMessage(resp, "音色刪除失敗"));
  }
}
