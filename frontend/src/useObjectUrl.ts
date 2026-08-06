import { useCallback, useEffect, useRef, useState } from "react";

/**
 * 管理一個會被替換的 blob object URL。
 *
 * 手寫這段的兩個地方（TTS 測試頁與音色試聽）已經長得不一樣——其中一個漏了卸載時的
 * 撤銷。這種「忘記一步就靜默洩漏」的樣板該只有一份。
 *
 * 回傳當前 URL 與一個 show(blob)：它撤銷上一份、建立新的。撤銷用 ref 而非 state 追
 * 蹤：換 URL 與撤銷舊 URL 必須在同一個動作內完成，靠 effect 依賴追會晚一個 render。
 */
export function useObjectUrl(): [string | null, (blob: Blob) => void] {
  const [url, setUrl] = useState<string | null>(null);
  const current = useRef<string | null>(null);

  useEffect(() => {
    return () => {
      if (current.current) URL.revokeObjectURL(current.current);
    };
  }, []);

  const show = useCallback((blob: Blob) => {
    if (current.current) URL.revokeObjectURL(current.current);
    const next = URL.createObjectURL(blob);
    current.current = next;
    setUrl(next);
  }, []);

  return [url, show];
}
