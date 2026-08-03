import { useState } from "react";

import { transcribe, type AsrResult } from "./asr";
import { findBlockingService, type Health } from "./health";

type View = "segments" | "text" | "raw";

const MAX_DURATION_SECONDS = 60 * 60;

export function AsrPanel({ health }: { health: Health | null }) {
  const [file, setFile] = useState<File | null>(null);
  const [extraTerms, setExtraTerms] = useState("");
  const [replaceContext, setReplaceContext] = useState(false);
  const [result, setResult] = useState<AsrResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [elapsedMs, setElapsedMs] = useState<number | null>(null);
  const [view, setView] = useState<View>("segments");
  const [copyNote, setCopyNote] = useState<string | null>(null);

  const blocking = findBlockingService("asr", health);

  const submit = async () => {
    if (!file) return;
    setLoading(true);
    setError(null);
    setResult(null);
    setCopyNote(null);
    const start = performance.now();
    try {
      const terms = extraTerms
        .split(",")
        .map((t) => t.trim())
        .filter(Boolean);
      const got = await transcribe(file, {
        extraTerms: terms.length ? terms : undefined,
        replaceContext,
      });
      setResult(got);
      setElapsedMs(performance.now() - start);
      setView("segments");
    } catch (err) {
      setError(err instanceof Error ? err.message : "辨識失敗");
    } finally {
      setLoading(false);
    }
  };

  const copyText = async () => {
    if (!result) return;
    try {
      if (!navigator.clipboard?.writeText) {
        throw new Error("clipboard unavailable");
      }
      await navigator.clipboard.writeText(result.transcription_only);
      setCopyNote("已複製純文字。");
    } catch {
      // 地端無 TLS（http 內網）時 navigator.clipboard 不存在，降級為提示手動選取。
      setCopyNote("此環境不支援自動複製，請手動選取純文字視圖內容。");
    }
  };

  const exportJson = () => {
    if (!result) return;
    const blob = new Blob([JSON.stringify(result, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "asr-result.json";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  };

  const overLong = result != null && result.duration > MAX_DURATION_SECONDS;

  return (
    <section className="panel">
      <h2 className="panel__title">ASR 測試</h2>
      <p className="panel__note">上傳音檔辨識，回帶語者與時間戳的分段結果。</p>

      {blocking && (
        <p className="panel__warn">
          {blocking.toUpperCase()} 服務尚未就緒，已停用送出以避免無效操作。
        </p>
      )}

      <div className="asr-form">
        <input
          type="file"
          aria-label="音檔"
          accept="audio/*"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
        />
        <input
          className="hw-input"
          aria-label="臨時詞彙"
          placeholder="臨時詞彙（逗號分隔）"
          value={extraTerms}
          onChange={(e) => setExtraTerms(e.target.value)}
        />
        <label className="asr-check">
          <input
            type="checkbox"
            aria-label="覆寫啟用中詞彙"
            checked={replaceContext}
            onChange={(e) => setReplaceContext(e.target.checked)}
          />
          覆寫（本次不套用啟用中 Hotword）
        </label>
        <button
          className="btn"
          type="button"
          onClick={() => void submit()}
          disabled={!file || loading || blocking !== null}
        >
          {loading ? "辨識中…" : "送出辨識"}
        </button>
      </div>

      {error && <p className="panel__warn">{error}</p>}

      {result && (
        <div className="asr-result">
          {overLong && (
            <p className="panel__warn">
              音檔長度 {(result.duration / 60).toFixed(1)} 分鐘，超過 60 分鐘上限，
              辨識耗時與品質可能受影響。
            </p>
          )}
          <p className="panel__note">
            本次套用詞彙：{result.applied_context || "（無）"}
            {elapsedMs != null && ` · 耗時 ${(elapsedMs / 1000).toFixed(1)} 秒`}
          </p>

          <div className="asr-views" role="tablist">
            <button
              type="button"
              id="asr-tab-segments"
              role="tab"
              aria-selected={view === "segments"}
              aria-controls="asr-panel-view"
              className={view === "segments" ? "hw-link hw-link--active" : "hw-link"}
              onClick={() => setView("segments")}
            >
              分段
            </button>
            <button
              type="button"
              id="asr-tab-text"
              role="tab"
              aria-selected={view === "text"}
              aria-controls="asr-panel-view"
              className={view === "text" ? "hw-link hw-link--active" : "hw-link"}
              onClick={() => setView("text")}
            >
              純文字
            </button>
            <button
              type="button"
              id="asr-tab-raw"
              role="tab"
              aria-selected={view === "raw"}
              aria-controls="asr-panel-view"
              className={view === "raw" ? "hw-link hw-link--active" : "hw-link"}
              onClick={() => setView("raw")}
            >
              原始
            </button>
          </div>

          <div id="asr-panel-view" role="tabpanel" aria-labelledby={`asr-tab-${view}`}>
            {view === "segments" && (
              <table className="asr-segments">
                <thead>
                  <tr>
                    <th>起始</th>
                    <th>結束</th>
                    <th>語者</th>
                    <th>內容</th>
                  </tr>
                </thead>
                <tbody>
                  {result.segments.map((s, i) => (
                    <tr key={i}>
                      <td>{s.Start.toFixed(2)}</td>
                      <td>{s.End.toFixed(2)}</td>
                      <td>{s.Speaker}</td>
                      <td>{s.Content}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {view === "text" && (
              <pre className="hw-preview__text">{result.transcription_only}</pre>
            )}
            {view === "raw" && <pre className="hw-preview__text">{result.raw_text}</pre>}
          </div>

          <div className="asr-actions">
            <button className="hw-link" type="button" onClick={() => void copyText()}>
              複製純文字
            </button>
            <button className="hw-link" type="button" onClick={exportJson}>
              匯出 JSON
            </button>
          </div>
          {copyNote && <p className="panel__note">{copyNote}</p>}
        </div>
      )}
    </section>
  );
}
