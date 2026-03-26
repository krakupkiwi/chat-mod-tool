/**
 * HistoryPage — persistent, multi-session detection history browser.
 *
 * Four sub-tabs backed by the /api/history/* endpoints:
 *   Threats      — flagged_users table
 *   Clusters     — cluster_events table (semantic + co-occurrence detections)
 *   Moderation   — moderation_actions table
 *   Escalations  — health_escalation_events table (level transitions)
 *
 * Filters match the Analytics page (time range, channel) plus per-tab extras.
 * All data survives across sessions — this is a forensic record, not a live feed.
 */

import { Fragment, useCallback, useEffect, useRef, useState } from 'react';
import { useChatStore } from '../store/chatStore';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface ThreatRow {
  id: number;
  flagged_at: number;
  user_id: string;
  username: string;
  channel: string;
  threat_score: number;
  signals: string[];
  status: 'active' | 'resolved' | 'false_positive';
  flag_count: number;
}

interface ClusterRow {
  id: number;
  detected_at: number;
  channel: string;
  cluster_id: string;
  member_count: number;
  sample_message: string | null;
  user_ids: string[];
  risk_score: number;
}

interface ModerationRow {
  id: number;
  created_at: number;
  completed_at: number | null;
  user_id: string;
  username: string;
  channel: string;
  action_type: string;
  duration_seconds: number | null;
  reason: string | null;
  status: string;
  triggered_by: string;
  confidence: number | null;
  error_message: string | null;
}

interface EscalationRow {
  id: number;
  occurred_at: number;
  channel: string;
  from_level: string;
  to_level: string;
  health_score: number;
  msg_per_min: number;
}

interface UserRow {
  user_id: string;
  username: string;
  message_count: number;
  first_seen: number;
  last_seen: number;
  is_subscriber: number;
  is_moderator: number;
  is_vip: number;
  account_age_days: number | null;
  avg_msg_length: number;
  url_msg_count: number;
  reputation: number;
  total_flags: number;
  total_actions: number;
  false_positives: number;
  recent_flags: number;
  max_threat_score: number;
  last_flagged: number | null;
  last_signals: string[];
}

interface MessageRow {
  id: number;
  received_at: number;
  channel: string;
  user_id: string;
  username: string;
  raw_text: string;
  emoji_count: number;
  url_count: number;
  word_count: number;
  char_count: number;
  has_url: number;
  is_subscriber: number;
  is_moderator: number;
  is_vip: number;
  account_age_days: number | null;
}

type HistoryTab = 'threats' | 'clusters' | 'moderation' | 'escalations' | 'users' | 'messages';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const TIME_RANGES: { label: string; hours: number }[] = [
  { label: '1h', hours: 1 },
  { label: '6h', hours: 6 },
  { label: '24h', hours: 24 },
  { label: '7d', hours: 168 },
  { label: '30d', hours: 720 },
];

