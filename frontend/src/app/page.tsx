"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  ArrowDownRight,
  ArrowUpRight,
  Bell,
  BellOff,
  Clock,
  Radio,
  RefreshCw,
  Send,
  TrendingUp,
  Wifi,
  WifiOff,
  Zap,
} from "lucide-react";

type ConnectionState = "connecting" | "open" | "closed";

type AlertPayload = {
  id: string;
  type: "alert";
  symbol: string;
  timeframe: string;
  direction: "LONG" | "SHORT";
  createdAt: string;
  sweepTime: string;
  liquidityTime: string;
  liquidityLevel: number;
  closePrice: number;
  sweepExtreme: number;
  emaAligned?: boolean;
  barsBetween?: number;
  debug?: boolean;
};

type MarketStatus = {
  state: string;
  timeframe?: string;
  historyCandles?: number;
  lastClosedCandle?: string;
  updatedAt?: string;
  message?: string;
};

type SocketMessage =
  | AlertPayload
  | {
      type: "snapshot";
      alerts: AlertPayload[];
      status: Record<string, MarketStatus>;
      hourlySymbols: string[];
      dailySymbolCount: number;
    }
  | { type: "status"; status: Record<string, MarketStatus> }
  | { type: "pong"; at: string };

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";
const WS_URL = process.env.NEXT_PUBLIC_WS_URL ?? "ws://127.0.0.1:8000/ws/alerts";

function formatTime(value?: string) {
  if (!value) return "Waiting";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  try {
    return new Intl.DateTimeFormat("tr-TR", {
      month: "short",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      timeZone: "Europe/Istanbul",
    }).format(date);
  } catch (err) {
    return date.toLocaleString("tr-TR");
  }
}

