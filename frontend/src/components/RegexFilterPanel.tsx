/**
 * RegexFilterPanel — manage local regex-based message filters.
 *
 * Used inside SettingsDrawer. Lets mods create regex rules that automatically
 * delete, timeout, or flag messages matching a pattern — filling the gap that
 * Twitch's AutoMod (literal-only) leaves.
 *
 * Includes a test mode: run a pattern against the last 5 minutes of messages
 * and preview matches before saving.
 */

import { useCallback, useEffect, useState } from 'react';

interface RegexFilter {
  id: number;
  pattern: string;
  flags: string;
  action_type: string;
  duration_seconds: number | null;
  note: string;
  enabled: number;
  match_count: number;
  created_at: number;
}

interface TestMatch {
  username: string;
  text: string;
  received_at: number;
}

interface Props {
  port: number;
  ipcSecret: string;
  open: boolean;
}

const ACTION_LABELS: Record<string, string> = {
  delete: 'Delete',
  timeout: 'Timeout',
  flag: 'Flag only',
};

export function RegexFilterPanel({ port, ipcSecret, open }: Props) {
  const [filters, setFilters] = useState<RegexFilter[]>([]);
  const [showAdd, setShowAdd] = useState(false);

  const fetchFilters = useCallback(async () => {
    try {
      const res = await fetch(`http://127.0.0.1:${port}/api/filters/regex`, {
        headers: { 'X-IPC-Secret': ipcSecret },
      });
      if (res.ok) {
        const data = await res.json();
        setFilters(data.filters ?? []);
      }
    } catch { /* ignore */ }
  }, [port, ipcSecret]);

  useEffect(() => {
    if (open) fetchFilters();
  }, [open, fetchFilters]);

  async function deleteFilter(id: number) {
    try {
      await fetch(`http://127.0.0.1:${port}/api/filters/regex/${id}`, {
        method: 'DELETE',
        headers: { 'X-IPC-Secret': ipcSecret },
      });
      setFilters((f) => f.filter((x) => x.id !== id));
    } catch { /* ignore */ }
  }

  async function toggleEnabled(filter: RegexFilter) {
    try {
      await fetch(`http://127.0.0.1:${port}/api/filters/regex/${filter.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json', 'X-IPC-Secret': ipcSecret },
        body: JSON.stringify({ enabled: !filter.enabled }),
      });
      setFilters((f) =>
        f.map((x) => (x.id === filter.id ? { ...x, enabled: filter.enabled ? 0 : 1 } : x))
      );
    } catch { /* ignore */ }
  }

  return (
    <div className="mt-4">
      <div className="flex items-center justify-between mb-2">
        <div className="text-xs font-semibold text-gray-500 uppercase tracking-wide">Regex Filters</div>
        <button
          onClick={() => setShowAdd((v) => !v)}
          className="text-[10px] text-accent-purple hover:text-white transition-colors"
        >
          {showAdd ? 'Cancel' : '+ Add'}
        </button>
      </div>

      {showAdd && (
        <AddFilterForm
          port={port}
          ipcSecret={ipcSecret}
          onCreated={() => { setShowAdd(false); fetchFilters(); }}
        />
      )}

      {filters.length === 0 && !showAdd && (
        <div className="text-xs text-gray-600 py-2">No regex filters configured.</div>
      )}

      <div className="space-y-1.5">
        {filters.map((f) => (
          <FilterRow
            key={f.id}
            filter={f}
            onDelete={() => deleteFilter(f.id)}
            onToggle={() => toggleEnabled(f)}
          />
        ))}
      </div>
    </div>
  );
}

function FilterRow({
  filter,
  onDelete,
  onToggle,
}: {
  filter: RegexFilter;
  onDelete: () => void;
  onToggle: () => void;
}) {
  const enabled = Boolean(filter.enabled);
  return (
    <div className={`border rounded px-2 py-1.5 text-xs transition-opacity ${
      enabled ? 'border-surface-3 bg-surface-2' : 'border-surface-3/40 bg-surface-2/40 opacity-50'
    }`}>
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-gray-200 truncate flex-1">{filter.pattern}</span>
        <div className="flex items-center gap-1.5 shrink-0">
          <span className="text-[10px] text-gray-500">{ACTION_LABELS[filter.action_type] ?? filter.action_type}</span>
          <button
            onClick={onToggle}
            className={`text-[10px] px-1.5 py-0.5 rounded border ${
              enabled
                ? 'border-green-700/50 text-green-400 hover:border-red-700/50 hover:text-red-400'
                : 'border-surface-3 text-gray-600 hover:text-green-400'
            }`}
          >
            {enabled ? 'On' : 'Off'}
          </button>
          <button
            onClick={onDelete}
            className="text-gray-600 hover:text-red-400 text-[10px]"
          >
            ✕
          </button>
        </div>
      </div>
      {filter.note && (
        <div className="text-[10px] text-gray-600 mt-0.5 truncate">{filter.note}</div>
      )}
      {filter.match_count > 0 && (
        <div className="text-[10px] text-gray-600 mt-0.5">{filter.match_count} matches</div>
      )}
    </div>
  );
}

function AddFilterForm({
  port,
  ipcSecret,
  onCreated,
}: {
  port: number;
  ipcSecret: string;
  onCreated: () => void;
}) {
  const [pattern, setPattern] = useState('');
  const [action, setAction] = useState('delete');
  const [duration, setDuration] = useState('');
  const [note, setNote] = useState('');
  const [patternError, setPatternError] = useState('');
  const [saving, setSaving] = useState(false);
  const [testResults, setTestResults] = useState<TestMatch[] | null>(null);
  const [testing, setTesting] = useState(false);

  function validatePattern(p: string): boolean {
    try { new RegExp(p); setPatternError(''); return true; }
    catch (e) { setPatternError(String(e)); return false; }
  }

  async function testPattern() {
    if (!validatePattern(pattern)) return;
    setTesting(true);
    setTestResults(null);
    try {
      const res = await fetch(`http://127.0.0.1:${port}/api/filters/regex/test`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-IPC-Secret': ipcSecret },
        body: JSON.stringify({ pattern, flags: 'i', lookback_seconds: 300 }),
      });
      if (res.ok) {
        const data = await res.json();
        setTestResults(data.matches ?? []);
      }
    } catch { /* ignore */ }
    setTesting(false);
  }

  async function save() {
    if (!validatePattern(pattern) || !pattern.trim()) return;
    setSaving(true);
    try {
      const body: Record<string, unknown> = {
        pattern: pattern.trim(),
        flags: 'i',
        action_type: action,
        note: note.trim(),
      };
      if (action === 'timeout' && duration) {
        body.duration_seconds = parseInt(duration, 10);
      }
      const res = await fetch(`http://127.0.0.1:${port}/api/filters/regex`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-IPC-Secret': ipcSecret },
        body: JSON.stringify(body),
      });
      if (res.ok) onCreated();
    } catch { /* ignore */ }
    setSaving(false);
  }

  return (
    <div className="mb-3 p-2.5 border border-surface-3 rounded bg-surface-2 space-y-2">
      <div>
        <input
          type="text"
          value={pattern}
          onChange={(e) => { setPattern(e.target.value); validatePattern(e.target.value); }}
          placeholder="Regex pattern (e.g. (?i)free.?nitro)"
          className="w-full text-xs bg-surface font-mono border border-surface-3 rounded px-2 py-1 text-gray-200 focus:outline-none focus:border-accent-purple placeholder:text-gray-600"
        />
        {patternError && <div className="text-[10px] text-red-400 mt-0.5">{patternError}</div>}
      </div>

      <div className="flex items-center gap-2">
        <select
          value={action}
          onChange={(e) => setAction(e.target.value)}
          className="text-xs bg-surface border border-surface-3 rounded px-1.5 py-0.5 text-gray-300 focus:outline-none"
        >
          <option value="delete">Delete message</option>
          <option value="timeout">Timeout user</option>
          <option value="flag">Flag only</option>
        </select>
        {action === 'timeout' && (
          <input
            type="number"
            value={duration}
            onChange={(e) => setDuration(e.target.value)}
            placeholder="Secs"
            min={1}
            max={1209600}
            className="text-xs bg-surface border border-surface-3 rounded px-1.5 py-0.5 text-gray-300 w-16 focus:outline-none"
          />
        )}
      </div>

      <input
        type="text"
        value={note}
        onChange={(e) => setNote(e.target.value)}
        placeholder="Note (optional)"
        className="w-full text-xs bg-surface border border-surface-3 rounded px-2 py-1 text-gray-400 focus:outline-none focus:border-accent-purple placeholder:text-gray-600"
      />

      {/* Test results */}
      {testResults !== null && (
        <div className="border border-surface-3 rounded p-2 max-h-28 overflow-y-auto">
          {testResults.length === 0 ? (
            <div className="text-[10px] text-gray-600">No matches in last 5 minutes</div>
          ) : (
            <>
              <div className="text-[10px] text-yellow-400 mb-1">{testResults.length} match(es) in last 5 min:</div>
              {testResults.slice(0, 10).map((m, i) => (
                <div key={i} className="text-[10px] text-gray-400 truncate">
                  <span className="text-gray-500 font-mono">{m.username}:</span> {m.text}
                </div>
              ))}
            </>
          )}
        </div>
      )}

      <div className="flex items-center gap-2">
        <button
          onClick={testPattern}
          disabled={testing || !pattern.trim()}
          className="text-[10px] px-2 py-1 border border-surface-3 text-gray-400 hover:text-gray-200 rounded disabled:opacity-40"
        >
          {testing ? 'Testing…' : 'Test'}
        </button>
        <button
          onClick={save}
          disabled={saving || !pattern.trim() || !!patternError}
          className="text-[10px] px-2 py-1 bg-accent-purple/20 border border-accent-purple/40 text-accent-purple hover:bg-accent-purple/30 rounded disabled:opacity-40"
        >
          {saving ? 'Saving…' : 'Save Filter'}
        </button>
      </div>
    </div>
  );
}
