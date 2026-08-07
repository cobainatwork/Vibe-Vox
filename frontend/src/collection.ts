import { useCallback, useEffect, useState } from "react";

/**
 * 管理平面清單的載入狀態。
 *
 * 四態互斥是重點：先前三個 Panel 各自用「一個陣列 + 一個 error 字串」表示清單，於是
 * 「還沒回來」與「回來了是空的」在型別上是同一個值——首次 render 就顯示「尚未建立任何
 * 音色」，載入失敗時錯誤訊息與空清單提示同時出現。那是對操作者宣告一個假的系統狀態，
 * 違反能力感知（CONTEXT.md）。判別式讓那兩個情境不可能再被寫成同一個分支。
 *
 * `ready` 保證 items 非空——空清單一律是 `empty`，消費端不必再判長度。
 * `error` 不攜帶訊息：要顯示什麼由 `errorMessage` 回答，兩者問的是不同問題。
 */
export type Collection<T> =
  | { status: "loading" }
  | { status: "empty" }
  | { status: "ready"; items: T[] }
  | { status: "error" };

export type CollectionView<T> = {
  collection: Collection<T>;
  /**
   * 目前該對操作者說的錯誤訊息：清單載入失敗，或最近一次 `run` 失敗。
   *
   * 兩者共用一個欄位而非各留一份，是因為「誰的錯誤優先」是一條規則，不該讓每個
   * Panel 各寫一次；最後發生的那個覆蓋前一個，任何一次成功都清掉它。
   */
  errorMessage: string | null;
  /**
   * 執行一個變更操作，成功後重抓清單、失敗則寫進 `errorMessage`。
   * 不改動資料的操作（例如試聽）帶 `{ reload: false }`，省掉那次重抓。
   *
   * 失敗不改動 `collection`：清單仍然可信，把它轉成 `error` 只會讓表格消失，
   * 操作者除了重整沒有別的路可走。
   */
  run: (action: () => Promise<unknown>, options?: { reload?: boolean }) => Promise<void>;
};

/**
 * `load` 必須是穩定參照（`useCallback`）——它是重新載入的觸發條件，每次 render 都換一
 * 個新函式會讓 effect 無限重跑。參數變動（如搜尋字串）就放進 `useCallback` 的依賴，
 * 舊的請求會在 cleanup 被丟棄，後發先至的回應不會蓋掉新的。
 *
 * 錯誤訊息直接取 `err.message`：各 API client module 的 interface 都保證只拋 `Error`。
 */
export function useCollection<T>(load: () => Promise<T[]>): CollectionView<T> {
  const [collection, setCollection] = useState<Collection<T>>({ status: "loading" });
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [reloadToken, setReloadToken] = useState(0);

  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const items = await load();
        if (!alive) return;
        setCollection(items.length === 0 ? { status: "empty" } : { status: "ready", items });
        // 清單回來了，先前那次操作失敗的訊息已經過期。
        setErrorMessage(null);
      } catch (err) {
        if (!alive) return;
        setCollection({ status: "error" });
        setErrorMessage((err as Error).message);
      }
    })();
    return () => {
      alive = false;
    };
  }, [load, reloadToken]);

  // 重抓期間不退回 loading：舊清單仍然是目前已知最好的事實，閃成「載入中」只會讓
  // 每次刪除都抖一下表格。
  const run = useCallback(
    async (action: () => Promise<unknown>, options?: { reload?: boolean }) => {
      try {
        await action();
        setErrorMessage(null);
        if (options?.reload !== false) setReloadToken((n) => n + 1);
      } catch (err) {
        setErrorMessage((err as Error).message);
      }
    },
    [],
  );

  return { collection, errorMessage, run };
}
