import { afterEach, describe, expect, it, vi } from "vitest";

import { transcribe } from "./asr";

function mockFetch(result: unknown, ok = true, status = 200) {
  return vi.fn().mockResolvedValue({ ok, status, json: async () => result });
}

afterEach(() => vi.unstubAllGlobals());

describe("transcribe", () => {
  it("posts multipart file 到 /api/asr/transcribe，回結果（不套 {data} 信封）", async () => {
    const result = {
      segments: [{ Start: 0, End: 1.2, Speaker: "A", Content: "你好" }],
      raw_text: "你好",
      transcription_only: "你好",
      duration: 1.2,
      applied_context: "",
    };
    const fetchMock = mockFetch(result);
    vi.stubGlobal("fetch", fetchMock);
    const file = new File(["x"], "a.wav", { type: "audio/wav" });

    const got = await transcribe(file);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/asr/transcribe");
    expect(init.method).toBe("POST");
    expect(init.body).toBeInstanceOf(FormData);
    expect((init.body as FormData).get("file")).toBe(file);
    expect(got.segments[0].Speaker).toBe("A");
    expect(got.transcription_only).toBe("你好");
  });

  it("提供時附加 extra_terms 與 replace_context", async () => {
    const fetchMock = mockFetch({
      segments: [],
      raw_text: "",
      transcription_only: "",
      duration: 0,
      applied_context: "",
    });
    vi.stubGlobal("fetch", fetchMock);
    const file = new File(["x"], "a.wav");

    await transcribe(file, { extraTerms: ["王小明"], replaceContext: true });

    const [, init] = fetchMock.mock.calls[0];
    const body = init.body as FormData;
    expect(body.get("extra_terms")).toBe(JSON.stringify(["王小明"]));
    expect(body.get("replace_context")).toBe("true");
  });

  it("失敗時以後端 {error:{message}} 拋錯", async () => {
    const fetchMock = mockFetch(
      { error: { code: "UNSUPPORTED_AUDIO_FORMAT", message: "不支援的音檔格式。" } },
      false,
      400,
    );
    vi.stubGlobal("fetch", fetchMock);
    const file = new File(["x"], "a.wav");

    await expect(transcribe(file)).rejects.toThrow("不支援的音檔格式。");
  });
});
