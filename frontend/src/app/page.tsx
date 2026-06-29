"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
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
  createdAt: string;
  sweepTime: string;
  breakdownTime: string;
  liquidityTime: string;
  swingLowTime: string;
  liquidityLevel: number;
  breakdownLevel: number;
  sweepHigh: number;
  breakdownClose: number;
  emaAligned?: boolean;
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
      symbols: string[];
      timeframe: string;
    }
  | { type: "status"; status: Record<string, MarketStatus> }
  | { type: "pong"; at: string };

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";
const WS_URL = process.env.NEXT_PUBLIC_WS_URL ?? "ws://127.0.0.1:8000/ws/alerts";

const timeFormatter = new Intl.DateTimeFormat(undefined, {
  month: "short",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
});

function formatTime(value?: string) {
  if (!value) return "Waiting";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return timeFormatter.format(date);
}

function formatPrice(value: number) {
  if (value >= 100) {
    return value.toLocaleString(undefined, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
  }
  return value.toLocaleString(undefined, {
    minimumFractionDigits: 4,
    maximumFractionDigits: 4,
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

export default function Home() {
  const [connectionState, setConnectionState] = useState<ConnectionState>("connecting");
  const [alerts, setAlerts] = useState<AlertPayload[]>([]);
  const [status, setStatus] = useState<Record<string, MarketStatus>>({});
  const [symbols, setSymbols] = useState<string[]>([]);
  const [timeframe, setTimeframe] = useState("1h");
  const [soundEnabled, setSoundEnabled] = useState(false);
  const [lastMessageAt, setLastMessageAt] = useState<string>();
  const audioContextRef = useRef<AudioContext | null>(null);
  const soundEnabledRef = useRef(false);

  useEffect(() => {
    soundEnabledRef.current = soundEnabled;
  }, [soundEnabled]);

  const statusEntries = useMemo(
    () => Object.entries(status).sort(([left], [right]) => left.localeCompare(right)),
    [status],
  );

  const onlineCount = statusEntries.filter(([, value]) => value.state === "online").length;
  const latestAlert = alerts[0];

  function playAlertTone() {
    const audioContext = audioContextRef.current;
    if (!audioContext || audioContext.state !== "running") return;

    const startedAt = audioContext.currentTime;
    const gain = audioContext.createGain();
    gain.gain.setValueAtTime(0.001, startedAt);
    gain.gain.exponentialRampToValueAtTime(0.18, startedAt + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.001, startedAt + 0.42);
    gain.connect(audioContext.destination);

    [740, 980].forEach((frequency, index) => {
      const oscillator = audioContext.createOscillator();
      oscillator.type = "sine";
      oscillator.frequency.setValueAtTime(frequency, startedAt + index * 0.12);
      oscillator.connect(gain);
      oscillator.start(startedAt + index * 0.12);
      oscillator.stop(startedAt + index * 0.12 + 0.18);
    });
  }

  async function toggleSound() {
    if (soundEnabled) {
      setSoundEnabled(false);
      return;
    }

    const AudioContextCtor =
      window.AudioContext ||
      (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    if (!audioContextRef.current) {
      audioContextRef.current = new AudioContextCtor();
    }
    await audioContextRef.current.resume();
    setSoundEnabled(true);
    window.setTimeout(playAlertTone, 50);
  }

  async function sendTestAlert() {
    await fetch(`${API_URL}/debug/test-alert`, { method: "POST" });
  }

  useEffect(() => {
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined;
    let socket: WebSocket | undefined;
    let shouldReconnect = true;

    function connect() {
      setConnectionState("connecting");
      socket = new WebSocket(WS_URL);

      socket.onopen = () => {
        setConnectionState("open");
        socket?.send("ping");
      };

      socket.onmessage = (event) => {
        const message = JSON.parse(event.data) as SocketMessage;
        setLastMessageAt(new Date().toISOString());

        if (message.type === "snapshot") {
          setAlerts([...message.alerts].reverse());
          setStatus(message.status ?? {});
          setSymbols(message.symbols ?? []);
          setTimeframe(message.timeframe ?? "1h");
          return;
        }

        if (message.type === "status") {
          setStatus(message.status ?? {});
          return;
        }

        if (message.type === "alert") {
          setAlerts((current) => [message, ...current].slice(0, 80));
          if (soundEnabledRef.current) playAlertTone();
        }
      };

      socket.onclose = () => {
        setConnectionState("closed");
        if (shouldReconnect) {
          reconnectTimer = setTimeout(connect, 2500);
        }
      };

      socket.onerror = () => {
        socket?.close();
      };
    }

    connect();

    return () => {
      shouldReconnect = false;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      socket?.close();
    };
  }, []);

  return (
    <main className="min-h-screen bg-[#0c0c0d] text-zinc-100">
      <div className="mx-auto flex w-full max-w-7xl flex-col gap-5 px-4 py-4 sm:px-6 lg:px-8">
        <header className="flex flex-col gap-3 border-b border-zinc-800 pb-4 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <div className="flex items-center gap-2 text-sm font-medium text-emerald-300">
              <Radio className="h-4 w-4" />
              Binance 1H Monitor
            </div>
            <h1 className="mt-1 text-2xl font-semibold text-zinc-50 sm:text-3xl">
              Trading Alerts
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
              {onlineCount}/{statusEntries.length || symbols.length || 0}
            </div>
          </div>
          <div className="rounded-md border border-zinc-800 bg-[#141416] p-4">
            <div className="flex items-center justify-between text-sm text-zinc-400">
              <span>Timeframe</span>
              <Clock className="h-4 w-4 text-sky-300" />
            </div>
            <div className="mt-3 text-2xl font-semibold uppercase text-zinc-50">
              {timeframe}
            </div>
          </div>
          <div className="rounded-md border border-zinc-800 bg-[#141416] p-4">
            <div className="flex items-center justify-between text-sm text-zinc-400">
              <span>Session alerts</span>
              <Zap className="h-4 w-4 text-amber-300" />
            </div>
            <div className="mt-3 text-2xl font-semibold text-zinc-50">{alerts.length}</div>
          </div>
          <div className="rounded-md border border-zinc-800 bg-[#141416] p-4">
            <div className="flex items-center justify-between text-sm text-zinc-400">
              <span>Last message</span>
              <RefreshCw className="h-4 w-4 text-zinc-300" />
            </div>
            <div className="mt-3 text-lg font-semibold text-zinc-50">
              {formatTime(lastMessageAt)}
            </div>
          </div>
        </section>

        <section className="grid gap-5 lg:grid-cols-[1.35fr_0.65fr]">
          <div className="rounded-md border border-zinc-800 bg-[#141416]">
            <div className="flex items-center justify-between border-b border-zinc-800 px-4 py-3">
              <div>
                <h2 className="text-base font-semibold text-zinc-50">Alert Feed</h2>
                <p className="text-sm text-zinc-400">Short setup confirmations</p>
              </div>
              <TrendingUp className="h-5 w-5 text-rose-400" />
            </div>

            <div className="max-h-[620px] overflow-auto p-3">
              {alerts.length === 0 ? (
                <div className="flex min-h-80 items-center justify-center rounded-md border border-dashed border-zinc-700 bg-zinc-950 text-sm text-zinc-400">
                  No alerts received in this session
                </div>
              ) : (
                <div className="flex flex-col gap-3">
                  {alerts.map((alert) => (
                    <article
                      className="rounded-md border border-zinc-800 bg-zinc-950 p-4"
                      key={alert.id}
                    >
                      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                        <div>
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="text-lg font-semibold text-zinc-50">
                              {alert.symbol}
                            </span>
                            <span className="rounded-md border border-rose-500/30 bg-rose-500/10 px-2 py-0.5 text-xs font-medium uppercase text-rose-300">
                              Short
                            </span>
                            {alert.emaAligned && (
                              <span className="rounded-md border border-purple-500/30 bg-purple-500/10 px-2 py-0.5 text-xs font-medium uppercase text-purple-300">
                                EMA 200 Aligned
                              </span>
                            )}
                            {alert.debug ? (
                              <span className="rounded-md border border-amber-400/30 bg-amber-400/10 px-2 py-0.5 text-xs font-medium text-amber-200">
                                Test
                              </span>
                            ) : null}
                          </div>
                          <div className="mt-1 text-sm text-zinc-400">
                            Confirmed {formatTime(alert.breakdownTime)}
                          </div>
                        </div>
                        <div className="text-left sm:text-right">
                          <div className="text-sm text-zinc-400">Break close</div>
                          <div className="font-mono text-lg text-rose-300">
                            {formatPrice(alert.breakdownClose)}
                          </div>
                        </div>
                      </div>

                      <dl className="mt-4 grid gap-2 sm:grid-cols-4">
                        <div className="rounded-md border border-zinc-800 bg-[#151519] p-3">
                          <dt className="text-xs text-zinc-500">Liquidity</dt>
                          <dd className="mt-1 font-mono text-sm text-zinc-100">
                            {formatPrice(alert.liquidityLevel)}
                          </dd>
                        </div>
                        <div className="rounded-md border border-zinc-800 bg-[#151519] p-3">
                          <dt className="text-xs text-zinc-500">Sweep high (Stop)</dt>
                          <dd className="mt-1 font-mono text-sm text-zinc-100">
                            {formatPrice(alert.sweepHigh)}
                          </dd>
                        </div>
                        <div className="rounded-md border border-zinc-800 bg-[#151519] p-3">
                          <dt className="text-xs text-zinc-500">Break level</dt>
                          <dd className="mt-1 font-mono text-sm text-zinc-100">
                            {formatPrice(alert.breakdownLevel)}
                          </dd>
                        </div>
                        <div className="rounded-md border border-zinc-800 bg-[#151519] p-3">
                          <dt className="text-xs text-zinc-500">Sweep time</dt>
                          <dd className="mt-1 text-sm text-zinc-100">
                            {formatTime(alert.sweepTime)}
                          </dd>
                        </div>
                      </dl>
                    </article>
                  ))}
                </div>
              )}
            </div>
          </div>

          <aside className="rounded-md border border-zinc-800 bg-[#141416]">
            <div className="border-b border-zinc-800 px-4 py-3">
              <h2 className="text-base font-semibold text-zinc-50">Market Status</h2>
              <p className="text-sm text-zinc-400">Closed-candle scanner</p>
            </div>
            <div className="divide-y divide-zinc-800 max-h-[620px] overflow-y-auto custom-scrollbar">
              {statusEntries.length === 0 ? (
                <div className="p-4 text-sm text-zinc-400">Waiting for backend status</div>
              ) : (
                statusEntries.map(([symbol, item]) => (
                  <div className="p-4" key={symbol}>
                    <div className="flex items-center justify-between gap-3">
                      <div className="flex min-w-0 items-center gap-2">
                        <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${statusTone(item.state)}`} />
                        <span className="truncate font-medium text-zinc-100">{symbol}</span>
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
          <div className="rounded-md border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-200">
            Latest: {latestAlert.symbol} confirmed below{" "}
            <span className="font-mono">{formatPrice(latestAlert.breakdownLevel)}</span> at{" "}
            {formatTime(latestAlert.breakdownTime)}
          </div>
        ) : null}
      </div>
    </main>
  );
}
