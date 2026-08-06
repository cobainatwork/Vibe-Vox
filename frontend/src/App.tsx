import { useEffect, useState } from "react";

import { AsrPanel } from "./AsrPanel";
import { HotwordsPanel } from "./HotwordsPanel";
import { TtsPanel } from "./TtsPanel";
import { VoicesPanel } from "./VoicesPanel";
import { fetchHealth } from "./api";
import {
  SECTIONS,
  SECTION_ORDER,
  findBlockingService,
  type Health,
  type SectionKey,
  type ServiceKey,
} from "./health";

type HealthState =
  | { status: "loading" }
  | { status: "ready"; health: Health }
  | { status: "error"; message: string };

function useHealth(pollMs = 10000): HealthState {
  const [state, setState] = useState<HealthState>({ status: "loading" });

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const health = await fetchHealth();
        if (alive) setState({ status: "ready", health });
      } catch (err) {
        if (alive) {
          setState({
            status: "error",
            message: err instanceof Error ? err.message : "未知錯誤",
          });
        }
      }
    };
    void load();
    const id = setInterval(() => void load(), pollMs);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [pollMs]);

  return state;
}

type ChipStatus = "loading" | "ready" | "down" | "error";

const CHIP_META: Record<ChipStatus, { kind: string; label: string }> = {
  loading: { kind: "unknown", label: "查詢中" },
  ready: { kind: "ready", label: "就緒" },
  down: { kind: "down", label: "未就緒" },
  error: { kind: "down", label: "連線失敗" },
};

function serviceStatus(state: HealthState, svc: ServiceKey): ChipStatus {
  if (state.status === "error") return "error";
  if (state.status === "loading") return "loading";
  return state.health[svc].ready ? "ready" : "down";
}

function ServiceChip({ name, status }: { name: string; status: ChipStatus }) {
  const { kind, label } = CHIP_META[status];
  return (
    <span className={`chip chip--${kind}`}>
      <span className="chip__dot" aria-hidden="true" />
      {name} · {label}
    </span>
  );
}

function StatusStrip({ state }: { state: HealthState }) {
  return (
    <div className="status-strip" role="status">
      <ServiceChip name="ASR" status={serviceStatus(state, "asr")} />
      <ServiceChip name="TTS" status={serviceStatus(state, "tts")} />
      {state.status === "error" && (
        <span className="status-strip__error">健康檢查連線失敗：{state.message}</span>
      )}
    </div>
  );
}

function SectionPanel({ section, state }: { section: SectionKey; state: HealthState }) {
  const meta = SECTIONS[section];
  const health = state.status === "ready" ? state.health : null;
  const blocking = findBlockingService(section, health);

  return (
    <section className="panel">
      <h2 className="panel__title">{meta.label}</h2>
      <p className="panel__note">此區塊功能於後續實作票交付；目前為 walking skeleton 佔位。</p>
      {blocking && (
        <p className="panel__warn">
          {blocking.toUpperCase()} 服務尚未就緒，已停用送出以避免無效操作。
        </p>
      )}
      <button className="btn" type="button" disabled={blocking !== null}>
        送出
      </button>
    </section>
  );
}

export function App() {
  const state = useHealth();
  const [active, setActive] = useState<SectionKey>("hotwords");

  return (
    <div className="app">
      <header className="app__header">
        <div className="brand">
          <span className="brand__mark" aria-hidden="true">◑</span>
          Vibe-Vox 主控台
        </div>
        <StatusStrip state={state} />
      </header>

      <nav className="tabs" aria-label="功能區塊">
        {SECTION_ORDER.map((key) => (
          <button
            key={key}
            type="button"
            className={key === active ? "tab tab--active" : "tab"}
            aria-current={key === active}
            onClick={() => setActive(key)}
          >
            {SECTIONS[key].label}
          </button>
        ))}
      </nav>

      <main className="app__main">
        {active === "hotwords" ? (
          <HotwordsPanel />
        ) : active === "asr" ? (
          <AsrPanel health={state.status === "ready" ? state.health : null} />
        ) : active === "voices" ? (
          <VoicesPanel />
        ) : active === "tts" ? (
          <TtsPanel health={state.status === "ready" ? state.health : null} />
        ) : (
          <SectionPanel section={active} state={state} />
        )}
      </main>
    </div>
  );
}
