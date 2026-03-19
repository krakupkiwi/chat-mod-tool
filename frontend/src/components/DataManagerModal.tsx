/**
 * DataManagerModal — database info, per-channel export, and import.
 *
 * Tabs:
 *   Info    — DB file path, size, row counts per channel per table
 *   Export  — channel filter, date range, dataset, format (CSV/JSON), download
 *   Import  — watchlist CSV import; links to ban-list importer
 */

import { useEffect, useRef, useState } from 'react';

interface Props {
  port: number;
  ipcSecret: string;
  onClose: () => void;
  onOpenBanList: () => void;
}

interface TableCount {
  channel: string;
  total: number;
  latest: number | null;
}

interface DbInfo {
  db_path: string;
  db_size_bytes: number;
  counts: {
    flagged_users: TableCount[];
    moderation_actions: TableCount[];
    messages: TableCount[];
  };
}

type Tab = 'info' | 'export' | 'import';

const DATASET_OPTIONS = [
  { value: 'flagged_users',      label: 'Flagged Users'       },
  { value: 'moderation_actions', label: 'Moderation Actions'  },
] as const;

const HOURS_OPTIONS = [
  { label: '24 hours',  value: 24    },
  { label: '7 days',   value: 168   },
  { label: '14 days',  value: 336   },
  { label: '30 days',  value: 720   },
  { label: 'All time', value: 8760  },
] as const;

function fmtBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

function fmtDate(ts: number | null): string {
  if (!ts) return '—';
  return new Date(ts * 1000).toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' });
}

// ──────────────────────────────────────────────────────────────────────────────
// Info tab
// ──────────────────────────────────────────────────────────────────────────────

