import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as api from "./asr";
import { AsrPanel } from "./AsrPanel";
import type { Health } from "./health";

vi.mock("./asr");
const mockedApi = vi.mocked(api);

const RESULT: api.AsrResult = {
  segments: [
    { Start: 0, End: 1.2, Speaker: "語者 1", Content: "你好" },
    { Start: 1.2, End: 2.0, Speaker: "語者 2", Content: "再見" },
  ],
  raw_text: '{"segments":[]}',
  transcription_only: "你好再見",
  duration: 2.0,
  applied_context: "台積電",
};

const READY: Health = { asr: { ready: true }, tts: { ready: true } };

afterEach(() => vi.clearAllMocks());

function upload(name = "a.wav") {
  const file = new File(["x"], name, { type: "audio/wav" });
  fireEvent.change(screen.getByLabelText("音檔"), { target: { files: [file] } });
  return file;
}

describe("AsrPanel", () => {
  it("上傳送出後顯示分段結果與本次套用詞彙", async () => {
    mockedApi.transcribe.mockResolvedValue(RESULT);
    render(<AsrPanel health={READY} />);
    const file = upload();

    fireEvent.click(screen.getByRole("button", { name: "送出辨識" }));

    expect(await screen.findByText("你好")).toBeInTheDocument();
    expect(screen.getByText("再見")).toBeInTheDocument();
    expect(screen.getByText(/台積電/)).toBeInTheDocument();
    await waitFor(() =>
      expect(mockedApi.transcribe).toHaveBeenCalledWith(
        file,
        expect.objectContaining({ replaceContext: false }),
      ),
    );
  });

  it("可切換到純文字視圖", async () => {
    mockedApi.transcribe.mockResolvedValue(RESULT);
    render(<AsrPanel health={READY} />);
    upload();
    fireEvent.click(screen.getByRole("button", { name: "送出辨識" }));
    await screen.findByText("你好");

    fireEvent.click(screen.getByRole("tab", { name: "純文字" }));

    expect(screen.getByText("你好再見")).toBeInTheDocument();
  });

  it("錯誤時顯示後端訊息", async () => {
    mockedApi.transcribe.mockRejectedValue(new Error("不支援的音檔格式。"));
    render(<AsrPanel health={READY} />);
    upload();

    fireEvent.click(screen.getByRole("button", { name: "送出辨識" }));

    expect(await screen.findByText("不支援的音檔格式。")).toBeInTheDocument();
  });

  it("音檔超過 60 分鐘顯示提示", async () => {
    mockedApi.transcribe.mockResolvedValue({ ...RESULT, duration: 3700 });
    render(<AsrPanel health={READY} />);
    upload();

    fireEvent.click(screen.getByRole("button", { name: "送出辨識" }));

    expect(await screen.findByText(/超過 60 分鐘/)).toBeInTheDocument();
  });
});
