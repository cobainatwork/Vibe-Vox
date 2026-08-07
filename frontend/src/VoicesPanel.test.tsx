import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as tts from "./tts";
import { VoicesPanel } from "./VoicesPanel";
import * as api from "./voices";

vi.mock("./voices");
vi.mock("./tts");

const mockedApi = vi.mocked(api);
const mockedTts = vi.mocked(tts);

function voice(over: Partial<api.Voice> = {}): api.Voice {
  return {
    id: "v1",
    name: "客戶-中年男性",
    type: "clone",
    language: "zh-TW",
    ref_audio_path: "/data/voices/abc",
    ref_text: null,
    instruct: null,
    unusable_reason: null,
    created_at: "t",
    updated_at: "t",
    ...over,
  };
}

describe("VoicesPanel", () => {
  afterEach(() => vi.clearAllMocks());

  it("試聽走與消費端相同的合成端點", async () => {
    // 試聽若另走一條路，這裡聽起來正常而 AI_practise 拿到的東西壞掉時沒有人會發現。
    mockedApi.listVoices.mockResolvedValue([voice()]);
    mockedTts.synthesizeSpeech.mockResolvedValue(new Blob([new Uint8Array([1])]));
    URL.createObjectURL = vi.fn(() => "blob:fake");
    URL.revokeObjectURL = vi.fn();

    render(<VoicesPanel />);
    fireEvent.click(await screen.findByRole("button", { name: "試聽" }));

    await waitFor(() =>
      expect(mockedTts.synthesizeSpeech).toHaveBeenCalledWith(
        expect.objectContaining({ voice: "v1" }),
      ),
    );
    expect(await screen.findByLabelText("試聽 客戶-中年男性")).toHaveAttribute(
      "src",
      "blob:fake",
    );
    // 試聽不改動任何資料，不該觸發清單重抓——載入時那一次以外不應該有第二次。
    expect(mockedApi.listVoices).toHaveBeenCalledTimes(1);
  });

  it("試聽句含數字，讓操作者聽得到數字唸法", async () => {
    // TN 前處理層唸錯不會回錯誤也不進 log，試聽是操作者唯一能親耳驗證的地方。試聽句不含
    // 數字的話，這條路徑上沒有任何人會發現它壞了。
    mockedApi.listVoices.mockResolvedValue([voice()]);
    mockedTts.synthesizeSpeech.mockResolvedValue(new Blob([new Uint8Array([1])]));
    URL.createObjectURL = vi.fn(() => "blob:fake");
    URL.revokeObjectURL = vi.fn();

    render(<VoicesPanel />);
    fireEvent.click(await screen.findByRole("button", { name: "試聽" }));

    await waitFor(() => expect(mockedTts.synthesizeSpeech).toHaveBeenCalled());
    const { input } = mockedTts.synthesizeSpeech.mock.calls[0][0];
    expect(input).toMatch(/\d/);
  });

  it("標出參考音不可用的音色，並擋掉一定失敗的試聽", async () => {
    // 建立時的驗證只對新音色生效。既有音色可能超界或參考音已遺失，而清單是操作者唯一
    // 看得到音色的地方——不標的話他只會看到試聽失敗，而錯誤訊息長得像整個面板壞了。
    mockedApi.listVoices.mockResolvedValue([
      voice({ id: "v1", name: "正常音色" }),
      voice({
        id: "v2",
        name: "超界音色",
        unusable_reason: "參考音時長 40.0 秒不在允許範圍 1.0 至 30.0 秒內，請裁剪後再上傳。",
      }),
    ]);

    render(<VoicesPanel />);

    const broken = (await screen.findByText("超界音色")).closest("tr")!;
    expect(within(broken).getByText(/參考音時長 40.0 秒/)).toBeInTheDocument();
    expect(within(broken).getByRole("button", { name: "試聽" })).toBeDisabled();

    const ok = screen.getByText("正常音色").closest("tr")!;
    expect(within(ok).getByRole("button", { name: "試聽" })).toBeEnabled();
  });

  it("沒有音色時說明系統不附任何音色", async () => {
    mockedApi.listVoices.mockResolvedValue([]);

    render(<VoicesPanel />);

    expect(await screen.findByText(/尚未建立任何音色/)).toBeInTheDocument();
  });

  it("清單回來之前不宣稱沒有音色", () => {
    // 首次 render 就說「尚未建立任何音色」，是把「還沒問到」講成「問過了，沒有」。
    mockedApi.listVoices.mockReturnValue(new Promise(() => {}));

    render(<VoicesPanel />);

    expect(screen.queryByText(/尚未建立任何音色/)).not.toBeInTheDocument();
    expect(screen.getByText(/載入中/)).toBeInTheDocument();
  });

  it("載入時列出音色", async () => {
    mockedApi.listVoices.mockResolvedValue([voice()]);

    render(<VoicesPanel />);

    expect(await screen.findByText("客戶-中年男性")).toBeInTheDocument();
  });

  it("上傳參考音送出後呼叫 createCloneVoice", async () => {
    mockedApi.listVoices.mockResolvedValue([]);
    mockedApi.createCloneVoice.mockResolvedValue(voice());
    const file = new File([new Uint8Array([1])], "ref.wav", { type: "audio/wav" });

    render(<VoicesPanel />);
    await screen.findByText(/尚未建立任何音色/);

    fireEvent.change(screen.getByLabelText("音色名稱"), { target: { value: "新音色" } });
    fireEvent.change(screen.getByLabelText("參考音檔"), { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: "建立 Voice clone" }));

    await waitFor(() =>
      expect(mockedApi.createCloneVoice).toHaveBeenCalledWith("新音色", "zh-TW", file, ""),
    );
  });

  it("未選參考音時不送出", async () => {
    mockedApi.listVoices.mockResolvedValue([]);

    render(<VoicesPanel />);
    await screen.findByText(/尚未建立任何音色/);

    fireEvent.change(screen.getByLabelText("音色名稱"), { target: { value: "新音色" } });
    fireEvent.click(screen.getByRole("button", { name: "建立 Voice clone" }));

    await waitFor(() => expect(mockedApi.createCloneVoice).not.toHaveBeenCalled());
  });

  it("刪除呼叫 deleteVoice 並重新載入", async () => {
    mockedApi.listVoices.mockResolvedValue([voice()]);
    mockedApi.deleteVoice.mockResolvedValue(undefined);

    render(<VoicesPanel />);
    fireEvent.click(await screen.findByRole("button", { name: "刪除" }));

    await waitFor(() => expect(mockedApi.deleteVoice).toHaveBeenCalledWith("v1"));
    await waitFor(() => expect(mockedApi.listVoices).toHaveBeenCalledTimes(2));
  });

  it("改名以新名稱呼叫 renameVoice", async () => {
    mockedApi.listVoices.mockResolvedValue([voice()]);
    mockedApi.renameVoice.mockResolvedValue(voice({ name: "新名字" }));

    render(<VoicesPanel />);
    fireEvent.click(await screen.findByRole("button", { name: "改名" }));
    fireEvent.change(screen.getByLabelText("新名稱"), { target: { value: "新名字" } });
    fireEvent.click(screen.getByRole("button", { name: "儲存" }));

    await waitFor(() => expect(mockedApi.renameVoice).toHaveBeenCalledWith("v1", "新名字"));
  });

  it("後端錯誤時顯示訊息，且不同時宣稱沒有音色", async () => {
    mockedApi.listVoices.mockRejectedValue(new Error("音色名稱「甲」已存在"));

    render(<VoicesPanel />);

    expect(await screen.findByText("音色名稱「甲」已存在")).toBeInTheDocument();
    // 載入失敗代表不知道有沒有音色，不代表沒有。
    expect(screen.queryByText(/尚未建立任何音色/)).not.toBeInTheDocument();
  });
});