function fmtTs(ts: number): string {
  return new Date(ts * 1000).toLocaleString([], {
    month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
}


const LEVEL_COLOR: Record<string, string> = {
  healthy:      'text-green-400',
  elevated:     'text-yellow-400',
  suspicious:   'text-orange-400',
  likely_attack:'text-red-400',
  critical:     'text-red-500',
};
const LEVEL_LABEL: Record<string, string> = {
  healthy: 'Healthy', elevated: 'Elevated', suspicious: 'Suspicious',
  likely_attack: 'Likely Attack', critical: 'Critical',
};

const ACTION_COLOR: Record<string, string> = {
  ban:            'text-red-400',
  timeout:        'text-orange-400',
  delete:         'text-yellow-400',
  slow_mode:      'text-blue-400',
  followers_only: 'text-cyan-400',
};

const STATUS_COLOR: Record<string, string> = {
  active:         'text-red-400',
  resolved:       'text-green-400',
  false_positive: 'text-gray-400',
  completed:      'text-green-400',
  failed:         'text-red-400',
  pending:        'text-yellow-400',
  undone:         'text-gray-400',
};

function ScoreBadge({ score }: { score: number }) {
  const cls =
    score >= 80 ? 'bg-red-900/50 text-red-300 border-red-700/50' :
    score >= 55 ? 'bg-orange-900/50 text-orange-300 border-orange-700/50' :
    'bg-yellow-900/30 text-yellow-300 border-yellow-700/30';
  return (
    <span className={`inline-flex items-center px-1.5 py-0.5 rounded border text-[10px] font-mono font-bold ${cls}`}>
      {score.toFixed(1)}
    </span>
  );
}

function SignalTags({ signals }: { signals: string[] }) {
  return (
    <div className="flex flex-wrap gap-1">
      {signals.slice(0, 4).map((s) => (
        <span key={s} className="text-[9px] bg-surface-3 text-purple-300 rounded px-1 py-0.5">
          {s}
        </span>
      ))}
      {signals.length > 4 && (
        <span className="text-[9px] text-gray-600">+{signals.length - 4}</span>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Empty / loading states
// ---------------------------------------------------------------------------

function Empty({ text }: { text: string }) {
  return (
    <div className="flex flex-col items-center justify-center flex-1 text-gray-600 text-xs gap-1 py-16">
      <span className="text-2xl opacity-30">⏳</span>
      <span>{text}</span>
    </div>
  );
}

function LoadMoreButton({ onClick, loading, hasMore }: { onClick: () => void; loading: boolean; hasMore: boolean }) {
  if (!hasMore) return null;
  return (
    <button
      onClick={onClick}
      disabled={loading}
      className="mx-auto my-3 block text-xs text-gray-500 hover:text-gray-200 disabled:opacity-40 bg-surface-2 hover:bg-surface-3 border border-surface-3 rounded px-4 py-1.5 transition-colors"
    >
      {loading ? 'Loading…' : 'Load more'}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Threats tab
// ---------------------------------------------------------------------------

function ThreatsTab({ port, ipcSecret, hours, channel, search }: {
  port: number; ipcSecret: string;
  hours: number; channel: string; search: string;
}) {
  const setSelectedUser = useChatStore((s) => s.setSelectedUser);
  const [rows, setRows] = useState<ThreatRow[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState('');
  const [minScore, setMinScore] = useState('');
  const offsetRef = useRef(0);

  const LIMIT = 50;

  const load = useCallback(async (reset: boolean) => {
    setLoading(true);
    const off = reset ? 0 : offsetRef.current;
    const params = new URLSearchParams({
      hours: String(hours),
      limit: String(LIMIT),
      offset: String(off),
    });
    if (channel) params.set('channel', channel);
    if (search) params.set('search', search);
    if (status) params.set('status', status);
    if (minScore) params.set('min_score', minScore);

    try {
      const res = await fetch(`http://127.0.0.1:${port}/api/history/threats?${params}`, {
        headers: { 'X-IPC-Secret': ipcSecret },
      });
      const data = await res.json();
      if (reset) {
        setRows(data.threats ?? []);
        offsetRef.current = data.threats?.length ?? 0;
      } else {
        setRows((prev) => [...prev, ...(data.threats ?? [])]);
        offsetRef.current = off + (data.threats?.length ?? 0);
      }
      setTotal(data.total ?? 0);
    } catch { /* network error */ }
    setLoading(false);
  }, [port, ipcSecret, hours, channel, search, status, minScore]);

  useEffect(() => { load(true); }, [load]);

  const handleExport = () => {
    const params = new URLSearchParams({ hours: String(hours), fmt: 'csv' });
    if (channel) params.set('channel', channel);
    window.open(`http://127.0.0.1:${port}/api/stats/export/flagged_users?${params}`);
  };

  return (
    <div className="flex flex-col flex-1 overflow-hidden">
      {/* Per-tab filters */}
      <div className="flex flex-wrap items-center gap-2 px-4 py-2 bg-surface-1 border-b border-surface-3 shrink-0">
        <select
          value={status}
          onChange={(e) => setStatus(e.target.value)}
          className="bg-surface-2 border border-surface-3 rounded px-2 py-1 text-xs text-gray-300 focus:outline-none focus:border-accent-purple"
        >
          <option value="">All statuses</option>
          <option value="active">Active</option>
          <option value="resolved">Resolved</option>
          <option value="false_positive">False positive</option>
        </select>
        <input
          type="number"
          value={minScore}
          onChange={(e) => setMinScore(e.target.value)}
          placeholder="Min score…"
          min={0} max={100}
          className="bg-surface-2 border border-surface-3 rounded px-2 py-1 text-xs text-gray-300 placeholder-gray-600 focus:outline-none focus:border-accent-purple w-24"
        />
        <span className="text-xs text-gray-600 ml-auto">
          {loading && rows.length === 0 ? 'Loading…' : `${total.toLocaleString()} results`}
        </span>
        <button
          onClick={handleExport}
          className="text-xs text-gray-500 hover:text-gray-200 bg-surface-2 hover:bg-surface-3 border border-surface-3 rounded px-2 py-1 transition-colors"
        >
          Export CSV
        </button>
      </div>

      <div className="flex-1 overflow-y-auto">
        {rows.length === 0 && !loading ? (
          <Empty text="No threats in this time range" />
        ) : (
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-surface-1 border-b border-surface-3">
              <tr className="text-gray-500 text-left">
                <th className="px-4 py-2 font-medium">Time</th>
                <th className="px-3 py-2 font-medium">User</th>
                <th className="px-3 py-2 font-medium">Channel</th>
                <th className="px-3 py-2 font-medium">Score</th>
                <th className="px-3 py-2 font-medium">Flags</th>
                <th className="px-3 py-2 font-medium">Signals</th>
                <th className="px-3 py-2 font-medium">Status</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id} className="border-b border-surface-3/50 hover:bg-surface-2/30 transition-colors">
                  <td className="px-4 py-2 text-gray-500 font-mono whitespace-nowrap">{fmtTs(row.flagged_at)}</td>
                  <td className="px-3 py-2">
                    <button
                      onClick={() => setSelectedUser({ userId: row.user_id, username: row.username })}
                      className="text-accent-purple hover:text-white font-mono transition-colors"
                    >
                      {row.username}
                    </button>
                  </td>
                  <td className="px-3 py-2 text-gray-500 font-mono">#{row.channel}</td>
                  <td className="px-3 py-2"><ScoreBadge score={row.threat_score} /></td>
                  <td className="px-3 py-2 text-gray-400">{row.flag_count}×</td>
                  <td className="px-3 py-2"><SignalTags signals={row.signals} /></td>
                  <td className={`px-3 py-2 font-medium ${STATUS_COLOR[row.status] ?? 'text-gray-400'}`}>
                    {row.status.replace('_', ' ')}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <LoadMoreButton
          onClick={() => load(false)}
          loading={loading}
          hasMore={rows.length < total}
        />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Cluster detail panel (expanded row)
// ---------------------------------------------------------------------------

interface ClusterDetail {
  cluster: ClusterRow;
  messages: {
    id: number; received_at: number; user_id: string; username: string;
    raw_text: string; emoji_count: number; url_count: number; has_url: number;
    is_subscriber: number; is_moderator: number; is_vip: number;
    account_age_days: number | null;
  }[];
  users: {
    user_id: string; username: string; message_count: number;
    is_subscriber: number; is_moderator: number; is_vip: number;
    account_age_days: number | null;
  }[];
}

function ClusterDetailPanel({ clusterId, port, ipcSecret }: {
  clusterId: number; port: number; ipcSecret: string;
}) {
  const setSelectedUser = useChatStore((s) => s.setSelectedUser);
  const [detail, setDetail] = useState<ClusterDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [selectedUserId, setSelectedUserId] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError('');
    fetch(`http://127.0.0.1:${port}/api/history/clusters/${clusterId}/messages`, {
      headers: { 'X-IPC-Secret': ipcSecret },
    })
      .then((r) => r.json())
      .then((d) => {
        if (d.error) { setError(d.error); return; }
        setDetail(d);
      })
      .catch(() => setError('Failed to load cluster detail'))
      .finally(() => setLoading(false));
  }, [clusterId, port, ipcSecret]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-6 text-xs text-gray-600">
        Loading cluster data…
      </div>
    );
  }
  if (error || !detail) {
    return (
      <div className="py-4 px-4 text-xs text-red-400">{error || 'No data'}</div>
    );
  }

  const visibleMessages = selectedUserId
    ? detail.messages.filter((m) => m.user_id === selectedUserId)
    : detail.messages;

  const userColors = [
    'text-purple-300', 'text-cyan-300', 'text-green-300', 'text-yellow-300',
    'text-pink-300', 'text-orange-300', 'text-blue-300', 'text-teal-300',
  ];
  const colorMap = new Map(
    detail.users.map((u, i) => [u.user_id, userColors[i % userColors.length]])
  );

  return (
    <div className="flex gap-0 border-t border-orange-800/30 bg-orange-950/10">
      {/* Left: user list */}
      <div className="w-48 shrink-0 border-r border-surface-3 py-2">
        <div className="px-3 pb-1.5 text-[9px] text-gray-600 uppercase tracking-wider font-medium">
          {detail.users.length} members
        </div>
        <button
          onClick={() => setSelectedUserId(null)}
          className={`w-full text-left px-3 py-1 text-[10px] transition-colors ${
            selectedUserId === null
              ? 'bg-surface-3 text-gray-200'
              : 'text-gray-500 hover:text-gray-300 hover:bg-surface-2/50'
          }`}
        >
          All messages
          <span className="ml-1 text-gray-600">({detail.messages.length})</span>
        </button>
        {detail.users.map((u) => (
          <div key={u.user_id} className="flex items-center gap-1 px-3 py-0.5 group">
            <button
              onClick={() => setSelectedUserId(selectedUserId === u.user_id ? null : u.user_id)}
              className={`flex-1 text-left text-[10px] font-mono truncate transition-colors ${
                selectedUserId === u.user_id
                  ? `${colorMap.get(u.user_id)} bg-surface-3 rounded px-1`
                  : `${colorMap.get(u.user_id)} opacity-80 hover:opacity-100`
              }`}
            >
              {u.username}
              {u.message_count > 0 && (
                <span className="text-gray-600 ml-1">×{u.message_count}</span>
              )}
            </button>
            <button
              onClick={() => setSelectedUser({ userId: u.user_id, username: u.username })}
              title="Open user profile"
              className="opacity-0 group-hover:opacity-100 text-[9px] text-gray-600 hover:text-accent-purple transition-all"
            >
              ↗
            </button>
          </div>
        ))}
      </div>

      {/* Right: messages */}
      <div className="flex-1 overflow-y-auto max-h-72 py-2">
        {visibleMessages.length === 0 ? (
          <div className="text-[10px] text-gray-600 px-4 py-4 text-center">
            No messages found in the ±90s detection window
          </div>
        ) : (
          <div className="flex flex-col gap-0.5 px-3">
            {visibleMessages.map((msg) => {
              const color = colorMap.get(msg.user_id) ?? 'text-gray-400';
              return (
                <div key={msg.id} className="flex items-start gap-2 py-0.5 hover:bg-surface-2/20 rounded px-1">
                  <span className="text-[9px] text-gray-600 font-mono whitespace-nowrap mt-0.5 w-14 shrink-0">
                    {new Date(msg.received_at * 1000).toLocaleTimeString([], {
                      hour: '2-digit', minute: '2-digit', second: '2-digit',
                    })}
                  </span>
                  <button
                    onClick={() => setSelectedUser({ userId: msg.user_id, username: msg.username })}
                    className={`text-[10px] font-mono font-semibold shrink-0 ${color} hover:underline`}
                  >
                    {msg.username}
                  </button>
                  <span className="text-[10px] text-gray-300 break-words min-w-0">{msg.raw_text}</span>
                  {msg.has_url ? (
                    <span className="text-[8px] bg-blue-900/40 text-blue-400 rounded px-1 shrink-0 self-center">URL</span>
                  ) : null}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Clusters tab
// ---------------------------------------------------------------------------

function ClustersTab({ port, ipcSecret, hours, channel }: {
  port: number; ipcSecret: string; hours: number; channel: string;
}) {
  const [rows, setRows] = useState<ClusterRow[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [minMembers, setMinMembers] = useState('3');
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const offsetRef = useRef(0);
  const LIMIT = 50;

  const load = useCallback(async (reset: boolean) => {
    setLoading(true);
    const off = reset ? 0 : offsetRef.current;
    const params = new URLSearchParams({
      hours: String(hours),
      limit: String(LIMIT),
      offset: String(off),
      min_members: minMembers || '1',
    });
    if (channel) params.set('channel', channel);

    try {
      const res = await fetch(`http://127.0.0.1:${port}/api/history/clusters?${params}`, {
        headers: { 'X-IPC-Secret': ipcSecret },
      });
      const data = await res.json();
      if (reset) {
        setRows(data.clusters ?? []);
        offsetRef.current = data.clusters?.length ?? 0;
      } else {
        setRows((prev) => [...prev, ...(data.clusters ?? [])]);
        offsetRef.current = off + (data.clusters?.length ?? 0);
      }
      setTotal(data.total ?? 0);
    } catch { /* network error */ }
    setLoading(false);
  }, [port, ipcSecret, hours, channel, minMembers]);

  useEffect(() => { load(true); }, [load]);

  return (
    <div className="flex flex-col flex-1 overflow-hidden">
      <div className="flex items-center gap-2 px-4 py-2 bg-surface-1 border-b border-surface-3 shrink-0">
        <label className="text-xs text-gray-500">Min members:</label>
        <input
          type="number"
          value={minMembers}
          onChange={(e) => setMinMembers(e.target.value)}
          min={1} max={100}
          className="bg-surface-2 border border-surface-3 rounded px-2 py-1 text-xs text-gray-300 w-16 focus:outline-none focus:border-accent-purple"
        />
        <span className="ml-auto text-xs text-gray-600">
          {loading && rows.length === 0 ? 'Loading…' : `${total.toLocaleString()} cluster events`}
        </span>
      </div>

      <div className="flex-1 overflow-y-auto">
        {rows.length === 0 && !loading ? (
          <Empty text="No cluster events in this time range" />
        ) : (
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-surface-1 border-b border-surface-3 z-10">
              <tr className="text-gray-500 text-left">
                <th className="px-3 py-2 w-6"></th>
                <th className="px-3 py-2 font-medium">Time</th>
                <th className="px-3 py-2 font-medium">Channel</th>
                <th className="px-3 py-2 font-medium">Cluster ID</th>
                <th className="px-3 py-2 font-medium">Members</th>
                <th className="px-3 py-2 font-medium">Risk</th>
                <th className="px-3 py-2 font-medium">Sample message</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const expanded = expandedId === row.id;
                return (
                  <Fragment key={row.id}>
                    <tr
                      onClick={() => setExpandedId(expanded ? null : row.id)}
                      className={`border-b border-surface-3/50 cursor-pointer transition-colors ${
                        expanded
                          ? 'bg-orange-950/20 border-orange-800/30'
                          : 'hover:bg-surface-2/30'
                      }`}
                    >
                      <td className="px-3 py-2 text-gray-600 select-none">
                        <span className="text-[10px]">{expanded ? '▼' : '▶'}</span>
                      </td>
                      <td className="px-3 py-2 text-gray-500 font-mono whitespace-nowrap">{fmtTs(row.detected_at)}</td>
                      <td className="px-3 py-2 text-gray-500 font-mono">#{row.channel}</td>
                      <td className="px-3 py-2 font-mono text-purple-400">{row.cluster_id}</td>
                      <td className="px-3 py-2">
                        <span className="text-orange-300 font-mono font-bold">{row.member_count}</span>
                        <span className="text-gray-600 ml-1 text-[10px]">users</span>
                      </td>
                      <td className="px-3 py-2"><ScoreBadge score={row.risk_score} /></td>
                      <td className="px-3 py-2 text-gray-400 max-w-xs truncate italic">
                        {row.sample_message ? `"${row.sample_message}"` : '—'}
                      </td>
                    </tr>
                    {expanded && (
                      <tr className="border-b border-orange-800/30">
                        <td colSpan={7} className="p-0">
                          <ClusterDetailPanel
                            clusterId={row.id}
                            port={port}
                            ipcSecret={ipcSecret}
                          />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        )}
        <LoadMoreButton onClick={() => load(false)} loading={loading} hasMore={rows.length < total} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Moderation tab
// ---------------------------------------------------------------------------

const ACTION_TYPES = ['ban', 'timeout', 'delete', 'slow_mode', 'followers_only'];

function ModerationTab({ port, ipcSecret, hours, channel, search }: {
  port: number; ipcSecret: string;
  hours: number; channel: string; search: string;
}) {
  const setSelectedUser = useChatStore((s) => s.setSelectedUser);
  const [rows, setRows] = useState<ModerationRow[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [actionType, setActionType] = useState('');
  const [status, setStatus] = useState('');
  const [triggeredBy, setTriggeredBy] = useState('');
  const offsetRef = useRef(0);
  const LIMIT = 50;

  const load = useCallback(async (reset: boolean) => {
    setLoading(true);
    const off = reset ? 0 : offsetRef.current;
    const params = new URLSearchParams({ hours: String(hours), limit: String(LIMIT), offset: String(off) });
    if (channel) params.set('channel', channel);
    if (search) params.set('search', search);
    if (actionType) params.set('action_type', actionType);
    if (status) params.set('status', status);
    if (triggeredBy) params.set('triggered_by', triggeredBy);

    try {
      const res = await fetch(`http://127.0.0.1:${port}/api/history/moderation?${params}`, {
        headers: { 'X-IPC-Secret': ipcSecret },
      });
      const data = await res.json();
      if (reset) {
        setRows(data.actions ?? []);
        offsetRef.current = data.actions?.length ?? 0;
      } else {
        setRows((prev) => [...prev, ...(data.actions ?? [])]);
        offsetRef.current = off + (data.actions?.length ?? 0);
      }
      setTotal(data.total ?? 0);
    } catch { /* network error */ }
    setLoading(false);
  }, [port, ipcSecret, hours, channel, search, actionType, status, triggeredBy]);

  useEffect(() => { load(true); }, [load]);

  const handleExport = () => {
    const params = new URLSearchParams({ hours: String(hours), fmt: 'csv' });
    if (channel) params.set('channel', channel);
    window.open(`http://127.0.0.1:${port}/api/stats/export/moderation_actions?${params}`);
  };

  return (
    <div className="flex flex-col flex-1 overflow-hidden">
      <div className="flex flex-wrap items-center gap-2 px-4 py-2 bg-surface-1 border-b border-surface-3 shrink-0">
        <select
          value={actionType}
          onChange={(e) => setActionType(e.target.value)}
          className="bg-surface-2 border border-surface-3 rounded px-2 py-1 text-xs text-gray-300 focus:outline-none focus:border-accent-purple"
        >
          <option value="">All actions</option>
          {ACTION_TYPES.map((a) => <option key={a} value={a}>{a}</option>)}
        </select>
        <select
          value={status}
          onChange={(e) => setStatus(e.target.value)}
          className="bg-surface-2 border border-surface-3 rounded px-2 py-1 text-xs text-gray-300 focus:outline-none focus:border-accent-purple"
        >
          <option value="">All statuses</option>
          <option value="completed">Completed</option>
          <option value="failed">Failed</option>
          <option value="pending">Pending</option>
          <option value="undone">Undone</option>
        </select>
        <select
          value={triggeredBy}
          onChange={(e) => setTriggeredBy(e.target.value)}
          className="bg-surface-2 border border-surface-3 rounded px-2 py-1 text-xs text-gray-300 focus:outline-none focus:border-accent-purple"
        >
          <option value="">Manual + Auto</option>
          <option value="manual">Manual only</option>
          <option value="auto">Auto only</option>
        </select>
        <span className="ml-auto text-xs text-gray-600">
          {loading && rows.length === 0 ? 'Loading…' : `${total.toLocaleString()} actions`}
        </span>
        <button
          onClick={handleExport}
          className="text-xs text-gray-500 hover:text-gray-200 bg-surface-2 hover:bg-surface-3 border border-surface-3 rounded px-2 py-1 transition-colors"
        >
          Export CSV
        </button>
      </div>

      <div className="flex-1 overflow-y-auto">
        {rows.length === 0 && !loading ? (
          <Empty text="No moderation actions in this time range" />
        ) : (
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-surface-1 border-b border-surface-3">
              <tr className="text-gray-500 text-left">
                <th className="px-4 py-2 font-medium">Time</th>
                <th className="px-3 py-2 font-medium">User</th>
                <th className="px-3 py-2 font-medium">Channel</th>
                <th className="px-3 py-2 font-medium">Action</th>
                <th className="px-3 py-2 font-medium">Duration</th>
                <th className="px-3 py-2 font-medium">Status</th>
                <th className="px-3 py-2 font-medium">Triggered by</th>
                <th className="px-3 py-2 font-medium">Reason</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id} className="border-b border-surface-3/50 hover:bg-surface-2/30 transition-colors">
                  <td className="px-4 py-2 text-gray-500 font-mono whitespace-nowrap">{fmtTs(row.created_at)}</td>
                  <td className="px-3 py-2">
                    <button
                      onClick={() => setSelectedUser({ userId: row.user_id, username: row.username })}
                      className="text-accent-purple hover:text-white font-mono transition-colors"
                    >
                      {row.username}
                    </button>
                  </td>
                  <td className="px-3 py-2 text-gray-500 font-mono">#{row.channel}</td>
                  <td className={`px-3 py-2 font-bold ${ACTION_COLOR[row.action_type] ?? 'text-gray-300'}`}>
                    {row.action_type}
                  </td>
                  <td className="px-3 py-2 text-gray-400">
                    {row.duration_seconds ? `${row.duration_seconds}s` : '—'}
                  </td>
                  <td className={`px-3 py-2 font-medium ${STATUS_COLOR[row.status] ?? 'text-gray-400'}`}>
                    {row.status}
                  </td>
                  <td className="px-3 py-2 text-gray-400">
                    {row.triggered_by.startsWith('auto:')
                      ? <span className="text-purple-400">auto <span className="text-gray-600 text-[9px]">{row.triggered_by.slice(5)}</span></span>
                      : <span className="text-gray-500">manual</span>
                    }
                  </td>
                  <td className="px-3 py-2 text-gray-500 max-w-xs truncate">{row.reason ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <LoadMoreButton onClick={() => load(false)} loading={loading} hasMore={rows.length < total} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Escalations tab
// ---------------------------------------------------------------------------

function EscalationsTab({ port, ipcSecret, hours, channel }: {
  port: number; ipcSecret: string; hours: number; channel: string;
}) {
  const [rows, setRows] = useState<EscalationRow[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [direction, setDirection] = useState('');
  const offsetRef = useRef(0);
  const LIMIT = 100;

  const load = useCallback(async (reset: boolean) => {
    setLoading(true);
    const off = reset ? 0 : offsetRef.current;
    const params = new URLSearchParams({ hours: String(hours), limit: String(LIMIT), offset: String(off) });
    if (channel) params.set('channel', channel);
    if (direction) params.set('direction', direction);

    try {
      const res = await fetch(`http://127.0.0.1:${port}/api/history/escalations?${params}`, {
        headers: { 'X-IPC-Secret': ipcSecret },
      });
      const data = await res.json();
      if (reset) {
        setRows(data.escalations ?? []);
        offsetRef.current = data.escalations?.length ?? 0;
      } else {
        setRows((prev) => [...prev, ...(data.escalations ?? [])]);
        offsetRef.current = off + (data.escalations?.length ?? 0);
      }
      setTotal(data.total ?? 0);
    } catch { /* network error */ }
    setLoading(false);
  }, [port, ipcSecret, hours, channel, direction]);

  useEffect(() => { load(true); }, [load]);

  return (
    <div className="flex flex-col flex-1 overflow-hidden">
      <div className="flex flex-wrap items-center gap-2 px-4 py-2 bg-surface-1 border-b border-surface-3 shrink-0">
        <select
          value={direction}
          onChange={(e) => setDirection(e.target.value)}
          className="bg-surface-2 border border-surface-3 rounded px-2 py-1 text-xs text-gray-300 focus:outline-none focus:border-accent-purple"
        >
          <option value="">All transitions</option>
          <option value="worsening">Worsening only</option>
          <option value="recovery">Recovery only</option>
        </select>
        <span className="ml-auto text-xs text-gray-600">
          {loading && rows.length === 0 ? 'Loading…' : `${total.toLocaleString()} events`}
        </span>
      </div>

      <div className="flex-1 overflow-y-auto">
        {rows.length === 0 && !loading ? (
          <Empty text="No health escalation events in this time range" />
        ) : (
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-surface-1 border-b border-surface-3">
              <tr className="text-gray-500 text-left">
                <th className="px-4 py-2 font-medium">Time</th>
                <th className="px-3 py-2 font-medium">Channel</th>
                <th className="px-3 py-2 font-medium">Transition</th>
                <th className="px-3 py-2 font-medium">Health score</th>
                <th className="px-3 py-2 font-medium">Msg/min</th>
                <th className="px-3 py-2 font-medium">Type</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const _LEVEL_ORDER: Record<string, number> = {
                  healthy: 0, elevated: 1, suspicious: 2, likely_attack: 3, critical: 4,
                };
                const worsening = (_LEVEL_ORDER[row.to_level] ?? 0) > (_LEVEL_ORDER[row.from_level] ?? 0);
                return (
                  <tr key={row.id} className="border-b border-surface-3/50 hover:bg-surface-2/30 transition-colors">
                    <td className="px-4 py-2 text-gray-500 font-mono whitespace-nowrap">{fmtTs(row.occurred_at)}</td>
                    <td className="px-3 py-2 text-gray-500 font-mono">#{row.channel}</td>
                    <td className="px-3 py-2">
                      <span className={LEVEL_COLOR[row.from_level] ?? 'text-gray-400'}>
                        {LEVEL_LABEL[row.from_level] ?? row.from_level}
                      </span>
                      <span className="text-gray-600 mx-1.5">→</span>
                      <span className={LEVEL_COLOR[row.to_level] ?? 'text-gray-400'}>
                        {LEVEL_LABEL[row.to_level] ?? row.to_level}
                      </span>
                    </td>
                    <td className="px-3 py-2">
                      <ScoreBadge score={row.health_score} />
                    </td>
                    <td className="px-3 py-2 text-gray-400 font-mono">{Math.round(row.msg_per_min)}</td>
                    <td className="px-3 py-2">
                      {worsening
                        ? <span className="text-red-400 text-[10px] font-bold">▲ Worsening</span>
                        : <span className="text-green-400 text-[10px] font-bold">▼ Recovery</span>
                      }
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
        <LoadMoreButton onClick={() => load(false)} loading={loading} hasMore={rows.length < total} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Users tab
// ---------------------------------------------------------------------------

const SORT_OPTIONS = [
  { value: 'message_count',    label: 'Most messages' },
  { value: 'max_threat_score', label: 'Highest threat' },
  { value: 'total_flags',      label: 'Most flagged' },
  { value: 'reputation',       label: 'Lowest reputation' },
  { value: 'last_seen',        label: 'Recently active' },
];

function ReputationBar({ score }: { score: number }) {
  const pct = Math.max(0, Math.min(100, score));
  const color = pct >= 80 ? 'bg-green-500' : pct >= 50 ? 'bg-yellow-500' : 'bg-red-500';
  return (
    <div className="flex items-center gap-1.5">
      <div className="w-16 h-1.5 bg-surface-3 rounded-full overflow-hidden">
        <div className={`h-full ${color} rounded-full`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-[10px] font-mono text-gray-400">{Math.round(pct)}</span>
    </div>
  );
}

function BadgeRow({ sub, mod, vip }: { sub: number; mod: number; vip: number }) {
  return (
    <div className="flex gap-0.5">
      {mod  ? <span className="text-[9px] bg-green-900/50 text-green-300 rounded px-1">MOD</span>  : null}
      {vip  ? <span className="text-[9px] bg-yellow-900/50 text-yellow-300 rounded px-1">VIP</span> : null}
      {sub  ? <span className="text-[9px] bg-purple-900/50 text-purple-300 rounded px-1">SUB</span> : null}
    </div>
  );
}

function UsersTab({ port, ipcSecret, hours, channel, search }: {
  port: number; ipcSecret: string;
  hours: number; channel: string; search: string;
}) {
  const setSelectedUser = useChatStore((s) => s.setSelectedUser);
  const [rows, setRows] = useState<UserRow[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [flaggedOnly, setFlaggedOnly] = useState(false);
  const [sortBy, setSortBy] = useState('message_count');
  const offsetRef = useRef(0);
  const LIMIT = 50;

  const load = useCallback(async (reset: boolean) => {
    setLoading(true);
    const off = reset ? 0 : offsetRef.current;
    const params = new URLSearchParams({
      hours: String(hours),
      limit: String(LIMIT),
      offset: String(off),
      sort_by: sortBy,
    });
    if (channel) params.set('channel', channel);
    if (search) params.set('search', search);
    if (flaggedOnly) params.set('flagged_only', 'true');

    try {
      const res = await fetch(`http://127.0.0.1:${port}/api/history/users?${params}`, {
        headers: { 'X-IPC-Secret': ipcSecret },
      });
      const data = await res.json();
      const fetched: UserRow[] = data.users ?? [];
      if (reset) {
        setRows(fetched);
        offsetRef.current = fetched.length;
      } else {
        setRows((prev) => [...prev, ...fetched]);
        offsetRef.current = off + fetched.length;
      }
      setTotal(data.total ?? 0);
    } catch { /* network error */ }
    setLoading(false);
  }, [port, ipcSecret, hours, channel, search, flaggedOnly, sortBy]);

  useEffect(() => { load(true); }, [load]);

  return (
    <div className="flex flex-col flex-1 overflow-hidden">
      <div className="flex flex-wrap items-center gap-2 px-4 py-2 bg-surface-1 border-b border-surface-3 shrink-0">
        <select
          value={sortBy}
          onChange={(e) => setSortBy(e.target.value)}
          className="bg-surface-2 border border-surface-3 rounded px-2 py-1 text-xs text-gray-300 focus:outline-none focus:border-accent-purple"
        >
          {SORT_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
        <label className="flex items-center gap-1.5 text-xs text-gray-400 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={flaggedOnly}
            onChange={(e) => setFlaggedOnly(e.target.checked)}
            className="accent-purple-500"
          />
          Flagged only
        </label>
        <span className="ml-auto text-xs text-gray-600">
          {loading && rows.length === 0 ? 'Loading…' : `${total.toLocaleString()} users`}
        </span>
      </div>

      <div className="flex-1 overflow-y-auto">
        {rows.length === 0 && !loading ? (
          <Empty text="No users active in this time range" />
        ) : (
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-surface-1 border-b border-surface-3">
              <tr className="text-gray-500 text-left">
                <th className="px-4 py-2 font-medium">User</th>
                <th className="px-3 py-2 font-medium">Badges</th>
                <th className="px-3 py-2 font-medium">Messages</th>
                <th className="px-3 py-2 font-medium">Avg length</th>
                <th className="px-3 py-2 font-medium">Reputation</th>
                <th className="px-3 py-2 font-medium">Flags</th>
                <th className="px-3 py-2 font-medium">Peak threat</th>
                <th className="px-3 py-2 font-medium">Last signals</th>
                <th className="px-3 py-2 font-medium">Acct age</th>
                <th className="px-3 py-2 font-medium">Last seen</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.user_id} className="border-b border-surface-3/50 hover:bg-surface-2/30 transition-colors">
                  <td className="px-4 py-2">
                    <button
                      onClick={() => setSelectedUser({ userId: row.user_id, username: row.username })}
                      className="text-accent-purple hover:text-white font-mono transition-colors"
                    >
                      {row.username}
                    </button>
                  </td>
                  <td className="px-3 py-2">
                    <BadgeRow sub={row.is_subscriber} mod={row.is_moderator} vip={row.is_vip} />
                  </td>
                  <td className="px-3 py-2 text-gray-300 font-mono">{row.message_count.toLocaleString()}</td>
                  <td className="px-3 py-2 text-gray-500 font-mono">{row.avg_msg_length}</td>
                  <td className="px-3 py-2"><ReputationBar score={row.reputation} /></td>
                  <td className="px-3 py-2">
                    <span className={row.recent_flags > 0 ? 'text-red-400 font-bold' : 'text-gray-600'}>
                      {row.recent_flags > 0 ? `${row.recent_flags}×` : '—'}
                    </span>
                    {row.total_flags > row.recent_flags && (
                      <span className="text-gray-600 ml-1 text-[9px]">({row.total_flags} all-time)</span>
                    )}
                  </td>
                  <td className="px-3 py-2">
                    {row.max_threat_score > 0 ? <ScoreBadge score={row.max_threat_score} /> : <span className="text-gray-600">—</span>}
                  </td>
                  <td className="px-3 py-2"><SignalTags signals={row.last_signals} /></td>
                  <td className="px-3 py-2 text-gray-500">
                    {row.account_age_days != null
                      ? row.account_age_days < 7
                        ? <span className="text-red-400">{row.account_age_days}d</span>
                        : `${row.account_age_days}d`
                      : '—'}
                  </td>
                  <td className="px-3 py-2 text-gray-500 font-mono whitespace-nowrap">{fmtTs(row.last_seen)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <LoadMoreButton onClick={() => load(false)} loading={loading} hasMore={rows.length < total} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Messages tab
// ---------------------------------------------------------------------------

function MessageBadges({ row }: { row: MessageRow }) {
  return (
    <div className="flex items-center gap-0.5">
      {row.is_moderator ? <span className="text-[8px] bg-green-900/50 text-green-300 rounded px-1">MOD</span>  : null}
      {row.is_vip       ? <span className="text-[8px] bg-yellow-900/50 text-yellow-300 rounded px-1">VIP</span> : null}
      {row.is_subscriber? <span className="text-[8px] bg-purple-900/50 text-purple-300 rounded px-1">SUB</span> : null}
      {row.has_url      ? <span className="text-[8px] bg-blue-900/50 text-blue-300 rounded px-1">URL</span>    : null}
    </div>
  );
}

function MessagesTab({ port, ipcSecret, hours, channel, search }: {
  port: number; ipcSecret: string;
  hours: number; channel: string; search: string;
}) {
  const setSelectedUser = useChatStore((s) => s.setSelectedUser);
  const [rows, setRows] = useState<MessageRow[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [flaggedOnly, setFlaggedOnly] = useState(false);
  const [hasUrl, setHasUrl] = useState('');
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const offsetRef = useRef(0);
  const LIMIT = 100;

  const load = useCallback(async (reset: boolean) => {
    setLoading(true);
    const off = reset ? 0 : offsetRef.current;
    const params = new URLSearchParams({
      hours: String(hours),
      limit: String(LIMIT),
      offset: String(off),
    });
    if (channel) params.set('channel', channel);
    if (search) params.set('search', search);
    if (flaggedOnly) params.set('flagged_only', 'true');
    if (hasUrl === 'yes') params.set('has_url', 'true');
    if (hasUrl === 'no') params.set('has_url', 'false');

    try {
      const res = await fetch(`http://127.0.0.1:${port}/api/history/messages?${params}`, {
        headers: { 'X-IPC-Secret': ipcSecret },
      });
      const data = await res.json();
      const fetched: MessageRow[] = data.messages ?? [];
      if (reset) {
        setRows(fetched);
        offsetRef.current = fetched.length;
      } else {
        setRows((prev) => [...prev, ...fetched]);
        offsetRef.current = off + fetched.length;
      }
      setTotal(data.total ?? 0);
    } catch { /* network error */ }
    setLoading(false);
  }, [port, ipcSecret, hours, channel, search, flaggedOnly, hasUrl]);

  useEffect(() => { load(true); }, [load]);

  return (
    <div className="flex flex-col flex-1 overflow-hidden">
      <div className="flex flex-wrap items-center gap-2 px-4 py-2 bg-surface-1 border-b border-surface-3 shrink-0">
        <label className="flex items-center gap-1.5 text-xs text-gray-400 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={flaggedOnly}
            onChange={(e) => setFlaggedOnly(e.target.checked)}
            className="accent-purple-500"
          />
          Flagged users only
        </label>
        <select
          value={hasUrl}
          onChange={(e) => setHasUrl(e.target.value)}
          className="bg-surface-2 border border-surface-3 rounded px-2 py-1 text-xs text-gray-300 focus:outline-none focus:border-accent-purple"
        >
          <option value="">All messages</option>
          <option value="yes">With URL</option>
          <option value="no">No URL</option>
        </select>
        <span className="ml-auto text-xs text-gray-600">
          {loading && rows.length === 0 ? 'Loading…' : `${total.toLocaleString()} messages`}
        </span>
      </div>

      <div className="flex-1 overflow-y-auto">
        {rows.length === 0 && !loading ? (
          <Empty text="No messages in this time range" />
        ) : (
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-surface-1 border-b border-surface-3">
              <tr className="text-gray-500 text-left">
                <th className="px-4 py-2 font-medium">Time</th>
                <th className="px-3 py-2 font-medium">User</th>
                <th className="px-3 py-2 font-medium">Channel</th>
                <th className="px-3 py-2 font-medium">Message</th>
                <th className="px-3 py-2 font-medium">Badges</th>
                <th className="px-3 py-2 font-medium">Acct age</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const expanded = expandedId === row.id;
                return (
                  <tr
                    key={row.id}
                    className="border-b border-surface-3/50 hover:bg-surface-2/30 transition-colors"
                  >
                    <td className="px-4 py-2 text-gray-500 font-mono whitespace-nowrap align-top">{fmtTs(row.received_at)}</td>
                    <td className="px-3 py-2 align-top">
                      <button
                        onClick={() => setSelectedUser({ userId: row.user_id, username: row.username })}
                        className="text-accent-purple hover:text-white font-mono transition-colors"
                      >
                        {row.username}
                      </button>
                    </td>
                    <td className="px-3 py-2 text-gray-500 font-mono align-top">#{row.channel}</td>
                    <td className="px-3 py-2 align-top max-w-md">
                      <button
                        onClick={() => setExpandedId(expanded ? null : row.id)}
                        className="text-left w-full"
                        title={expanded ? 'Click to collapse' : 'Click to expand'}
                      >
                        <span className={`text-gray-300 ${expanded ? 'whitespace-pre-wrap break-words' : 'truncate block'}`}>
                          {row.raw_text}
                        </span>
                        {!expanded && row.raw_text.length > 80 && (
                          <span className="text-gray-600 text-[9px] ml-1">+{row.raw_text.length - 80} chars</span>
                        )}
                      </button>
                    </td>
                    <td className="px-3 py-2 align-top"><MessageBadges row={row} /></td>
                    <td className="px-3 py-2 align-top text-gray-500">
                      {row.account_age_days != null
                        ? row.account_age_days < 7
                          ? <span className="text-red-400">{row.account_age_days}d</span>
                          : `${row.account_age_days}d`
                        : '—'}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
        <LoadMoreButton onClick={() => load(false)} loading={loading} hasMore={rows.length < total} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

const TABS: { key: HistoryTab; label: string; desc: string }[] = [
  { key: 'threats',     label: 'Threats',     desc: 'Flagged users by the detection engine' },
  { key: 'users',       label: 'Users',       desc: 'All chatters with activity, reputation, and flag data' },
  { key: 'messages',    label: 'Messages',    desc: 'Full message history with content search' },
  { key: 'clusters',    label: 'Clusters',    desc: 'Semantic bot clusters detected by DBSCAN' },
  { key: 'moderation',  label: 'Moderation',  desc: 'Bans, timeouts, and other mod actions' },
  { key: 'escalations', label: 'Escalations', desc: 'Health level transition events' },
];

export function HistoryPage({ port, ipcSecret }: { port: number; ipcSecret: string }) {
  const [activeTab, setActiveTab] = useState<HistoryTab>('threats');
  const [hours, setHours] = useState(24);
  const [channel, setChannel] = useState('');
  const [search, setSearch] = useState('');

  return (
    <div className="flex flex-col flex-1 overflow-hidden">
      {/* Page header */}
      <div className="px-4 py-3 bg-surface-1 border-b border-surface-3 shrink-0">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-sm font-semibold text-gray-200">Detection History</h2>
            <p className="text-[10px] text-gray-500 mt-0.5">Persistent record across all sessions</p>
          </div>

          {/* Shared time range + channel + search */}
          <div className="flex items-center gap-2">
            <div className="flex items-center gap-0.5 bg-surface-2 rounded p-0.5">
              {TIME_RANGES.map((r) => (
                <button
                  key={r.hours}
                  onClick={() => setHours(r.hours)}
                  className={`text-xs px-2.5 py-1 rounded transition-colors ${
                    hours === r.hours
                      ? 'bg-surface-3 text-gray-200'
                      : 'text-gray-500 hover:text-gray-300'
                  }`}
                >
                  {r.label}
                </button>
              ))}
            </div>
            <input
              type="text"
              value={channel}
              onChange={(e) => setChannel(e.target.value.replace(/^#/, ''))}
              placeholder="Channel…"
              className="bg-surface-2 border border-surface-3 rounded px-2 py-1 text-xs text-gray-300 placeholder-gray-600 focus:outline-none focus:border-accent-purple w-28"
            />
            {(['threats', 'moderation', 'users', 'messages'] as HistoryTab[]).includes(activeTab) && (
              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder={activeTab === 'messages' ? 'Search user or text…' : 'Search username…'}
                className="bg-surface-2 border border-surface-3 rounded px-2 py-1 text-xs text-gray-300 placeholder-gray-600 focus:outline-none focus:border-accent-purple w-44"
              />
            )}
          </div>
        </div>

        {/* Sub-tab bar */}
        <div className="flex items-center gap-1 mt-3">
          {TABS.map((tab) => (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key)}
              title={tab.desc}
              className={`text-xs px-3 py-1.5 rounded transition-colors ${
                activeTab === tab.key
                  ? 'bg-accent-purple text-white'
                  : 'text-gray-500 hover:text-gray-200 hover:bg-surface-2'
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      {/* Tab content */}
      {activeTab === 'threats' && (
        <ThreatsTab port={port} ipcSecret={ipcSecret} hours={hours} channel={channel} search={search} />
      )}
      {activeTab === 'users' && (
        <UsersTab port={port} ipcSecret={ipcSecret} hours={hours} channel={channel} search={search} />
      )}
      {activeTab === 'messages' && (
        <MessagesTab port={port} ipcSecret={ipcSecret} hours={hours} channel={channel} search={search} />
      )}
      {activeTab === 'clusters' && (
        <ClustersTab port={port} ipcSecret={ipcSecret} hours={hours} channel={channel} />
      )}
      {activeTab === 'moderation' && (
        <ModerationTab port={port} ipcSecret={ipcSecret} hours={hours} channel={channel} search={search} />
      )}
      {activeTab === 'escalations' && (
        <EscalationsTab port={port} ipcSecret={ipcSecret} hours={hours} channel={channel} />
      )}
    </div>
  );
}
