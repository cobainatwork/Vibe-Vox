import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { HotwordsPanel } from "./HotwordsPanel";
import * as api from "./hotwords";

vi.mock("./hotwords");

const mockedApi = vi.mocked(api);

function hw(over: Partial<api.Hotword> = {}): api.Hotword {
  return {
    id: "1",
    term: "台積電",
    note: null,
    enabled: true,
    created_at: "t",
    updated_at: "t",
    ...over,
  };
}

describe("HotwordsPanel", () => {
  afterEach(() => vi.clearAllMocks());

  it("載入時列出 hotwords", async () => {
    mockedApi.listHotwords.mockResolvedValue([hw()]);

    render(<HotwordsPanel />);

    expect(await screen.findByText("台積電")).toBeInTheDocument();
  });

  it("清單回來之前不宣稱沒有 Hotword", () => {
    // 「還沒問到」與「問過了，沒有」是兩件事，不能共用同一個畫面。
    mockedApi.listHotwords.mockReturnValue(new Promise(() => {}));

    render(<HotwordsPanel />);

    expect(screen.queryByText(/尚無 Hotword/)).not.toBeInTheDocument();
    expect(screen.getByText(/載入中/)).toBeInTheDocument();
  });

  it("載入失敗時顯示訊息，且不宣稱沒有 Hotword", async () => {
    mockedApi.listHotwords.mockRejectedValue(new Error("Hotword 請求失敗：HTTP 503"));

    render(<HotwordsPanel />);

    expect(await screen.findByText("Hotword 請求失敗：HTTP 503")).toBeInTheDocument();
    expect(screen.queryByText(/尚無 Hotword/)).not.toBeInTheDocument();
  });

  it("切換 enabled 以目標值呼叫 setHotwordEnabled", async () => {
    mockedApi.listHotwords.mockResolvedValue([hw({ enabled: true })]);
    mockedApi.setHotwordEnabled.mockResolvedValue(hw({ enabled: false }));

    render(<HotwordsPanel />);
    const toggle = await screen.findByRole("checkbox");
    fireEvent.click(toggle);

    await waitFor(() => expect(mockedApi.setHotwordEnabled).toHaveBeenCalledWith("1", false));
  });

  it("新增表單送出呼叫 createHotword", async () => {
    mockedApi.listHotwords.mockResolvedValue([]);
    mockedApi.createHotword.mockResolvedValue(hw({ term: "新詞" }));

    render(<HotwordsPanel />);
    await waitFor(() => expect(mockedApi.listHotwords).toHaveBeenCalled());

    fireEvent.change(screen.getByLabelText("新增 term"), { target: { value: "新詞" } });
    fireEvent.click(screen.getByRole("button", { name: "新增" }));

    await waitFor(() => expect(mockedApi.createHotword).toHaveBeenCalledWith("新詞", ""));
  });

  it("搜尋輸入以 query 呼叫 listHotwords", async () => {
    mockedApi.listHotwords.mockResolvedValue([]);

    render(<HotwordsPanel />);
    await waitFor(() => expect(mockedApi.listHotwords).toHaveBeenCalled());

    fireEvent.change(screen.getByLabelText("搜尋 Hotword"), { target: { value: "台積" } });

    await waitFor(() => expect(mockedApi.listHotwords).toHaveBeenCalledWith("台積"));
  });

  it("刪除按鈕呼叫 deleteHotword", async () => {
    mockedApi.listHotwords.mockResolvedValue([hw()]);
    mockedApi.deleteHotword.mockResolvedValue();

    render(<HotwordsPanel />);
    fireEvent.click(await screen.findByRole("button", { name: "刪除" }));

    await waitFor(() => expect(mockedApi.deleteHotword).toHaveBeenCalledWith("1"));
  });

  it("預覽 context 呼叫 previewContext 並顯示編譯字串", async () => {
    mockedApi.listHotwords.mockResolvedValue([]);
    mockedApi.previewContext.mockResolvedValue({
      context: "台積電、聯發科",
      token_estimate: 6,
      token_budget: 8000,
    });

    render(<HotwordsPanel />);
    fireEvent.click(await screen.findByRole("button", { name: "預覽 context" }));

    expect(await screen.findByText(/台積電、聯發科/)).toBeInTheDocument();
  });

  it("匯入檔案呼叫 importHotwords 並顯示結果", async () => {
    mockedApi.listHotwords.mockResolvedValue([]);
    mockedApi.importHotwords.mockResolvedValue({ created: 2, updated: 1 });

    render(<HotwordsPanel />);
    const file = new File(["[]"], "h.json", { type: "application/json" });
    fireEvent.change(screen.getByLabelText("匯入檔案"), { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: "匯入" }));

    await waitFor(() =>
      expect(mockedApi.importHotwords).toHaveBeenCalledWith(file, "json"),
    );
    expect(await screen.findByText(/新增 2/)).toBeInTheDocument();
  });
});
