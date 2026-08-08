import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as api from "./tts";
import * as preview from "./spokenForm";
import { TtsPanel } from "./TtsPanel";
import type { Health } from "./health";

vi.mock("./tts");
vi.mock("./spokenForm");
const mockedApi = vi.mocked(api);
const mockedPreview = vi.mocked(preview);

const READY: Health = { asr: { ready: true }, tts: { ready: true } };
const TTS_DOWN: Health = { asr: { ready: true }, tts: { ready: false } };

const VOICE: api.TtsVoice = {
  id: "v1",
  name: "客戶-中年男性",
  type: "clone",
  language: "zh-TW",
};

beforeEach(() => {
  // jsdom 沒有這兩個方法，播放與下載都靠它們把 blob 變成可用的 URL。直接掛在 URL 上
  // 而不用 stubGlobal 換掉整個物件：後者會在 afterEach 還原，而 React 的 unmount 清理
  // 跑在那之後，撤銷 URL 時就會撞到「函式不存在」。
  URL.createObjectURL = vi.fn(() => "blob:fake");
  URL.revokeObjectURL = vi.fn();
  mockedApi.listTtsVoices.mockResolvedValue([VOICE]);
});

afterEach(() => vi.clearAllMocks());

async function fillAndSubmit(text = "您好，我想了解一下這張保單。") {
  await screen.findByText("客戶-中年男性");
  fireEvent.change(screen.getByLabelText("要合成的文字"), { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: "送出合成" }));
}

describe("TtsPanel", () => {
  it("合成後顯示前處理層輸出的文字", async () => {
    // 唸錯不會回錯誤也不進 log。把前處理後的字串攤出來，操作者才分得出是前處理錯了還是
    // 模型錯了。
    mockedApi.synthesizeSpeech.mockResolvedValue(new Blob([new Uint8Array([1])]));
    mockedPreview.previewSpokenForm.mockResolvedValue("重量三公斤");
    render(<TtsPanel health={READY} />);

    await fillAndSubmit("重量 3kg");

    expect(await screen.findByText("重量三公斤")).toBeInTheDocument();
  });

  it("預覽失敗不影響合成結果", async () => {
    // 預覽是診斷用的附加資訊。前端與 BFF 是兩個部署單元，image 落後時這個端點根本不存在
    // ——讓它擋住合成等於用一個診斷功能換掉主功能。
    mockedApi.synthesizeSpeech.mockResolvedValue(new Blob([new Uint8Array([1])]));
    mockedPreview.previewSpokenForm.mockRejectedValue(new Error("HTTP 404"));
    render(<TtsPanel health={READY} />);

    await fillAndSubmit();

    expect(await screen.findByLabelText("合成結果")).toBeInTheDocument();
    expect(screen.queryByText(/HTTP 404/)).not.toBeInTheDocument();
  });

  it("合成後提供可播放與可下載的音訊", async () => {
    mockedApi.synthesizeSpeech.mockResolvedValue(
      new Blob([new Uint8Array([1])], { type: "audio/wav" }),
    );
    render(<TtsPanel health={READY} />);

    await fillAndSubmit();

    const player = await screen.findByLabelText("合成結果");
    expect(player).toHaveAttribute("src", "blob:fake");
    expect(screen.getByRole("link", { name: "下載 wav" })).toHaveAttribute("href", "blob:fake");
  });

  it("Instruction 隨請求送出", async () => {
    // #6 驗收項：Instruction 要進得去。沒有這條，欄位存在但沒接上也不會有人發現。
    mockedApi.synthesizeSpeech.mockResolvedValue(new Blob([]));
    render(<TtsPanel health={READY} />);

    await screen.findByText("客戶-中年男性");
    fireEvent.change(screen.getByLabelText("Instruction（選填）"), {
      target: { value: "語速偏快、音量略大" },
    });
    await fillAndSubmit();

    await waitFor(() =>
      expect(mockedApi.synthesizeSpeech).toHaveBeenCalledWith({
        input: "您好，我想了解一下這張保單。",
        voice: "v1",
        instruct: "語速偏快、音量略大",
      }),
    );
  });

  it("合成失敗時顯示後端的錯誤訊息", async () => {
    mockedApi.synthesizeSpeech.mockRejectedValue(new Error("音色不存在或已被刪除。"));
    render(<TtsPanel health={READY} />);

    await fillAndSubmit();

    expect(await screen.findByText("音色不存在或已被刪除。")).toBeInTheDocument();
  });

  it("TTS 服務未就緒時停用送出", async () => {
    render(<TtsPanel health={TTS_DOWN} />);

    await screen.findByText("客戶-中年男性");
    fireEvent.change(screen.getByLabelText("要合成的文字"), { target: { value: "測試" } });

    expect(screen.getByRole("button", { name: "送出合成" })).toBeDisabled();
  });

  it("沒有任何音色時說明要先去建立，而非給一個送不出去的表單", async () => {
    // 新部署此清單必為空（契約 §3）：系統不附任何音色。
    mockedApi.listTtsVoices.mockResolvedValue([]);
    render(<TtsPanel health={READY} />);

    expect(await screen.findByText(/尚未建立任何音色/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "送出合成" })).toBeDisabled();
  });

  it("音色清單回來之前不宣稱沒有音色，但也不讓人送出", () => {
    // 「還沒問到」時叫操作者去建立音色，是要他去修一個可能不存在的問題。
    mockedApi.listTtsVoices.mockReturnValue(new Promise(() => {}));
    render(<TtsPanel health={READY} />);

    expect(screen.queryByText(/尚未建立任何音色/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "送出合成" })).toBeDisabled();
    // 選單空、按鈕灰掉而畫面不說話，操作者只會以為壞了。
    expect(screen.getByText(/音色清單載入中/)).toBeInTheDocument();
  });

  it("音色清單載入失敗時顯示訊息，且不宣稱沒有音色", async () => {
    mockedApi.listTtsVoices.mockRejectedValue(new Error("音色清單載入失敗：HTTP 503"));
    render(<TtsPanel health={READY} />);

    expect(await screen.findByText("音色清單載入失敗：HTTP 503")).toBeInTheDocument();
    expect(screen.queryByText(/尚未建立任何音色/)).not.toBeInTheDocument();
  });
});