function formatPrice(value: number) {
  if (value >= 100) {
    return value.toLocaleString(undefined, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
  }
  if (value >= 1) {
    return value.toLocaleString(undefined, {
      minimumFractionDigits: 4,
      maximumFractionDigits: 4,
    });
  }
  return value.toLocaleString(undefined, {
    minimumFractionDigits: 6,
    maximumFractionDigits: 6,
  });
}

function connectionCopy(state: ConnectionState) {
  if (state === "open") return "Connected";
  if (state === "connecting") return "Connecting";
  return "Reconnecting";
}

function statusTone(state?: string) {
  if (state === "online") return "bg-emerald-400";
  if (state === "error") return "bg-rose-400";
  return "bg-amber-300";
}

type FilterTab = "all" | "1d" | "1h";

export default function Home() {
  const [connectionState, setConnectionState] = useState<ConnectionState>("connecting");
  const [alerts, setAlerts] = useState<AlertPayload[]>([]);
  const [status, setStatus] = useState<Record<string, MarketStatus>>({});
  const [hourlySymbols, setHourlySymbols] = useState<string[]>([]);
  const [dailyCount, setDailyCount] = useState(0);
  const [soundEnabled, setSoundEnabled] = useState(false);
  const [lastMessageAt, setLastMessageAt] = useState<string>();
  const [filterTab, setFilterTab] = useState<FilterTab>("all");
  const audioContextRef = useRef<AudioContext | null>(null);
  const soundEnabledRef = useRef(false);

  useEffect(() => {
    soundEnabledRef.current = soundEnabled;
  }, [soundEnabled]);

  const statusEntries = useMemo(
    () => Object.entries(status).sort(([a], [b]) => a.localeCompare(b)),
    [status],
  );

  const onlineCount = statusEntries.filter(([, v]) => v.state === "online").length;

  const filteredAlerts = useMemo(() => {
    if (filterTab === "all") return alerts;
    return alerts.filter((a) => a.timeframe === filterTab);
  }, [alerts, filterTab]);

  const latestAlert = alerts[0];

  function playAlertTone() {
    const ctx = audioContextRef.current;
    if (!ctx || ctx.state !== "running") return;
    const t = ctx.currentTime;
    const gain = ctx.createGain();
    gain.gain.setValueAtTime(0.001, t);
    gain.gain.exponentialRampToValueAtTime(0.18, t + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.001, t + 0.42);
    gain.connect(ctx.destination);
    [740, 980].forEach((freq, i) => {
      const osc = ctx.createOscillator();
      osc.type = "sine";
      osc.frequency.setValueAtTime(freq, t + i * 0.12);
      osc.connect(gain);
      osc.start(t + i * 0.12);
      osc.stop(t + i * 0.12 + 0.18);
    });
  }

  async function toggleSound() {
    if (soundEnabled) {
      setSoundEnabled(false);
      return;
    }
    const Ctor =
      window.AudioContext ||
      (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    if (!audioContextRef.current) audioContextRef.current = new Ctor();
    await audioContextRef.current.resume();
    setSoundEnabled(true);
    window.setTimeout(playAlertTone, 50);
  }

  async function sendTestAlert() {
    await fetch(`${API_URL}/debug/test-alert`, { method: "POST" });
  }

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    let ws: WebSocket | undefined;
    let alive = true;

    function connect() {
      setConnectionState("connecting");
      try {
        ws = new WebSocket(WS_URL);
      } catch (err) {
        console.error("WebSocket failed:", err);
        setConnectionState("closed");
        if (alive) timer = setTimeout(connect, 5000);
        return;
      }

      ws.onopen = () => {
        setConnectionState("open");
        ws?.send("ping");
      };

      ws.onmessage = (ev) => {
        const msg = JSON.parse(ev.data) as SocketMessage;
        setLastMessageAt(new Date().toISOString());

        if (msg.type === "snapshot") {
          setAlerts([...msg.alerts].reverse());
          setStatus(msg.status ?? {});
          setHourlySymbols(msg.hourlySymbols ?? []);
          setDailyCount(msg.dailySymbolCount ?? 0);
          return;
        }
        if (msg.type === "status") {
          setStatus(msg.status ?? {});
          return;
        }
        if (msg.type === "alert") {
          setAlerts((cur) => [msg, ...cur].slice(0, 100));
          if (soundEnabledRef.current) playAlertTone();
        }
      };

      ws.onclose = () => {
        setConnectionState("closed");
        if (alive) timer = setTimeout(connect, 2500);
      };
      ws.onerror = () => ws?.close();
    }

    connect();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
      ws?.close();
    };
  }, []);

  return (
    <main className="min-h-screen bg-[#0c0c0d] text-zinc-100">
      <div className="mx-auto flex w-full max-w-7xl flex-col gap-5 px-4 py-4 sm:px-6 lg:px-8">
        <header className="flex flex-col gap-3 border-b border-zinc-800 pb-4 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <div className="flex items-center gap-2 text-sm font-medium text-emerald-300">
              <Radio className="h-4 w-4" />
              KuCoin Futures — Dual Monitor (1D + 1H)
            </div>
            <h1 className="mt-1 text-2xl font-semibold text-zinc-50 sm:text-3xl">
              Liquidity Sweep Alerts
            </h1>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <div className="flex h-10 items-center gap-2 rounded-md border border-zinc-800 bg-zinc-950 px-3 text-sm text-zinc-200">
              {connectionState === "open" ? (
                <Wifi className="h-4 w-4 text-emerald-300" />
              ) : (
                <WifiOff className="h-4 w-4 text-amber-300" />
              )}
              {connectionCopy(connectionState)}
            </div>
            <button
              className="flex h-10 items-center gap-2 rounded-md border border-zinc-800 bg-zinc-950 px-3 text-sm text-zinc-100 transition hover:border-zinc-600"
              onClick={toggleSound}
              title={soundEnabled ? "Mute alert sound" : "Enable alert sound"}
              type="button"
            >
              {soundEnabled ? (
                <Bell className="h-4 w-4 text-emerald-300" />
              ) : (
                <BellOff className="h-4 w-4 text-zinc-400" />
              )}
              Sound
            </button>
            <button
              className="flex h-10 items-center gap-2 rounded-md border border-emerald-500/40 bg-emerald-500/10 px-3 text-sm text-emerald-100 transition hover:border-emerald-300"
              onClick={sendTestAlert}
              title="Send test alert"
              type="button"
            >
              <Send className="h-4 w-4" />
              Test alert
            </button>
          </div>
        </header>

        <section className="grid gap-3 md:grid-cols-4">
          <div className="rounded-md border border-zinc-800 bg-[#141416] p-4">
            <div className="flex items-center justify-between text-sm text-zinc-400">
              <span>Markets online</span>
              <Activity className="h-4 w-4 text-emerald-300" />
            </div>
            <div className="mt-3 text-2xl font-semibold text-zinc-50">
              {onlineCount}/{statusEntries.length || 0}
            </div>
          </div>
          <div className="rounded-md border border-zinc-800 bg-[#141416] p-4">
            <div className="flex items-center justify-between text-sm text-zinc-400">
              <span>1D Coins</span>
              <Clock className="h-4 w-4 text-sky-300" />
            </div>
            <div className="mt-3 text-2xl font-semibold text-zinc-50">
              {dailyCount}
            </div>
          </div>
          <div className="rounded-md border border-zinc-800 bg-[#141416] p-4">
            <div className="flex items-center justify-between text-sm text-zinc-400">
              <span>1H Coins</span>
              <Clock className="h-4 w-4 text-amber-300" />
            </div>
            <div className="mt-3 text-2xl font-semibold text-zinc-50">
              {hourlySymbols.length}
            </div>
          </div>
          <div className="rounded-md border border-zinc-800 bg-[#141416] p-4">
            <div className="flex items-center justify-between text-sm text-zinc-400">
              <span>Session alerts</span>
              <Zap className="h-4 w-4 text-amber-300" />
            </div>
            <div className="mt-3 text-2xl font-semibold text-zinc-50">{alerts.length}</div>
          </div>
        </section>

        <section className="grid gap-5 lg:grid-cols-[1.35fr_0.65fr]">
          <div className="rounded-md border border-zinc-800 bg-[#141416]">
            <div className="flex items-center justify-between border-b border-zinc-800 px-4 py-3">
              <div>
                <h2 className="text-base font-semibold text-zinc-50">Alert Feed</h2>
                <p className="text-sm text-zinc-400">Liquidity sweep confirmations</p>
              </div>
              <div className="flex items-center gap-1">
                {(["all", "1d", "1h"] as FilterTab[]).map((tab) => (
                  <button
                    key={tab}
                    onClick={() => setFilterTab(tab)}
                    className={`rounded-md px-3 py-1.5 text-xs font-medium uppercase transition ${
                      filterTab === tab
                        ? "bg-zinc-700 text-zinc-50"
                        : "text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200"
                    }`}
                    type="button"
                  >
                    {tab === "all" ? "All" : tab}
                  </button>
                ))}
              </div>
            </div>

            <div className="max-h-[620px] overflow-auto p-3">
              {filteredAlerts.length === 0 ? (
                <div className="flex min-h-80 items-center justify-center rounded-md border border-dashed border-zinc-700 bg-zinc-950 text-sm text-zinc-400">
                  No alerts received in this session
                </div>
              ) : (
                <div className="flex flex-col gap-3">
                  {filteredAlerts.map((alert) => {
                    const isLong = alert.direction === "LONG";
                    return (
                      <article
                        className={`rounded-md border p-4 ${
                          isLong
                            ? "border-emerald-500/20 bg-emerald-500/[0.03]"
                            : "border-rose-500/20 bg-rose-500/[0.03]"
                        }`}
                        key={alert.id}
                      >
                        <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                          <div>
                            <div className="flex flex-wrap items-center gap-2">
                              <span className="text-lg font-semibold text-zinc-50">
                                {alert.symbol}
                              </span>
                              {isLong ? (
                                <span className="flex items-center gap-1 rounded-md border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 text-xs font-medium uppercase text-emerald-300">
                                  <ArrowUpRight className="h-3 w-3" />
                                  Long
                                </span>
                              ) : (
                                <span className="flex items-center gap-1 rounded-md border border-rose-500/30 bg-rose-500/10 px-2 py-0.5 text-xs font-medium uppercase text-rose-300">
                                  <ArrowDownRight className="h-3 w-3" />
                                  Short
                                </span>
                              )}
                              <span className="rounded-md border border-zinc-700 bg-zinc-800/50 px-2 py-0.5 text-xs font-medium uppercase text-zinc-300">
                                {alert.timeframe}
                              </span>
                              {alert.emaAligned && (
                                <span className="rounded-md border border-purple-500/30 bg-purple-500/10 px-2 py-0.5 text-xs font-medium uppercase text-purple-300">
                                  EMA 200
                                </span>
                              )}
                              {alert.debug ? (
                                <span className="rounded-md border border-amber-400/30 bg-amber-400/10 px-2 py-0.5 text-xs font-medium text-amber-200">
                                  Test
                                </span>
                              ) : null}
                            </div>
                            <div className="mt-1 text-sm text-zinc-400">
                              Confirmed {formatTime(alert.sweepTime)}
                              {alert.barsBetween ? (
                                <span className="ml-2 text-zinc-500">
                                  ({alert.barsBetween} bars)
                                </span>
                              ) : null}
                            </div>
                          </div>
                          <div className="text-left sm:text-right">
                            <div className="text-sm text-zinc-400">Close</div>
                            <div
                              className={`font-mono text-lg ${
                                isLong ? "text-emerald-300" : "text-rose-300"
                              }`}
                            >
                              {formatPrice(alert.closePrice)}
                            </div>
                          </div>
                        </div>

                        <dl className="mt-4 grid gap-2 sm:grid-cols-3">
                          <div className="rounded-md border border-zinc-800 bg-[#151519] p-3">
                            <dt className="text-xs text-zinc-500">
                              {isLong ? "Sellside Liquidity" : "Buyside Liquidity"}
                            </dt>
                            <dd className="mt-1 font-mono text-sm text-zinc-100">
                              {formatPrice(alert.liquidityLevel)}
                            </dd>
                          </div>
                          <div className="rounded-md border border-zinc-800 bg-[#151519] p-3">
                            <dt className="text-xs text-zinc-500">
                              {isLong ? "Sweep Low" : "Sweep High"}
                            </dt>
                            <dd className="mt-1 font-mono text-sm text-zinc-100">
                              {formatPrice(alert.sweepExtreme)}
                            </dd>
                          </div>
                          <div className="rounded-md border border-zinc-800 bg-[#151519] p-3">
                            <dt className="text-xs text-zinc-500">Liquidity Time</dt>
                            <dd className="mt-1 text-sm text-zinc-100">
                              {formatTime(alert.liquidityTime)}
                            </dd>
                          </div>
                        </dl>
                      </article>
                    );
                  })}
                </div>
              )}
            </div>
          </div>

          <aside className="rounded-md border border-zinc-800 bg-[#141416]">
            <div className="border-b border-zinc-800 px-4 py-3">
              <h2 className="text-base font-semibold text-zinc-50">Market Status</h2>
              <p className="text-sm text-zinc-400">Dual scanner (1D + 1H)</p>
            </div>
            <div className="divide-y divide-zinc-800 max-h-[620px] overflow-y-auto custom-scrollbar">
              {statusEntries.length === 0 ? (
                <div className="p-4 text-sm text-zinc-400">Waiting for backend status</div>
              ) : (
                statusEntries.map(([key, item]) => (
                  <div className="p-4" key={key}>
                    <div className="flex items-center justify-between gap-3">
                      <div className="flex min-w-0 items-center gap-2">
                        <span
                          className={`h-2.5 w-2.5 shrink-0 rounded-full ${statusTone(
                            item.state,
                          )}`}
                        />
                        <span className="truncate font-medium text-zinc-100">{key}</span>
                      </div>
                      <span className="rounded-md border border-zinc-700 px-2 py-0.5 text-xs uppercase text-zinc-300">
                        {item.state}
                      </span>
                    </div>
                    <div className="mt-3 grid grid-cols-2 gap-2 text-sm">
                      <div>
                        <div className="text-zinc-500">Candles</div>
                        <div className="font-mono text-zinc-200">
                          {item.historyCandles ?? "n/a"}
                        </div>
                      </div>
                      <div>
                        <div className="text-zinc-500">Last close</div>
                        <div className="text-zinc-200">{formatTime(item.lastClosedCandle)}</div>
                      </div>
                    </div>
                    {item.message ? (
                      <div className="mt-2 text-sm text-amber-200">{item.message}</div>
                    ) : null}
                  </div>
                ))
              )}
            </div>
          </aside>
        </section>

        {latestAlert ? (
          <div
            className={`rounded-md border px-4 py-3 text-sm ${
              latestAlert.direction === "LONG"
                ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-200"
                : "border-rose-500/30 bg-rose-500/10 text-rose-200"
            }`}
          >
            Latest: {latestAlert.symbol}{" "}
            <span className="font-semibold">{latestAlert.direction}</span>{" "}
            sweep at{" "}
            <span className="font-mono">{formatPrice(latestAlert.liquidityLevel)}</span>{" "}
            → closed{" "}
            <span className="font-mono">{formatPrice(latestAlert.closePrice)}</span>{" "}
            ({formatTime(latestAlert.sweepTime)})
          </div>
        ) : null}
      </div>
    </main>
  );
}
