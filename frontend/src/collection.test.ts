import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { useCollection } from "./collection";

// load 必須是穩定參照，否則 effect 每次 render 都重跑——消費端一律以 useCallback 包。
function deferred<T>() {
  let settle!: (value: T) => void;
  let fail!: (err: Error) => void;
  const promise = new Promise<T>((resolve, reject) => {
    settle = resolve;
    fail = reject;
  });
  return { promise, settle, fail };
}

describe("useCollection", () => {
  it("清單回來之前是 loading，不宣稱空", async () => {
    // 這是 C6 的核心：首次 render 就說「尚未建立任何音色」是在宣告一個假的系統狀態。
    const d = deferred<string[]>();
    const load = vi.fn(() => d.promise);

    const { result } = renderHook(() => useCollection(load));

    expect(result.current.collection.status).toBe("loading");

    await act(async () => d.settle([]));
    await waitFor(() => expect(result.current.collection.status).toBe("empty"));
  });

  it("非空清單進 ready，並攜帶項目", async () => {
    const load = vi.fn(async () => ["甲", "乙"]);

    const { result } = renderHook(() => useCollection(load));

    await waitFor(() => expect(result.current.collection.status).toBe("ready"));
    expect(result.current.collection).toEqual({ status: "ready", items: ["甲", "乙"] });
  });

  it("載入失敗進 error，不宣稱空", async () => {
    // 載入失敗不代表清單是空的——error 與 empty 同時出現正是 C6 記載的假狀態。
    const load = vi.fn(async () => {
      throw new Error("音色載入失敗");
    });

    const { result } = renderHook(() => useCollection(load));

    await waitFor(() => expect(result.current.collection.status).toBe("error"));
    expect(result.current.errorMessage).toBe("音色載入失敗");
  });

  it("run 成功後重抓清單", async () => {
    const load = vi.fn(async () => ["甲"]);

    const { result } = renderHook(() => useCollection(load));
    await waitFor(() => expect(result.current.collection.status).toBe("ready"));

    await act(async () => {
      await result.current.run(async () => undefined);
    });

    expect(load).toHaveBeenCalledTimes(2);
  });

  it("run 失敗時保留清單，訊息走 errorMessage", async () => {
    // 刪除失敗不代表清單不可得。把它轉成 error 態會讓表格消失，操作者只能重整。
    const load = vi.fn(async () => ["甲"]);

    const { result } = renderHook(() => useCollection(load));
    await waitFor(() => expect(result.current.collection.status).toBe("ready"));

    await act(async () => {
      await result.current.run(async () => {
        throw new Error("音色使用中，無法刪除");
      });
    });

    expect(result.current.errorMessage).toBe("音色使用中，無法刪除");
    expect(result.current.collection).toEqual({ status: "ready", items: ["甲"] });
    expect(load).toHaveBeenCalledTimes(1);
  });

  it("run 成功清掉前一次的錯誤", async () => {
    const load = vi.fn(async () => ["甲"]);

    const { result } = renderHook(() => useCollection(load));
    await waitFor(() => expect(result.current.collection.status).toBe("ready"));
    await act(async () => {
      await result.current.run(async () => {
        throw new Error("暫時失敗");
      });
    });

    await act(async () => {
      await result.current.run(async () => undefined);
    });

    expect(result.current.errorMessage).toBeNull();
  });

  it("重新載入成功時清掉過期的錯誤", async () => {
    // 刪除失敗後改搜尋字串：清單重抓成功了，畫面卻還掛著上一次的失敗訊息。
    const first = vi.fn(async () => ["甲"]);
    const second = vi.fn(async () => ["乙"]);

    const { result, rerender } = renderHook(({ load }) => useCollection(load), {
      initialProps: { load: first },
    });
    await waitFor(() => expect(result.current.collection.status).toBe("ready"));
    await act(async () => {
      await result.current.run(async () => {
        throw new Error("音色使用中，無法刪除");
      });
    });
    expect(result.current.errorMessage).toBe("音色使用中，無法刪除");

    rerender({ load: second });

    await waitFor(() => expect(result.current.errorMessage).toBeNull());
  });

  it("reload:false 的動作不重抓清單", async () => {
    // 試聽不改動任何資料，重抓只是多打一次後端。
    const load = vi.fn(async () => ["甲"]);

    const { result } = renderHook(() => useCollection(load));
    await waitFor(() => expect(result.current.collection.status).toBe("ready"));

    await act(async () => {
      await result.current.run(async () => undefined, { reload: false });
    });

    expect(load).toHaveBeenCalledTimes(1);
  });
});
