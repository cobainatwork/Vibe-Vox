import { useState } from "react";

import { useCollection } from "./collection";
import { findBlockingService, type Health } from "./health";
import { previewSpokenForm } from "./spokenForm";
import { listTtsVoices, synthesizeSpeech } from "./tts";
import { useObjectUrl } from "./useObjectUrl";

export function TtsPanel({ health }: { health: Health | null }) {
  const { collection, errorMessage: voicesError } = useCollection(listTtsVoices);
  const voices = collection.status === "ready" ? collection.items : [];
  // 未選時落到第一個：選單只在 ready 態有選項，所以清單未回時它必然是空字串。
  const [chosenId, setChosenId] = useState("");
  const voiceId = chosenId || voices[0]?.id || "";

  const [text, setText] = useState("");
  const [instruct, setInstruct] = useState("");
  const [audioUrl, showAudio] = useObjectUrl();
  const [spoken, setSpoken] = useState<string | null>(null);
  const [synthesisError, setSynthesisError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const blocking = findBlockingService("tts", health);
  // 合成錯誤不歸清單那個 hook 管，但兩者共用同一條訊息列。
  const errorMessage = voicesError ?? synthesisError;
  // 清單未回或取不到時一併停用：沒有可選音色的送出必然失敗。
  const submitDisabled =
    !text.trim() || loading || collection.status !== "ready" || blocking !== null;

  const submit = async () => {
    if (!text.trim() || !voiceId) return;
    setLoading(true);
    setSynthesisError(null);
    setSpoken(null);
    try {
      showAudio(await synthesizeSpeech({ input: text, voice: voiceId, instruct }));
      // 前處理後的文字是診斷用的附加資訊，**在合成之後取且失敗不上報**：前端與 BFF 是
      // 兩個部署單元，image 落後時這個端點根本不存在，讓它擋住合成等於用一個診斷功能
      // 換掉主功能。
      setSpoken(await previewSpokenForm(text, instruct).catch(() => null));
    } catch (err) {
      setSynthesisError((err as Error).message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <section className="panel">
      <h2 className="panel__title">TTS 測試</h2>
      <p className="panel__note">
        以既有音色合成語音。Instruction 要寫發聲方式（音量、語速、句尾走向），寫情緒名稱
        沒有效果。
      </p>

      {blocking && (
        <p className="panel__warn">
          {blocking.toUpperCase()} 服務尚未就緒，已停用送出以避免無效操作。
        </p>
      )}
      {collection.status === "loading" && (
        <p className="panel__note" role="status">
          音色清單載入中…
        </p>
      )}
      {collection.status === "empty" && (
        <p className="panel__warn">
          尚未建立任何音色，請先到「音色管理」建立一個。系統不附任何內建音色。
        </p>
      )}
      {errorMessage && <p className="panel__warn">{errorMessage}</p>}

      <div className="asr-form">
        <label className="hw-input" htmlFor="tts-voice">
          音色
          <select
            id="tts-voice"
            aria-label="音色"
            value={voiceId}
            onChange={(e) => setChosenId(e.target.value)}
          >
            {voices.map((v) => (
              <option key={v.id} value={v.id}>
                {v.name}
              </option>
            ))}
          </select>
        </label>

        <label className="hw-input" htmlFor="tts-text">
          要合成的文字
          <textarea
            id="tts-text"
            aria-label="要合成的文字"
            rows={3}
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="您好，我想了解一下這張保單的內容。"
          />
        </label>

        <label className="hw-input" htmlFor="tts-instruct">
          Instruction（選填）
          <input
            id="tts-instruct"
            aria-label="Instruction（選填）"
            value={instruct}
            onChange={(e) => setInstruct(e.target.value)}
            placeholder="例如：語速偏快、音量略大、句尾上揚"
          />
        </label>

        <button
          className="btn"
          type="button"
          onClick={() => void submit()}
          disabled={submitDisabled}
        >
          {loading ? "合成中…" : "送出合成"}
        </button>
      </div>

      {audioUrl && (
        <div className="asr-result">
          {/* 沒有字幕軌可給：這是剛合成出來的語音，來源文字就在上面的輸入框裡。 */}
          <audio aria-label="合成結果" controls src={audioUrl} />
          {/* 唸錯時要能分辨是前處理錯了還是模型錯了。取不到就不顯示——寧可少一段診斷
              資訊，也不要在這裡放一則看起來像合成失敗的錯誤。 */}
          {spoken && (
            <p className="tts-spoken">
              前處理後的文字：<span>{spoken}</span>
            </p>
          )}
          <div className="asr-actions">
            <a className="hw-link" href={audioUrl} download="speech.wav">
              下載 wav
            </a>
          </div>
        </div>
      )}
    </section>
  );
}
