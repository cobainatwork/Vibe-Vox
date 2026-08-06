import { afterEach, describe, expect, it, vi } from "vitest";

import { listTtsVoices, synthesizeSpeech } from "./tts";

afterEach(() => vi.unstubAllGlobals());

const VOICE = { id: "v1", name: "客戶-中年男性", type: "clone" as const, language: "zh-TW" };

describe("tts API client", () => {
  it("listTtsVoices 讀消費端契約的形狀，不套 {data} 信封", async () => {
    // /api/tts/* 是給 AI_practise 的消費端契約，ADR-0003 明定不套信封；
    // /api/admin/* 才有。兩者混用會在執行期拿到 undefined。
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({ voices: [VOICE] }) }),
    );

    await expect(listTtsVoices()).resolves.toEqual([VOICE]);
  });

  it("synthesizeSpeech 送出 input／voice／instruct 並回音訊 blob", async () => {
    const blob = new Blob([new Uint8Array([1, 2])], { type: "audio/wav" });
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      blob: async () => blob,
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      synthesizeSpeech({ input: "您好", voice: "v1", instruct: "語速偏快" }),
    ).resolves.toBe(blob);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/tts/speech");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({
      input: "您好",
      voice: "v1",
      instruct: "語速偏快",
    });
  });

  it("synthesizeSpeech 空 instruct 不進 body", async () => {
    // 契約 §5.2：instruct 未給時語氣由音色本身決定。送空字串會讓後端組出空前綴。
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      blob: async () => new Blob([]),
    });
    vi.stubGlobal("fetch", fetchMock);

    await synthesizeSpeech({ input: "您好", voice: "v1", instruct: "" });

    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      input: "您好",
      voice: "v1",
    });
  });

  it("失敗時取用錯誤信封的 message", async () => {
    // 成功回二進位、錯誤回 JSON（契約 §6）。無條件當 JSON 解會在成功路徑上炸掉。
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 404,
        json: async () => ({ error: { code: "VOICE_NOT_FOUND", message: "音色不存在或已被刪除。" } }),
      }),
    );

    await expect(synthesizeSpeech({ input: "您好", voice: "gone" })).rejects.toThrow(
      "音色不存在或已被刪除。",
    );
  });
});
