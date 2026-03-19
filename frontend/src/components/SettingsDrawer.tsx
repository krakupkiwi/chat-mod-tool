/**
 * SettingsDrawer — slide-in panel for runtime configuration.
 *
 * Reads current settings from GET /api/config and patches via PATCH /api/config.
 * All changes take effect immediately on the backend.
 *
 * Sections:
 *   - Safety: dry-run toggle, auto-timeout, auto-ban
 *   - Thresholds: alert / timeout / ban confidence levels
 *   - Storage: DB size, per-table auto-purge retention, manual purge
 *   - Threat Panel: show live/history, history window, sort order
 *   - Whitelist: add/remove usernames from protection list (stored in DB)
 */

import { memo, useEffect, useRef, useState, useCallback } from 'react';
import { RegexFilterPanel } from './RegexFilterPanel';
import { useThreatPrefs } from '../hooks/useThreatPrefs';
import type { ThreatSortBy, ThreatSortDir } from '../hooks/useThreatPrefs';
import { useChatStore } from '../store/chatStore';

interface WhitelistEntry {
  username: string;
  added_at: number;
  note: string;
}

interface Config {
  dry_run: boolean;
  auto_timeout_enabled: boolean;
  auto_ban_enabled: boolean;
  timeout_threshold: number;
  ban_threshold: number;
  alert_threshold: number;
  default_channel: string;
  message_retention_days: number;
  health_history_retention_days: number;
  flagged_users_retention_days: number;
  moderation_actions_retention_days: number;
}

interface Props {
  port: number;
  ipcSecret: string;
  open: boolean;
  onClose: () => void;
}

function Toggle({
  label,
  description,
  value,
  onChange,
  danger,
}: {
  label: string;
  description: string;
  value: boolean;
  onChange: (v: boolean) => void;
  danger?: boolean;
}) {
  return (
    <label className="flex items-start justify-between gap-4 py-2.5 border-b border-surface-3 last:border-0 cursor-pointer">
      <div className="flex-1">
        <div className={`text-sm font-medium ${danger ? 'text-red-400' : 'text-gray-200'}`}>{label}</div>
        <div className="text-xs text-gray-500 mt-0.5">{description}</div>
      </div>
      <button
        role="switch"
        aria-checked={value}
        onClick={() => onChange(!value)}
        className={`relative shrink-0 w-10 h-5 rounded-full transition-colors mt-0.5 ${
          value ? (danger ? 'bg-red-600' : 'bg-accent-purple') : 'bg-surface-3'
        }`}
      >
        <span
          className={`absolute top-0.5 left-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform ${
            value ? 'translate-x-5' : 'translate-x-0'
          }`}
        />
      </button>
    </label>
  );
}

