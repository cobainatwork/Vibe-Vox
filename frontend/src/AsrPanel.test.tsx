import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as api from "./asr";
import { AsrPanel } from "./AsrPanel";
import type { Health } from "./health";

vi.mock("./asr");
const mockedApi = vi.mocked(api);

// 刻意一段對齊成功、一段失敗：兩種狀態的顯示是本頁的主要職責（ADR-0004）。
const RESULT: api.AsrResult = {
  segments: [
    {
      Start: 0.42,
      End: 1.2,
      Speaker: "語者 1",
      Content: "你好",
      aligned: true,
      words: [
        { Text: "你", Start: 0.42, End: 0.58 },
        { Text: "好", Start: 0.58, End: 1.2 },
      ],
    },
    {
      Start: 1.2,
      End: 2.0,
      Speaker: "語者 2",
      Content: "再見",
      aligned: false,
      words: [],
    },
  ],
  raw_text: '{"segments":[]}',
  transcription_only: "你好再見",
  duration: 2.0,
  applied_context: "台積電",
  alignment: {
    audio_duration: 2.4,
    speech_start: 0.42,
    speech_end: 1.2,
    aligned_duration: 0.78,
  },
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

  it("逐段標示對齊狀態", async () => {
    // 驗收：對齊失敗的段落在視覺上可立即辨識。狀態必須是顯式標記，不以「字級清單
    // 為空」隱含表示——空清單會被誤讀為「該段沒有字」（ADR-0004）。
    mockedApi.transcribe.mockResolvedValue(RESULT);
    render(<AsrPanel health={READY} />);
    upload();

    fireEvent.click(screen.getByRole("button", { name: "送出辨識" }));
    await screen.findByText("你好");

    expect(screen.getByText("已對齊")).toBeInTheDocument();
    expect(screen.getByText("未對齊")).toBeInTheDocument();
  });

  it("字級時間戳預設不渲染，展開後才出現", async () => {
    // 驗收：展開字級數字不造成明顯卡頓。字級資料量大（技術上限下逾萬個 Word），
    // 故未展開的段落不得把 word 放進 DOM——僅以 CSS 隱藏不算。
    mockedApi.transcribe.mockResolvedValue(RESULT);
    render(<AsrPanel health={READY} />);
    upload();
    fireEvent.click(screen.getByRole("button", { name: "送出辨識" }));
    await screen.findByText("你好");

    expect(screen.queryByText("你 0.42–0.58")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /字級/ }));

    expect(screen.getByText("你 0.42–0.58")).toBeInTheDocument();
    expect(screen.getByText("好 0.58–1.20")).toBeInTheDocument();
  });

  it("展開一段不渲染其他段的字級", async () => {
    // 摺疊須逐段獨立，否則展開任一段就把整份字級資料放進 DOM，效能防護等於沒有。
    const bothAligned: api.AsrResult = {
      ...RESULT,
      segments: [
        { ...RESULT.segments[0] },
        {
          ...RESULT.segments[1],
          aligned: true,
          words: [
            { Text: "再", Start: 1.3, End: 1.6 },
            { Text: "見", Start: 1.6, End: 2.0 },
          ],
        },
      ],
    };
    mockedApi.transcribe.mockResolvedValue(bothAligned);
    render(<AsrPanel health={READY} />);
    upload();
    fireEvent.click(screen.getByRole("button", { name: "送出辨識" }));
    await screen.findByText("你好");

    const [first] = screen.getAllByRole("button", { name: /字級/ });
    fireEvent.click(first);

    expect(screen.getByText("你 0.42–0.58")).toBeInTheDocument();
    expect(screen.queryByText("再 1.30–1.60")).not.toBeInTheDocument();
  });

  it("全段未對齊時給出整體警示", async () => {
    // 對齊服務不可用時 ASR 仍回 200 與完整逐字稿、全段標記未對齊（ADR-0004 的第二層
    // 降級）。這是最重要的失敗模式，但逐段狀態欄在長逐字稿裡要滾動才看得到。
    mockedApi.transcribe.mockResolvedValue({
      ...RESULT,
      segments: RESULT.segments.map((s) => ({ ...s, aligned: false, words: [] })),
    });
    render(<AsrPanel health={READY} />);
    upload();

    fireEvent.click(screen.getByRole("button", { name: "送出辨識" }));

    expect(await screen.findByText(/所有段落均未對齊/)).toBeInTheDocument();
  });

  it("有段落對齊成功時不給整體警示", async () => {
    // 防假警示：RESULT 有一段成功、一段失敗，不該被當成全段失敗。
    mockedApi.transcribe.mockResolvedValue(RESULT);
    render(<AsrPanel health={READY} />);
    upload();
    fireEvent.click(screen.getByRole("button", { name: "送出辨識" }));
    await screen.findByText("你好");

    expect(screen.queryByText(/所有段落均未對齊/)).not.toBeInTheDocument();
  });

  it("重新辨識後展開狀態重置", async () => {
    // 展開狀態以段落索引記錄，而不同結果的同一索引毫無關係。不重置會讓新結果的
    // 第一段莫名展開，且展開的是另一份音檔的字級數字。
    // 第二次回不同 Content，才能確認斷言是對新結果做的而非 setResult(null) 的中間態。
    const second: api.AsrResult = {
      ...RESULT,
      segments: [{ ...RESULT.segments[0], Content: "第二次辨識" }],
    };
    mockedApi.transcribe
      .mockResolvedValueOnce(RESULT)
      .mockResolvedValueOnce(second);
    render(<AsrPanel health={READY} />);
    upload();
    fireEvent.click(screen.getByRole("button", { name: "送出辨識" }));
    await screen.findByText("你好");
    fireEvent.click(screen.getByRole("button", { name: /字級/ }));
    expect(screen.getByText("你 0.42–0.58")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "送出辨識" }));
    await screen.findByText("第二次辨識");

    expect(screen.queryByText("你 0.42–0.58")).not.toBeInTheDocument();
  });

  it("未對齊的段落不給展開入口，即使字級清單非空", async () => {
    // 契約規則：`aligned` 必須顯式檢查，不可用 words 是否為空代替判斷
    // （docs/api/asr.md §4.4）。此處的字級清單非空是契約外的異常，但正因如此才要
    // 守住這條——只看 words 長度會把不可信的時間戳當成可查閱的資料呈現。
    mockedApi.transcribe.mockResolvedValue({
      ...RESULT,
      segments: [
        {
          ...RESULT.segments[0],
          aligned: false,
          words: [{ Text: "你", Start: 0.42, End: 0.58 }],
        },
      ],
    });
    render(<AsrPanel health={READY} />);
    upload();
    fireEvent.click(screen.getByRole("button", { name: "送出辨識" }));
    await screen.findByText("你好");

    expect(screen.queryByRole("button", { name: /字級/ })).not.toBeInTheDocument();
  });

  it("音檔超過 60 分鐘顯示提示", async () => {
    mockedApi.transcribe.mockResolvedValue({ ...RESULT, duration: 3700 });
    render(<AsrPanel health={READY} />);
    upload();

    fireEvent.click(screen.getByRole("button", { name: "送出辨識" }));

    expect(await screen.findByText(/超過 60 分鐘/)).toBeInTheDocument();
  });
});