function InfoTab({ info }: { info: DbInfo | null }) {
  if (!info) return <div className="text-gray-500 text-xs mt-4">Loading…</div>;

  function showInFolder() {
    (window as any).electronAPI?.showInFolder?.(info!.db_path);
  }

  const allChannels = Array.from(new Set([
    ...info.counts.flagged_users.map((r) => r.channel),
    ...info.counts.moderation_actions.map((r) => r.channel),
    ...info.counts.messages.map((r) => r.channel),
  ])).sort();

  return (
    <div className="space-y-4">
      {/* DB file location */}
      <div>
        <div className="text-[10px] text-gray-500 uppercase tracking-wider mb-1">Database file</div>
        <div className="flex items-center gap-2">
          <code className="flex-1 text-[11px] text-gray-300 bg-surface-3 rounded px-2 py-1.5 break-all">
            {info.db_path}
          </code>
          <button
            onClick={showInFolder}
            title="Show in Explorer"
            className="shrink-0 text-xs px-2 py-1.5 bg-surface-3 hover:bg-surface-2 text-gray-400 hover:text-gray-200 rounded transition-colors"
          >
            📂
          </button>
        </div>
        <div className="text-[10px] text-gray-600 mt-1">
          Size: {fmtBytes(info.db_size_bytes)} · SQLite 3 format · open with{' '}
          <span className="text-gray-500">DB Browser for SQLite</span>
        </div>
      </div>

      {/* Per-channel row counts */}
      <div>
        <div className="text-[10px] text-gray-500 uppercase tracking-wider mb-2">Data per channel</div>
        {allChannels.length === 0 ? (
          <div className="text-xs text-gray-600">No data recorded yet.</div>
        ) : (
          <table className="w-full text-xs border-collapse">
            <thead>
              <tr className="text-gray-600 text-[10px] uppercase tracking-wider border-b border-surface-3">
                <th className="text-left py-1 pr-3">Channel</th>
                <th className="text-right py-1 pr-3">Messages</th>
                <th className="text-right py-1 pr-3">Flagged</th>
                <th className="text-right py-1">Actions</th>
              </tr>
            </thead>
            <tbody>
              {allChannels.map((ch) => {
                const msgs  = info.counts.messages.find((r) => r.channel === ch);
                const flags = info.counts.flagged_users.find((r) => r.channel === ch);
                const acts  = info.counts.moderation_actions.find((r) => r.channel === ch);
                const latest = Math.max(msgs?.latest ?? 0, flags?.latest ?? 0, acts?.latest ?? 0);
                return (
                  <tr key={ch} className="border-b border-surface-3/50 hover:bg-surface-3/20">
                    <td className="py-1.5 pr-3 font-mono text-accent-purple">#{ch}</td>
                    <td className="py-1.5 pr-3 text-right text-gray-300">{msgs?.total?.toLocaleString() ?? '—'}</td>
                    <td className="py-1.5 pr-3 text-right text-gray-300">{flags?.total?.toLocaleString() ?? '—'}</td>
                    <td className="py-1.5 text-right text-gray-300">{acts?.total?.toLocaleString() ?? '—'}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      <div className="text-[10px] text-gray-600 pt-1 border-t border-surface-3">
        Watchlist, whitelist, and reputation data are shared across all channels.
      </div>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────────
// Export tab
// ──────────────────────────────────────────────────────────────────────────────

function ExportTab({
  port,
  ipcSecret,
  channels,
}: {
  port: number;
  ipcSecret: string;
  channels: string[];
}) {
  const [dataset, setDataset] = useState<'flagged_users' | 'moderation_actions'>('flagged_users');
  const [channel, setChannel] = useState<string>('');
  const [hours, setHours] = useState<number>(168);
  const [fmt, setFmt] = useState<'csv' | 'json'>('csv');
  const [downloading, setDownloading] = useState(false);

  async function download() {
    setDownloading(true);
    try {
      const params = new URLSearchParams({ hours: String(hours), fmt });
      if (channel) params.set('channel', channel);
      const url = `http://127.0.0.1:${port}/api/stats/export/${dataset}?${params}`;
      const res = await fetch(url, { headers: { 'X-IPC-Secret': ipcSecret } });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const blob = await res.blob();
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `${dataset}${channel ? `_${channel}` : ''}.${fmt}`;
      a.click();
      URL.revokeObjectURL(a.href);
    } catch (err) {
      console.error('Export failed', err);
    } finally {
      setDownloading(false);
    }
  }

  return (
    <div className="space-y-4">
      {/* Dataset */}
      <div>
        <div className="text-[10px] text-gray-500 uppercase tracking-wider mb-1.5">Dataset</div>
        <div className="flex gap-2">
          {DATASET_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              onClick={() => setDataset(opt.value)}
              className={`px-3 py-1.5 rounded text-xs transition-colors ${
                dataset === opt.value
                  ? 'bg-accent-purple text-white'
                  : 'bg-surface-3 text-gray-400 hover:text-gray-200'
              }`}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </div>

      {/* Channel */}
      <div>
        <div className="text-[10px] text-gray-500 uppercase tracking-wider mb-1.5">Channel</div>
        <select
          value={channel}
          onChange={(e) => setChannel(e.target.value)}
          className="w-full bg-surface-3 border border-surface-3 rounded px-2 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-accent-purple"
        >
          <option value="">All channels</option>
          {channels.map((ch) => (
            <option key={ch} value={ch}>#{ch}</option>
          ))}
        </select>
      </div>

      {/* Date range */}
      <div>
        <div className="text-[10px] text-gray-500 uppercase tracking-wider mb-1.5">Date range</div>
        <div className="flex flex-wrap gap-1.5">
          {HOURS_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              onClick={() => setHours(opt.value)}
              className={`px-2.5 py-1 rounded text-xs transition-colors ${
                hours === opt.value
                  ? 'bg-accent-purple text-white'
                  : 'bg-surface-3 text-gray-400 hover:text-gray-200'
              }`}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </div>

      {/* Format */}
      <div>
        <div className="text-[10px] text-gray-500 uppercase tracking-wider mb-1.5">Format</div>
        <div className="flex gap-2">
          {(['csv', 'json'] as const).map((f) => (
            <button
              key={f}
              onClick={() => setFmt(f)}
              className={`px-3 py-1.5 rounded text-xs transition-colors ${
                fmt === f
                  ? 'bg-accent-purple text-white'
                  : 'bg-surface-3 text-gray-400 hover:text-gray-200'
              }`}
            >
              {f.toUpperCase()}
            </button>
          ))}
        </div>
        <div className="text-[10px] text-gray-600 mt-1">
          {fmt === 'csv' ? 'Opens in Excel / Google Sheets' : 'Structured JSON — importable into other tools'}
        </div>
      </div>

      <button
        onClick={download}
        disabled={downloading}
        className="w-full py-2 bg-accent-purple hover:bg-purple-600 disabled:opacity-50 text-white text-sm rounded transition-colors font-medium"
      >
        {downloading ? 'Preparing download…' : `Download ${fmt.toUpperCase()}`}
      </button>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────────
// Import tab
// ──────────────────────────────────────────────────────────────────────────────

function ImportTab({
  port,
  ipcSecret,
  onOpenBanList,
}: {
  port: number;
  ipcSecret: string;
  onOpenBanList: () => void;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [status, setStatus] = useState<'idle' | 'loading' | 'done' | 'error'>('idle');
  const [imported, setImported] = useState(0);
  const [skipped, setSkipped] = useState(0);

  async function handleWatchlistFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setStatus('loading');
    setImported(0);
    setSkipped(0);

    try {
      const text = await file.text();
      const lines = text.split('\n').map((l) => l.trim()).filter(Boolean);
      // Expect CSV with header: user_id,username[,note[,priority]]
      // or plain list of usernames (no commas)
      let okCount = 0;
      let skipCount = 0;

      for (const line of lines) {
        if (line.toLowerCase().startsWith('user_id') || line.toLowerCase().startsWith('username')) continue;
        const parts = line.split(',').map((p) => p.trim());
        const [user_id, username, note, priority] = parts;
        if (!username && !user_id) { skipCount++; continue; }

        const body: Record<string, string> = {
          user_id: user_id || username,
          username: username || user_id,
          note: note || 'Imported from CSV',
          priority: priority === 'high' ? 'high' : 'normal',
        };

        const res = await fetch(`http://127.0.0.1:${port}/api/watchlist`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-IPC-Secret': ipcSecret },
          body: JSON.stringify(body),
        });
        if (res.ok) okCount++; else skipCount++;
      }

      setImported(okCount);
      setSkipped(skipCount);
      setStatus('done');
    } catch {
      setStatus('error');
    } finally {
      if (fileRef.current) fileRef.current.value = '';
    }
  }

  return (
    <div className="space-y-4">
      {/* Ban list import */}
      <div className="p-3 bg-surface-3/40 rounded border border-surface-3">
        <div className="text-sm font-medium text-gray-200 mb-1">Shared ban list</div>
        <div className="text-xs text-gray-500 mb-2">
          Import a newline-separated list of usernames to ban across your channel.
        </div>
        <button
          onClick={onOpenBanList}
          className="text-xs px-3 py-1.5 bg-surface-3 hover:bg-surface-2 text-gray-300 rounded transition-colors"
        >
          Open Ban List Importer
        </button>
      </div>

      {/* Watchlist CSV import */}
      <div className="p-3 bg-surface-3/40 rounded border border-surface-3">
        <div className="text-sm font-medium text-gray-200 mb-1">Watchlist (CSV)</div>
        <div className="text-xs text-gray-500 mb-2">
          Import a CSV file to add users to your watchlist. Expected columns:
        </div>
        <code className="block text-[10px] text-gray-400 bg-surface-3 rounded px-2 py-1.5 mb-3">
          user_id, username, note, priority
        </code>
        <div className="text-[10px] text-gray-600 mb-2">
          <code>priority</code> is optional — use <code>high</code> or leave blank for normal.
          <br />
          Plain username lists (one per line, no commas) are also accepted.
        </div>

        <input
          ref={fileRef}
          type="file"
          accept=".csv,.txt"
          onChange={handleWatchlistFile}
          className="hidden"
        />
        <button
          onClick={() => fileRef.current?.click()}
          disabled={status === 'loading'}
          className="text-xs px-3 py-1.5 bg-surface-3 hover:bg-surface-2 disabled:opacity-50 text-gray-300 rounded transition-colors"
        >
          {status === 'loading' ? 'Importing…' : 'Choose file…'}
        </button>

        {status === 'done' && (
          <div className="mt-2 text-xs text-green-400">
            Imported {imported} users{skipped > 0 ? `, skipped ${skipped}` : ''}.
          </div>
        )}
        {status === 'error' && (
          <div className="mt-2 text-xs text-red-400">Import failed — check the file format.</div>
        )}
      </div>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────────
// Modal shell
// ──────────────────────────────────────────────────────────────────────────────

export function DataManagerModal({ port, ipcSecret, onClose, onOpenBanList }: Props) {
  const [tab, setTab] = useState<Tab>('info');
  const [info, setInfo] = useState<DbInfo | null>(null);
  const overlayRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetch(`http://127.0.0.1:${port}/api/data/info`, {
      headers: { 'X-IPC-Secret': ipcSecret },
    })
      .then((r) => r.json())
      .then(setInfo)
      .catch(() => {});
  }, [port, ipcSecret]);

  function handleOverlay(e: React.MouseEvent) {
    if (e.target === overlayRef.current) onClose();
  }

  const allChannels = info
    ? Array.from(new Set([
        ...info.counts.flagged_users.map((r) => r.channel),
        ...info.counts.moderation_actions.map((r) => r.channel),
      ])).sort()
    : [];

  const TABS: { id: Tab; label: string }[] = [
    { id: 'info',   label: 'Info'   },
    { id: 'export', label: 'Export' },
    { id: 'import', label: 'Import' },
  ];

  return (
    <div
      ref={overlayRef}
      onClick={handleOverlay}
      className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center"
    >
      <div className="w-[480px] max-h-[80vh] bg-surface-1 border border-surface-3 rounded-lg shadow-2xl flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-surface-3 shrink-0">
          <span className="text-sm font-semibold text-gray-200">Data Manager</span>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-200 text-lg leading-none">×</button>
        </div>

        {/* Tabs */}
        <div className="flex border-b border-surface-3 shrink-0">
          {TABS.map((t) => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`flex-1 py-2 text-xs font-medium transition-colors ${
                tab === t.id
                  ? 'text-gray-200 border-b-2 border-accent-purple'
                  : 'text-gray-500 hover:text-gray-300'
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-4 py-4">
          {tab === 'info'   && <InfoTab info={info} />}
          {tab === 'export' && <ExportTab port={port} ipcSecret={ipcSecret} channels={allChannels} />}
          {tab === 'import' && (
            <ImportTab
              port={port}
              ipcSecret={ipcSecret}
              onOpenBanList={() => { onClose(); onOpenBanList(); }}
            />
          )}
        </div>
      </div>
    </div>
  );
}