function SliderField({
  label,
  value,
  min,
  max,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  onChange: (v: number) => void;
}) {
  return (
    <div className="py-2.5 border-b border-surface-3 last:border-0">
      <div className="flex items-center justify-between mb-1.5">
        <span className="text-sm text-gray-200">{label}</span>
        <span className="text-sm font-mono text-accent-purple">{value}</span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full h-1 bg-surface-3 rounded appearance-none cursor-pointer accent-[#9147ff]"
      />
      <div className="flex justify-between text-xs text-gray-600 mt-0.5">
        <span>{min}</span>
        <span>{max}</span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Whitelist editor
// ---------------------------------------------------------------------------

function WhitelistEditor({ port, ipcSecret, open }: { port: number; ipcSecret: string; open: boolean }) {
  const [entries, setEntries] = useState<WhitelistEntry[]>([]);
  const [input, setInput] = useState('');
  const [note, setNote] = useState('');
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(() => {
    fetch(`http://127.0.0.1:${port}/api/config/whitelist`, {
      headers: { 'X-IPC-Secret': ipcSecret },
    })
      .then((r) => r.json())
      .then((data: WhitelistEntry[]) => setEntries(data))
      .catch(() => {/* ignore */});
  }, [port, ipcSecret]);

  useEffect(() => {
    if (open) load();
  }, [open, load]);

  async function handleAdd() {
    const username = input.trim().replace(/^#/, '').toLowerCase();
    if (!username) return;
    setAdding(true);
    setError('');
    try {
      const res = await fetch(`http://127.0.0.1:${port}/api/config/whitelist`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-IPC-Secret': ipcSecret },
        body: JSON.stringify({ username, note }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setError(body.detail ?? 'Failed to add');
      } else {
        setInput('');
        setNote('');
        load();
      }
    } catch {
      setError('Network error');
    } finally {
      setAdding(false);
    }
  }

  async function handleRemove(username: string) {
    try {
      await fetch(
        `http://127.0.0.1:${port}/api/config/whitelist/${encodeURIComponent(username)}`,
        { method: 'DELETE', headers: { 'X-IPC-Secret': ipcSecret } }
      );
      setEntries((prev) => prev.filter((e) => e.username !== username));
    } catch {/* ignore */}
  }

  return (
    <div className="mt-4">
      <div className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
        Protected accounts
      </div>
      <p className="text-xs text-gray-600 mb-2">
        These usernames are never flagged or actioned, regardless of score.
        Mods, VIPs, and 60-day subscribers are always protected automatically.
      </p>

      {/* Add row */}
      <div className="flex gap-1.5 mb-1">
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') handleAdd(); }}
          placeholder="username"
          className="flex-1 min-w-0 bg-surface-2 border border-surface-3 rounded px-2 py-1 text-xs text-white placeholder-gray-600 focus:outline-none focus:border-accent-purple"
        />
        <input
          type="text"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') handleAdd(); }}
          placeholder="note (optional)"
          className="w-24 bg-surface-2 border border-surface-3 rounded px-2 py-1 text-xs text-white placeholder-gray-600 focus:outline-none focus:border-accent-purple"
        />
        <button
          onClick={handleAdd}
          disabled={adding || !input.trim()}
          className="text-xs px-2 py-1 bg-accent-purple/20 hover:bg-accent-purple/30 border border-accent-purple/40 text-accent-purple rounded disabled:opacity-40 transition-colors shrink-0"
        >
          Add
        </button>
      </div>
      {error && <div className="text-xs text-red-400 mb-1">{error}</div>}

      {/* Entry list */}
      {entries.length === 0 ? (
        <div className="text-xs text-gray-600 py-2 text-center">No entries yet</div>
      ) : (
        <div className="border border-surface-3 rounded overflow-hidden">
          {entries.map((e) => (
            <div
              key={e.username}
              className="flex items-center gap-2 px-2 py-1.5 border-b border-surface-3 last:border-0 hover:bg-surface-2 group"
            >
              <span className="flex-1 text-xs text-gray-200 font-mono truncate">{e.username}</span>
              {e.note && (
                <span className="text-xs text-gray-600 truncate max-w-[60px]" title={e.note}>
                  {e.note}
                </span>
              )}
              <button
                onClick={() => handleRemove(e.username)}
                className="text-gray-600 hover:text-red-400 text-xs leading-none opacity-0 group-hover:opacity-100 transition-opacity shrink-0"
                title="Remove from whitelist"
              >
                ✕
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Simulator panel
// ---------------------------------------------------------------------------

const SCENARIO_LABELS: Record<string, string> = {
  normal_chat:    'Normal Chat',
  spam_flood:     'Spam Flood',
  bot_raid:       'Bot Raid',
  '5000_mpm_mixed': '5K msg/min Mixed',
};

interface SimStatus {
  state: 'idle' | 'running';
  scenario: string;
  elapsed: number;
  duration: number;
}

function SimulatorPanel({ port, ipcSecret, open }: { port: number; ipcSecret: string; open: boolean }) {
  const [status, setStatus] = useState<SimStatus>({ state: 'idle', scenario: '', elapsed: 0, duration: 120 });
  const [scenario, setScenario] = useState('bot_raid');
  const [duration, setDuration] = useState(120);
  const [rate, setRate] = useState(1.0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchStatus = useCallback(() => {
    fetch(`http://127.0.0.1:${port}/api/simulator/status`, {
      headers: { 'X-IPC-Secret': ipcSecret },
    })
      .then((r) => r.json())
      .then((d: SimStatus) => setStatus(d))
      .catch(() => {/* ignore */});
  }, [port, ipcSecret]);

  // Poll while drawer is open
  useEffect(() => {
    if (!open) {
      if (pollRef.current) clearInterval(pollRef.current);
      return;
    }
    fetchStatus();
    pollRef.current = setInterval(fetchStatus, 1500);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [open, fetchStatus]);

  async function handleStart() {
    setBusy(true);
    setError('');
    try {
      const res = await fetch(`http://127.0.0.1:${port}/api/simulator/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-IPC-Secret': ipcSecret },
        body: JSON.stringify({ scenario, duration, rate }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setError(body.detail ?? 'Failed to start');
      } else {
        const d: SimStatus = await res.json();
        setStatus(d);
      }
    } catch {
      setError('Network error');
    } finally {
      setBusy(false);
    }
  }

  async function handleStop() {
    setBusy(true);
    try {
      await fetch(`http://127.0.0.1:${port}/api/simulator/stop`, {
        method: 'POST',
        headers: { 'X-IPC-Secret': ipcSecret },
      });
      setStatus((prev) => ({ ...prev, state: 'idle', elapsed: 0 }));
    } catch {/* ignore */} finally {
      setBusy(false);
    }
  }

  const running = status.state === 'running';
  const progress = running && status.duration > 0
    ? Math.min(status.elapsed / status.duration, 1)
    : 0;

  return (
    <div className="mt-4">
      <div className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
        Simulator
      </div>
      <p className="text-xs text-gray-600 mb-3">
        Inject synthetic bot traffic into the live pipeline to test detection.
        All injected messages are processed as real chat — dry-run mode still applies.
      </p>

      {/* Scenario picker */}
      <div className="mb-2.5">
        <label className="text-xs text-gray-400 block mb-1">Scenario</label>
        <select
          value={scenario}
          onChange={(e) => setScenario(e.target.value)}
          disabled={running}
          className="w-full bg-surface-2 border border-surface-3 rounded px-2 py-1.5 text-xs text-white
                     focus:outline-none focus:border-accent-purple disabled:opacity-50 cursor-pointer"
        >
          {Object.entries(SCENARIO_LABELS).map(([key, label]) => (
            <option key={key} value={key}>{label}</option>
          ))}
        </select>
      </div>

      {/* Duration slider */}
      <div className="py-2 border-b border-surface-3">
        <div className="flex items-center justify-between mb-1">
          <span className="text-xs text-gray-400">Duration</span>
          <span className="text-xs font-mono text-accent-purple">{duration}s</span>
        </div>
        <input
          type="range" min={30} max={600} step={30}
          value={duration}
          onChange={(e) => setDuration(Number(e.target.value))}
          disabled={running}
          className="w-full h-1 bg-surface-3 rounded appearance-none cursor-pointer accent-[#9147ff] disabled:opacity-50"
        />
        <div className="flex justify-between text-xs text-gray-600 mt-0.5">
          <span>30s</span><span>600s</span>
        </div>
      </div>

      {/* Rate slider */}
      <div className="py-2 border-b border-surface-3">
        <div className="flex items-center justify-between mb-1">
          <span className="text-xs text-gray-400">Rate multiplier</span>
          <span className="text-xs font-mono text-accent-purple">{rate.toFixed(2)}×</span>
        </div>
        <input
          type="range" min={25} max={500} step={25}
          value={Math.round(rate * 100)}
          onChange={(e) => setRate(Number(e.target.value) / 100)}
          disabled={running}
          className="w-full h-1 bg-surface-3 rounded appearance-none cursor-pointer accent-[#9147ff] disabled:opacity-50"
        />
        <div className="flex justify-between text-xs text-gray-600 mt-0.5">
          <span>0.25×</span><span>5×</span>
        </div>
      </div>

      {/* Progress bar (shown while running) */}
      {running && (
        <div className="mt-2.5 mb-1">
          <div className="flex justify-between text-xs text-gray-500 mb-1">
            <span className="text-green-400 font-medium">
              Running: {SCENARIO_LABELS[status.scenario] ?? status.scenario}
            </span>
            <span>{Math.round(status.elapsed)}s / {status.duration}s</span>
          </div>
          <div className="h-1 bg-surface-3 rounded overflow-hidden">
            <div
              className="h-full bg-accent-purple transition-all duration-500 rounded"
              style={{ width: `${Math.round(progress * 100)}%` }}
            />
          </div>
        </div>
      )}

      {error && <div className="text-xs text-red-400 mt-1 mb-1">{error}</div>}

      {/* Run / Stop button */}
      <button
        onClick={running ? handleStop : handleStart}
        disabled={busy}
        className={`mt-2.5 w-full py-1.5 rounded text-xs font-medium transition-colors disabled:opacity-50 ${
          running
            ? 'bg-red-900/40 hover:bg-red-900/60 border border-red-700/50 text-red-300'
            : 'bg-accent-purple/20 hover:bg-accent-purple/30 border border-accent-purple/40 text-accent-purple'
        }`}
      >
        {busy ? '…' : running ? 'Stop Simulation' : 'Run Simulation'}
      </button>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────────
// Storage settings
// ──────────────────────────────────────────────────────────────────────────────

const MSG_RETENTION_OPTS   = [1, 3, 7, 14, 30].map((d) => ({ label: `${d}d`, value: d }));
const HEALTH_RETENTION_OPTS = [7, 14, 30, 60, 90].map((d) => ({ label: `${d}d`, value: d }));
const LONG_RETENTION_OPTS  = [
  { label: 'Off', value: 0 },
  { label: '30d', value: 30 },
  { label: '60d', value: 60 },
  { label: '90d', value: 90 },
  { label: '180d', value: 180 },
  { label: '1yr', value: 365 },
];
const PURGE_AGE_OPTS = [
  { label: '7 days',   value: 7   },
  { label: '14 days',  value: 14  },
  { label: '30 days',  value: 30  },
  { label: '60 days',  value: 60  },
  { label: '90 days',  value: 90  },
  { label: '1 year',   value: 365 },
  { label: 'All',      value: 0   },
];

function fmtBytes(b: number) {
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
  return `${(b / (1024 * 1024)).toFixed(2)} MB`;
}

function PillGroup<T extends number>({
  options,
  value,
  onChange,
}: {
  options: { label: string; value: T }[];
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <div className="flex flex-wrap gap-1 mt-1">
      {options.map((o) => (
        <button
          key={o.value}
          onClick={() => onChange(o.value)}
          className={`px-2 py-0.5 rounded text-[10px] transition-colors ${
            value === o.value
              ? 'bg-accent-purple text-white'
              : 'bg-surface-3 text-gray-400 hover:text-gray-200'
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

function SimPurgeButton({
  port,
  ipcSecret,
  onSizeUpdate,
}: {
  port: number;
  ipcSecret: string;
  onSizeUpdate: (bytes: number) => void;
}) {
  const [state, setState] = useState<'idle' | 'purging' | 'done'>('idle');
  const [deleted, setDeleted] = useState<Record<string, number> | null>(null);
  const bumpDataRefreshKey = useChatStore((s) => s.bumpDataRefreshKey);
  const clearAlerts = useChatStore((s) => s.clearAlerts);

  async function purge() {
    setState('purging');
    try {
      const res = await fetch(`http://127.0.0.1:${port}/api/data/purge_sim`, {
        method: 'POST',
        headers: { 'X-IPC-Secret': ipcSecret },
      });
      const data = await res.json();
      setDeleted(data.deleted ?? {});
      if (data.db_size_bytes) onSizeUpdate(data.db_size_bytes);
      bumpDataRefreshKey();
      clearAlerts();
      setState('done');
    } catch {
      setState('idle');
    }
  }

  return (
    <div className="mt-3 pt-3 border-t border-surface-3">
      <div className="text-[10px] text-gray-500 mb-1.5">Simulator data</div>
      <div className="text-[10px] text-gray-600 mb-2">
        Purge all data tagged <code className="text-gray-500">__sim__</code> — injected during test runs.
      </div>
      {deleted && (
        <div className="text-[10px] text-green-400 mb-1.5">
          Removed: {Object.entries(deleted).filter(([, n]) => n > 0).map(([t, n]) => `${n} ${t.replace('_', ' ')}`).join(', ') || 'nothing to purge'}
        </div>
      )}
      <button
        onClick={purge}
        disabled={state === 'purging'}
        className="px-3 py-1.5 bg-surface-3 hover:bg-yellow-950/40 hover:text-yellow-300 disabled:opacity-40 text-gray-400 rounded text-xs transition-colors border border-transparent hover:border-yellow-800/50"
      >
        {state === 'purging' ? 'Purging…' : 'Purge simulated data'}
      </button>
    </div>
  );
}

interface StorageProps {
  port: number;
  ipcSecret: string;
  config: Config;
  update: (key: keyof Config, value: unknown) => void;
  open: boolean;
}

function StorageSettings({ port, ipcSecret, config, update, open }: StorageProps) {
  const [dbSize, setDbSize] = useState<number | null>(null);
  const [purgeAge, setPurgeAge] = useState(30);
  const [purgeTables, setPurgeTables] = useState<Set<string>>(
    new Set(['flagged_users', 'moderation_actions'])
  );
  const [purging, setPurging] = useState(false);
  const [purgeResult, setPurgeResult] = useState<Record<string, number> | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const bumpDataRefreshKey = useChatStore((s) => s.bumpDataRefreshKey);
  const clearAlerts = useChatStore((s) => s.clearAlerts);

  useEffect(() => {
    if (!open) return;
    fetch(`http://127.0.0.1:${port}/api/data/info`, { headers: { 'X-IPC-Secret': ipcSecret } })
      .then((r) => r.json())
      .then((d) => setDbSize(d.db_size_bytes ?? null))
      .catch(() => {});
  }, [open, port, ipcSecret]);

  function toggleTable(t: string) {
    setPurgeTables((prev) => {
      const next = new Set(prev);
      if (next.has(t)) next.delete(t); else next.add(t);
      return next;
    });
  }

  async function runPurge() {
    if (purgeTables.size === 0) return;
    setPurging(true);
    setPurgeResult(null);
    try {
      const params = new URLSearchParams({
        older_than_days: String(purgeAge),
        tables: [...purgeTables].join(','),
      });
      const res = await fetch(`http://127.0.0.1:${port}/api/data/purge?${params}`, {
        method: 'POST',
        headers: { 'X-IPC-Secret': ipcSecret },
      });
      const data = await res.json();
      setPurgeResult(data.deleted ?? {});
      if (data.db_size_bytes) setDbSize(data.db_size_bytes);
      bumpDataRefreshKey();
      clearAlerts();
    } finally {
      setPurging(false);
      setConfirmOpen(false);
    }
  }

  const TABLE_LABELS: Record<string, string> = {
    messages: 'Messages',
    flagged_users: 'Flagged users',
    moderation_actions: 'Mod actions',
    health_history: 'Health history',
  };

  return (
    <div className="mt-4">
      <div className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">Storage</div>

      {/* DB size */}
      <div className="py-2 border-b border-surface-3">
        <div className="flex items-center justify-between">
          <span className="text-sm text-gray-300">Database size</span>
          <span className="font-mono text-sm text-gray-200">
            {dbSize != null ? fmtBytes(dbSize) : '…'}
          </span>
        </div>
        <div className="text-xs text-gray-600 mt-0.5">
          Located at <code className="text-gray-500">%APPDATA%\TwitchIDS\data.db</code>
        </div>
      </div>

      {/* Auto-retention settings */}
      <div className="py-2 border-b border-surface-3 space-y-2">
        <div className="text-xs text-gray-500 mb-1">Auto-purge (runs daily at startup)</div>

        <div>
          <div className="text-xs text-gray-400">Messages</div>
          <PillGroup
            options={MSG_RETENTION_OPTS}
            value={config.message_retention_days as any}
            onChange={(v) => update('message_retention_days', v)}
          />
        </div>
        <div>
          <div className="text-xs text-gray-400">Health history</div>
          <PillGroup
            options={HEALTH_RETENTION_OPTS}
            value={config.health_history_retention_days as any}
            onChange={(v) => update('health_history_retention_days', v)}
          />
        </div>
        <div>
          <div className="text-xs text-gray-400">Flagged users <span className="text-gray-600">(Off = keep forever)</span></div>
          <PillGroup
            options={LONG_RETENTION_OPTS}
            value={config.flagged_users_retention_days as any}
            onChange={(v) => update('flagged_users_retention_days', v)}
          />
        </div>
        <div>
          <div className="text-xs text-gray-400">Mod actions <span className="text-gray-600">(Off = keep forever)</span></div>
          <PillGroup
            options={LONG_RETENTION_OPTS}
            value={config.moderation_actions_retention_days as any}
            onChange={(v) => update('moderation_actions_retention_days', v)}
          />
        </div>
      </div>

      {/* Manual purge */}
      <div className="py-2">
        <div className="text-xs text-gray-500 mb-2">Manual purge</div>

        <div className="text-[10px] text-gray-500 mb-1">Delete data older than <span className="text-gray-600">(All = wipe completely)</span></div>
        <PillGroup options={PURGE_AGE_OPTS} value={purgeAge as any} onChange={(v) => setPurgeAge(v)} />

        <div className="text-[10px] text-gray-500 mt-2 mb-1">Tables to purge</div>
        <div className="flex flex-wrap gap-1.5">
          <button
            onClick={() => {
              const allKeys = Object.keys(TABLE_LABELS);
              const allSelected = allKeys.every((k) => purgeTables.has(k));
              setPurgeTables(allSelected ? new Set() : new Set(allKeys));
            }}
            className={`px-2 py-0.5 rounded text-[10px] border transition-colors ${
              Object.keys(TABLE_LABELS).every((k) => purgeTables.has(k))
                ? 'border-red-700 bg-red-950/40 text-red-300'
                : 'border-surface-3 text-gray-500 hover:text-gray-300'
            }`}
          >
            All
          </button>
          {Object.entries(TABLE_LABELS).map(([key, label]) => (
            <button
              key={key}
              onClick={() => toggleTable(key)}
              className={`px-2 py-0.5 rounded text-[10px] border transition-colors ${
                purgeTables.has(key)
                  ? 'border-red-700 bg-red-950/40 text-red-300'
                  : 'border-surface-3 text-gray-600 hover:text-gray-400'
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        {purgeResult && (
          <div className="mt-2 text-[10px] text-green-400">
            Purged: {Object.entries(purgeResult).map(([t, n]) => `${n} ${TABLE_LABELS[t] ?? t}`).join(', ')}
          </div>
        )}

        {confirmOpen ? (
          <div className="mt-2 p-2 bg-red-950/40 border border-red-800/50 rounded text-xs text-red-300 space-y-2">
            <div>
              Delete <strong>{[...purgeTables].map((t) => TABLE_LABELS[t]).join(', ')}</strong>{' '}
              {purgeAge === 0
                ? <><strong>entirely</strong> (all rows)</>
                : <>older than <strong>{purgeAge} days</strong></>
              }? This cannot be undone.
            </div>
            <div className="flex gap-2">
              <button
                onClick={runPurge}
                disabled={purging}
                className="px-3 py-1 bg-red-700 hover:bg-red-600 disabled:opacity-50 text-white rounded text-xs"
              >
                {purging ? 'Purging…' : 'Confirm purge'}
              </button>
              <button
                onClick={() => setConfirmOpen(false)}
                className="px-3 py-1 bg-surface-3 hover:bg-surface-2 text-gray-300 rounded text-xs"
              >
                Cancel
              </button>
            </div>
          </div>
        ) : (
          <button
            onClick={() => { setPurgeResult(null); setConfirmOpen(true); }}
            disabled={purgeTables.size === 0}
            className="mt-2 px-3 py-1.5 bg-surface-3 hover:bg-red-950/40 hover:text-red-300 disabled:opacity-40 text-gray-400 rounded text-xs transition-colors border border-transparent hover:border-red-800/50"
          >
            Purge now…
          </button>
        )}

        <SimPurgeButton port={port} ipcSecret={ipcSecret} onSizeUpdate={setDbSize} />
      </div>
    </div>
  );
}

const AGE_OPTIONS: { label: string; value: number }[] = [
  { label: '3 days',  value: 3  },
  { label: '7 days',  value: 7  },
  { label: '14 days', value: 14 },
  { label: '30 days', value: 30 },
  { label: 'All time', value: 0  },
];

const SORT_BY_OPTIONS: { label: string; value: ThreatSortBy }[] = [
  { label: 'Threat score', value: 'score'     },
  { label: 'Time',         value: 'age'       },
  { label: 'Times flagged', value: 'flagCount' },
];

function ThreatPanelSettings() {
  const { prefs, update } = useThreatPrefs();

  return (
    <div className="mt-4">
      <div className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">Threat Panel</div>

      <Toggle
        label="Show live alerts"
        description="Threats detected in the current session."
        value={prefs.showLive}
        onChange={(v) => update('showLive', v)}
      />
      <Toggle
        label="Show historical threats"
        description="Threats from previous sessions stored in the database."
        value={prefs.showHistory}
        onChange={(v) => update('showHistory', v)}
      />

      {prefs.showHistory && (
        <div className="py-2.5 border-b border-surface-3">
          <div className="text-sm font-medium text-gray-200 mb-1.5">History window</div>
          <div className="flex flex-wrap gap-1.5">
            {AGE_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                onClick={() => update('maxAgeDays', opt.value)}
                className={`px-2 py-1 rounded text-xs transition-colors ${
                  prefs.maxAgeDays === opt.value
                    ? 'bg-accent-purple text-white'
                    : 'bg-surface-3 text-gray-400 hover:text-gray-200'
                }`}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="py-2.5 border-b border-surface-3">
        <div className="text-sm font-medium text-gray-200 mb-1.5">Sort by</div>
        <div className="flex gap-1.5">
          {SORT_BY_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              onClick={() => update('sortBy', opt.value)}
              className={`px-2 py-1 rounded text-xs transition-colors ${
                prefs.sortBy === opt.value
                  ? 'bg-accent-purple text-white'
                  : 'bg-surface-3 text-gray-400 hover:text-gray-200'
              }`}
            >
              {opt.label}
            </button>
          ))}
        </div>
        <div className="flex gap-1.5 mt-1.5">
          {([['desc', 'High → Low'], ['asc', 'Low → High']] as [ThreatSortDir, string][]).map(([dir, label]) => (
            <button
              key={dir}
              onClick={() => update('sortDir', dir)}
              className={`px-2 py-1 rounded text-xs transition-colors ${
                prefs.sortDir === dir
                  ? 'bg-surface-3 text-gray-200'
                  : 'text-gray-600 hover:text-gray-400'
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

export const SettingsDrawer = memo(function SettingsDrawer({ port, ipcSecret, open, onClose }: Props) {
  const [config, setConfig] = useState<Config | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const pendingRef = useRef<Partial<Config>>({});
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const overlayRef = useRef<HTMLDivElement>(null);

  // Load config when drawer opens
  useEffect(() => {
    if (!open) return;
    fetch(`http://127.0.0.1:${port}/api/config`, {
      headers: { 'X-IPC-Secret': ipcSecret },
    })
      .then((r) => r.json())
      .then(setConfig)
      .catch(() => {/* ignore */});
  }, [open, port, ipcSecret]);

  // Debounced save
  function scheduleSave(patch: Partial<Config>) {
    pendingRef.current = { ...pendingRef.current, ...patch };
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(async () => {
      setSaving(true);
      try {
        const res = await fetch(`http://127.0.0.1:${port}/api/config`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json', 'X-IPC-Secret': ipcSecret },
          body: JSON.stringify(pendingRef.current),
        });
        if (res.ok) {
          const updated = await res.json();
          setConfig(updated);
          setSaved(true);
          setTimeout(() => setSaved(false), 1500);
        }
      } catch {/* ignore */} finally {
        setSaving(false);
        pendingRef.current = {};
      }
    }, 600);
  }

  function update<K extends keyof Config>(key: K, value: Config[K]) {
    if (!config) return;
    const patch = { [key]: value } as Partial<Config>;
    setConfig((prev) => prev ? { ...prev, ...patch } : prev);
    scheduleSave(patch);
  }

  // Close on overlay click
  function handleOverlayClick(e: React.MouseEvent) {
    if (e.target === overlayRef.current) onClose();
  }

  if (!open) return null;

  return (
    <div
      ref={overlayRef}
      onClick={handleOverlayClick}
      className="fixed inset-0 z-50 bg-black/50 flex justify-end"
    >
      <div className="w-80 h-full bg-surface-1 border-l border-surface-3 flex flex-col shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-surface-3 shrink-0">
          <span className="text-sm font-semibold text-gray-200">Settings</span>
          <div className="flex items-center gap-2">
            {saving && <span className="text-xs text-gray-500">Saving…</span>}
            {saved && <span className="text-xs text-green-400">Saved</span>}
            <button
              onClick={onClose}
              className="text-gray-500 hover:text-gray-200 text-lg leading-none"
              aria-label="Close settings"
            >
              ×
            </button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto px-4 py-2">
          {config === null ? (
            <div className="text-xs text-gray-500 mt-4">Loading…</div>
          ) : (
            <>
              {/* Safety */}
              <div className="text-xs font-semibold text-gray-500 uppercase tracking-wide mt-2 mb-1">Safety</div>

              <Toggle
                label="Dry-run mode"
                description="All actions are logged but never sent to Twitch. Safe default."
                value={config.dry_run}
                onChange={(v) => update('dry_run', v)}
              />
              <Toggle
                label="Auto-timeout"
                description="Automatically timeout users who exceed the timeout threshold."
                value={config.auto_timeout_enabled}
                onChange={(v) => update('auto_timeout_enabled', v)}
                danger={!config.dry_run}
              />
              <Toggle
                label="Auto-ban"
                description="Permanently ban users with two independent signals both > 90. Requires dry-run OFF."
                value={config.auto_ban_enabled}
                onChange={(v) => update('auto_ban_enabled', v)}
                danger={!config.dry_run}
              />

              {/* Thresholds */}
              <div className="text-xs font-semibold text-gray-500 uppercase tracking-wide mt-4 mb-1">Thresholds</div>

              <SliderField
                label="Alert threshold"
                value={Math.round(config.alert_threshold)}
                min={20}
                max={80}
                onChange={(v) => update('alert_threshold', v)}
              />
              <SliderField
                label="Timeout threshold"
                value={Math.round(config.timeout_threshold)}
                min={50}
                max={95}
                onChange={(v) => update('timeout_threshold', v)}
              />
              <SliderField
                label="Ban threshold"
                value={Math.round(config.ban_threshold)}
                min={80}
                max={100}
                onChange={(v) => update('ban_threshold', v)}
              />

              {/* Warning when live moderation is active */}
              {!config.dry_run && (config.auto_timeout_enabled || config.auto_ban_enabled) && (
                <div className="mt-3 px-3 py-2 bg-red-950/50 border border-red-800/50 rounded text-xs text-red-300">
                  Live moderation is active. Actions will be sent to Twitch.
                </div>
              )}

              {/* Storage */}
              <StorageSettings port={port} ipcSecret={ipcSecret} config={config} update={update} open={open} />

              {/* Threat Panel */}
              <ThreatPanelSettings />

              {/* Whitelist */}
              <WhitelistEditor port={port} ipcSecret={ipcSecret} open={open} />

              {/* Regex Filters */}
              <RegexFilterPanel port={port} ipcSecret={ipcSecret} open={open} />

              {/* Simulator */}
              <SimulatorPanel port={port} ipcSecret={ipcSecret} open={open} />
            </>
          )}
        </div>
      </div>
    </div>
  );
});
