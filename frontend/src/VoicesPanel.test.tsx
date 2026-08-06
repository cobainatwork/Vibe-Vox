import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { VoicesPanel } from "./VoicesPanel";
import * as api from "./voices";

vi.mock("./voices");

const mockedApi = vi.mocked(api);

function voice(over: Partial<api.Voice> = {}): api.Voice {
  return {
    id: "v1",
    name: "客戶-中年男性",
    type: "clone",
    language: "zh-TW",
    ref_audio_path: "/data/voices/abc",
    ref_text: null,
    instruct: null,
    created_at: "t",
    updated_at: "t",
    ...over,
  };
}

describe("VoicesPanel", () => {
  afterEach(() => vi.clearAllMocks());

  it("沒有音色時說明系統不附任何音色", async () => {
    mockedApi.listVoices.mockResolvedValue([]);

    render(<VoicesPanel />);

    expect(await screen.findByText(/尚未建立任何音色/)).toBeInTheDocument();
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

  it("後端錯誤時顯示訊息", async () => {
    mockedApi.listVoices.mockRejectedValue(new Error("音色名稱「甲」已存在"));

    render(<VoicesPanel />);

    expect(await screen.findByText("音色名稱「甲」已存在")).toBeInTheDocument();
  });
});
