import { useCallback, useEffect, useState } from "react";

import { findBlockingService, type Health } from "./health";
import { listTtsVoices, synthesizeSpeech, type TtsVoice } from "./tts";
import { useObjectUrl } from "./useObjectUrl";

export function TtsPanel({ health }: { health: Health | null }) {
  const [voices, setVoices] = useState<TtsVoice[]>([]);
  const [voiceId, setVoiceId] = useState("");
  const [text, setText] = useState("");
  const [instruct, setInstruct] = useState("");
  const [audioUrl, showAudio] = useObjectUrl();
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const blocking = findBlockingService("tts", health);

  const load = useCallback(async () => {
    try {
      const got = await listTtsVoices();
      setVoices(got);
      setVoiceId((current) => current || got[0]?.id || "");
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "音色清單載入失敗");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const submit = async () => {
    if (!text.trim() || !voiceId) return;
    setLoading(true);
    setError(null);
    try {
      showAudio(await synthesizeSpeech({ input: text, voice: voiceId, instruct }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "合成失敗");
    } finally {
      setLoading(false);
    }
  };

  const noVoices = voices.length === 0;

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
      {noVoices && (
        <p className="panel__warn">
          尚未建立任何音色，請先到「音色管理」建立一個。系統不附任何內建音色。
        </p>
      )}
      {error && <p className="panel__warn">{error}</p>}

      <div className="asr-form">
        <label className="hw-input" htmlFor="tts-voice">
          音色
          <select
            id="tts-voice"
            aria-label="音色"
            value={voiceId}
            onChange={(e) => setVoiceId(e.target.value)}
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
          disabled={!text.trim() || loading || noVoices || blocking !== null}
        >
          {loading ? "合成中…" : "送出合成"}
        </button>
      </div>

      {audioUrl && (
        <div className="asr-result">
          {/* 沒有字幕軌可給：這是剛合成出來的語音，來源文字就在上面的輸入框裡。 */}
          <audio aria-label="合成結果" controls src={audioUrl} />
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
