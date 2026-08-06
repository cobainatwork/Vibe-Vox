import { afterEach, describe, expect, it, vi } from "vitest";

import { createCloneVoice, deleteVoice, listVoices, renameVoice } from "./voices";

function mockFetch(data: unknown, ok = true, status = 200) {
  return vi.fn().mockResolvedValue({ ok, status, json: async () => ({ data }) });
}

const VOICE = {
  id: "v1",
  name: "客戶-中年男性",
  type: "clone" as const,
  language: "zh-TW",
  ref_audio_path: "/data/voices/abc",
  ref_text: null,
  instruct: null,
  created_at: "t",
  updated_at: "t",
};

describe("voices API client", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("listVoices 解析 {data} 信封為陣列", async () => {
    vi.stubGlobal("fetch", mockFetch([VOICE]));

    await expect(listVoices()).resolves.toEqual([VOICE]);
  });

  it("createCloneVoice 以 multipart 送出，欄位名對齊後端", async () => {
    const fetchMock = mockFetch(VOICE);
    vi.stubGlobal("fetch", fetchMock);
    const file = new File([new Uint8Array([1, 2, 3])], "ref.wav", { type: "audio/wav" });

    await createCloneVoice("客戶-中年男性", "zh-TW", file);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/admin/voices/clone");
    expect(init.method).toBe("POST");
    const form = init.body as FormData;
    expect(form.get("name")).toBe("客戶-中年男性");
    expect(form.get("language")).toBe("zh-TW");
    expect(form.get("ref_audio")).toBe(file);
  });

  it("createCloneVoice 未給逐字稿時不送 ref_text 欄位", async () => {
    const fetchMock = mockFetch(VOICE);
    vi.stubGlobal("fetch", fetchMock);
    const file = new File([new Uint8Array([1])], "ref.wav", { type: "audio/wav" });

    await createCloneVoice("甲", "zh-TW", file);

    const form = (fetchMock.mock.calls[0][1].body as FormData);
    expect(form.has("ref_text")).toBe(false);
  });

  it("renameVoice 送 JSON 以觸發 CORS 預檢", async () => {
    const fetchMock = mockFetch({ ...VOICE, name: "新名字" });
    vi.stubGlobal("fetch", fetchMock);

    await renameVoice("v1", "新名字");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/admin/voices/v1");
    expect(init.method).toBe("PUT");
    expect(init.headers).toEqual({ "Content-Type": "application/json" });
    expect(JSON.parse(init.body)).toEqual({ name: "新名字" });
  });

  it("後端錯誤時採用 {error:{message}} 的訊息", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 409,
        json: async () => ({ error: { code: "VOICE_NAME_TAKEN", message: "音色名稱「甲」已存在" } }),
      }),
    );

    await expect(renameVoice("v1", "甲")).rejects.toThrow("音色名稱「甲」已存在");
  });

  it("deleteVoice 對 404 拋出後端訊息", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 404,
        json: async () => ({ error: { code: "VOICE_NOT_FOUND", message: "音色不存在或已被刪除。" } }),
      }),
    );

    await expect(deleteVoice("nope")).rejects.toThrow("音色不存在或已被刪除。");
  });
});
