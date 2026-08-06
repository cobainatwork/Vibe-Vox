import { useCallback, useEffect, useState } from "react";

import {
  createCloneVoice,
  deleteVoice,
  listVoices,
  renameVoice,
  type Voice,
} from "./voices";

const TYPE_LABEL: Record<Voice["type"], string> = {
  clone: "Clone",
  design: "Design",
};

export function VoicesPanel() {
  const [voices, setVoices] = useState<Voice[]>([]);
  const [error, setError] = useState<string | null>(null);

  const [name, setName] = useState("");
  const [language, setLanguage] = useState("zh-TW");
  const [refAudio, setRefAudio] = useState<File | null>(null);
  const [refText, setRefText] = useState("");

  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameTo, setRenameTo] = useState("");

  const load = useCallback(async () => {
    try {
      setVoices(await listVoices());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "音色載入失敗");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const run = async (action: () => Promise<unknown>) => {
    try {
      await action();
      setError(null);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "操作失敗");
    }
  };

  const submitClone = () => {
    // 參考音是 clone 音色的身分本體，沒有它建不出音色。
    if (!name.trim() || !refAudio) return;
    void run(async () => {
      await createCloneVoice(name, language, refAudio, refText);
      setName("");
      setRefText("");
      setRefAudio(null);
    });
  };

  const saveRename = () =>
    void run(async () => {
      await renameVoice(renamingId!, renameTo);
      setRenamingId(null);
    });

  return (
    <section className="panel">
      <h2 className="panel__title">音色管理</h2>
      <p className="panel__note">
        建立可重複使用的 TTS 音色。系統不附任何音色，全部由此建立；合成時一律以可控風格模式重播，故每個音色都支援 Instruction。Instruction 要寫發聲方式（音量、語速、句尾走向），寫情緒名稱沒有效果。
      </p>

      {error && <p className="panel__warn">{error}</p>}

      <form
        className="hw-form"
        onSubmit={(e) => {
          e.preventDefault();
          submitClone();
        }}
      >
        <label className="hw-input" htmlFor="voice-name">
          音色名稱
          <input
            id="voice-name"
            aria-label="音色名稱"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="例如：客戶-中年男性-謹慎"
          />
        </label>

        <label className="hw-input" htmlFor="voice-language">
          語言
          <input
            id="voice-language"
            aria-label="語言"
            value={language}
            onChange={(e) => setLanguage(e.target.value)}
          />
        </label>

        <label className="hw-input" htmlFor="voice-ref-audio">
          參考音檔
          <input
            id="voice-ref-audio"
            aria-label="參考音檔"
            type="file"
            accept="audio/*"
            onChange={(e) => setRefAudio(e.target.files?.[0] ?? null)}
          />
        </label>

        <label className="hw-input" htmlFor="voice-ref-text">
          參考音逐字稿（選填）
          <input
            id="voice-ref-text"
            aria-label="參考音逐字稿"
            value={refText}
            onChange={(e) => setRefText(e.target.value)}
            placeholder="僅供辨識參考音內容，不影響合成結果"
          />
        </label>

        <button className="btn" type="submit">
          建立 Voice clone
        </button>
      </form>

      {voices.length === 0 ? (
        <p className="hw-empty">尚未建立任何音色。上傳一段 5 至 30 秒的參考音即可建立第一個。</p>
      ) : (
        <table className="hw-table">
          <thead>
            <tr>
              <th>名稱</th>
              <th>型別</th>
              <th>語言</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {voices.map((v) => (
              <tr key={v.id}>
                <td>
                  {renamingId === v.id ? (
                    <input
                      aria-label="新名稱"
                      value={renameTo}
                      onChange={(e) => setRenameTo(e.target.value)}
                    />
                  ) : (
                    v.name
                  )}
                </td>
                <td>{TYPE_LABEL[v.type]}</td>
                <td>{v.language}</td>
                <td className="hw-actions">
                  {renamingId === v.id ? (
                    <>
                      <button className="hw-link" type="button" onClick={saveRename}>
                        儲存
                      </button>
                      <button
                        className="hw-link"
                        type="button"
                        onClick={() => setRenamingId(null)}
                      >
                        取消
                      </button>
                    </>
                  ) : (
                    <>
                      <button
                        className="hw-link"
                        type="button"
                        onClick={() => {
                          setRenamingId(v.id);
                          setRenameTo(v.name);
                        }}
                      >
                        改名
                      </button>
                      <button
                        className="hw-link hw-link--danger"
                        type="button"
                        onClick={() => void run(() => deleteVoice(v.id))}
                      >
                        刪除
                      </button>
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
