import { afterEach, describe, expect, it, vi } from "vitest";

import { transcribe } from "./asr";

function mockFetch(result: unknown, ok = true, status = 200) {
  return vi.fn().mockResolvedValue({ ok, status, json: async () => result });
}

afterEach(() => vi.unstubAllGlobals());

describe("transcribe", () => {
  it("posts multipart file 到 /api/asr/transcribe，回結果（不套 {data} 信封）", async () => {
    const result = {
      segments: [
        {
          Start: 0,
          End: 1.2,
          Speaker: "A",
          Content: "你好",
          aligned: false,
          words: [],
        },
      ],
      raw_text: "你好",
      transcription_only: "你好",
      duration: 1.2,
      applied_context: "",
      alignment: {
        audio_duration: 1.5,
        speech_start: null,
        speech_end: null,
        aligned_duration: 0,
      },
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
      alignment: {
        audio_duration: 0,
        speech_start: null,
        speech_end: null,
        aligned_duration: 0,
      },
    });
    vi.stubGlobal("fetch", fetchMock);
    const file = new File(["x"], "a.wav");

    await transcribe(file, { extraTerms: ["王小明"], replaceContext: true });

    const [, init] = fetchMock.mock.calls[0];
    const body = init.body as FormData;
    expect(body.get("extra_terms")).toBe(JSON.stringify(["王小明"]));
    expect(body.get("replace_context")).toBe("true");
  });

  it("504 逾時附上音檔長度的脈絡", async () => {
    // 超過長度上限的音檔永遠拿不到成功回應，故事後的長度警示救不到它們（#35）。
    // 後端只說「逾時」，操作者不會想到要裁切音檔，脈絡得由 client 補上。
    const fetchMock = mockFetch(
      { error: { code: "REQUEST_TIMEOUT", message: "請求處理逾時，已中止並釋放資源。" } },
      false,
      504,
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(transcribe(new File(["x"], "a.wav"))).rejects.toThrow(/20 分鐘/);
  });

  it("非 504 的錯誤不附加長度脈絡", async () => {
    // 防誤導：格式不支援與音檔長度無關，附上「請裁切」只會把人帶錯方向。
    const fetchMock = mockFetch(
      { error: { code: "UNSUPPORTED_AUDIO_FORMAT", message: "不支援的音檔格式。" } },
      false,
      400,
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(transcribe(new File(["x"], "a.wav"))).rejects.toThrow(
      "不支援的音檔格式。",
    );
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
