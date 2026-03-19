/**
 * ThreatPanel — right sidebar showing active threat alerts.
 *
 * Shows two sections (controlled by ThreatPrefs):
 *   1. Live alerts — pushed via the `threat_alert` WebSocket event this session (NEW badge)
 *   2. Historical  — loaded from GET /api/threats on mount (flagged_users table)
 *
 * Settings (stored in localStorage via useThreatPrefs):
 *   showLive, showHistory, maxAgeDays, sortBy, sortDir
 */

import { useEffect, useState } from 'react';
import { useChatStore } from '../store/chatStore';
import { useThreatPrefs } from '../hooks/useThreatPrefs';
import type { Alert, AlertExplanation } from '../store/chatStore';

const SEVERITY_STYLES: Record<Alert['severity'], string> = {
  critical: 'border-red-500 bg-red-950/40',
  high:     'border-orange-500 bg-orange-950/40',
  medium:   'border-yellow-500 bg-yellow-950/30',
  low:      'border-surface-3 bg-surface-2',
};

const SEVERITY_BADGE: Record<Alert['severity'], string> = {
  critical: 'bg-red-600 text-white',
  high:     'bg-orange-500 text-white',
  medium:   'bg-yellow-500 text-black',
  low:      'bg-surface-3 text-gray-300',
};

const CONTRIBUTION_COLOR: Record<Alert['severity'], string> = {
  critical: 'bg-red-500',
  high:     'bg-orange-500',
  medium:   'bg-yellow-500',
  low:      'bg-gray-500',
};

function ExplanationBar({ item, severity }: { item: AlertExplanation; severity: Alert['severity'] }) {
  return (
    <div className="flex items-center gap-1.5 mt-0.5">
      <span className="text-gray-500 w-28 truncate text-[10px]">{item.label}</span>
      <div className="flex-1 h-1 bg-surface-3 rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full ${CONTRIBUTION_COLOR[severity]} opacity-70`}
          style={{ width: `${Math.min(item.contribution, 100)}%` }}
        />
      </div>
      <span className="text-gray-500 text-[10px] w-8 text-right">{item.contribution.toFixed(0)}%</span>
    </div>
  );
}

function formatAbsTime(timestamp: number): string {
  const d = new Date(timestamp * 1000);
  const now = new Date();
  const sameDay =
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate();
  if (sameDay) {
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }
  return (
    d.toLocaleDateString([], { month: 'short', day: 'numeric' }) +
    ' ' +
    d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  );
}

function formatTimeAgo(timestamp: number): string {
  const seconds = Math.floor(Date.now() / 1000 - timestamp);
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  return `${Math.floor(seconds / 3600)}h ago`;
}

function AlertCard({
  alert,
  showAbsTime = false,
  port,
  ipcSecret,
}: {
  alert: Alert;
  showAbsTime?: boolean;
  port: number;
  ipcSecret: string;
}) {
  const dismissAlert = useChatStore((s) => s.dismissAlert);
  const setSelectedUser = useChatStore((s) => s.setSelectedUser);
  const watchedUsers = useChatStore((s) => s.watchedUsers);
  const addWatchedUser = useChatStore((s) => s.addWatchedUser);
  const [showExplanation, setShowExplanation] = useState(false);
  const [flagging, setFlagging] = useState(false);

  const timeLabel = showAbsTime ? formatAbsTime(alert.timestamp) : formatTimeAgo(alert.timestamp);
  const hasExplanation = alert.explanation && alert.explanation.length > 0;
  const isWatched = watchedUsers.some((w) => w.user_id === alert.userId);

  function openUser() {
    if (alert.userId && alert.username) {
      setSelectedUser({ userId: alert.userId, username: alert.username });
    }
  }

  async function toggleFlag() {
    if (!alert.userId || flagging) return;
    setFlagging(true);
    try {
      if (isWatched) {
        await fetch(
          `http://127.0.0.1:${port}/api/watchlist/${encodeURIComponent(alert.userId)}`,
          { method: 'DELETE', headers: { 'X-IPC-Secret': ipcSecret } },
        );
        // store doesn't auto-remove on API call — handled by WatchlistPanel polling
        // do a quick local remove via store
        useChatStore.getState().removeWatchedUser(alert.userId);
      } else {
        const res = await fetch(`http://127.0.0.1:${port}/api/watchlist`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-IPC-Secret': ipcSecret },
          body: JSON.stringify({
            user_id: alert.userId,
            username: alert.username,
            note: `Flagged from threats panel (score ${alert.confidence.toFixed(0)})`,
            priority: 'high',
          }),
        });
        if (res.ok) {
          const data = await res.json();
          addWatchedUser(data);
        }
      }
    } finally {
      setFlagging(false);
    }
  }

  return (
    <div className={`border-l-2 rounded-r px-3 py-2 mb-2 text-xs ${SEVERITY_STYLES[alert.severity]}`}>
      {/* Row 1: badges + time + actions */}
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-1 flex-wrap">
          <span className={`shrink-0 rounded px-1.5 py-0.5 font-bold uppercase text-[10px] ${SEVERITY_BADGE[alert.severity]}`}>
            {alert.severity}
          </span>
          {alert.source === 'live' && (
            <span className="shrink-0 rounded px-1 py-0.5 font-bold uppercase text-[9px] bg-accent-purple text-white">
              NEW
            </span>
          )}
          {alert.source === 'live' && alert.sessionFlagCount > 1 && (
            <span
              className="shrink-0 rounded px-1 py-0.5 text-[9px] bg-yellow-900/60 text-yellow-400"
              title={`Re-flagged ${alert.sessionFlagCount} times this session`}
            >
              ×{alert.sessionFlagCount}
            </span>
          )}
          {alert.source === 'history' && alert.flagCount != null && alert.flagCount > 1 && (
            <span
              className="shrink-0 rounded px-1 py-0.5 text-[9px] bg-surface-3 text-gray-400"
              title={`Flagged ${alert.flagCount} times total`}
            >
              ×{alert.flagCount}
            </span>
          )}
        </div>

        <div className="flex items-center gap-1.5 shrink-0">
          <span className="text-gray-600 text-[10px]" title={new Date(alert.timestamp * 1000).toLocaleString()}>
            {timeLabel}
          </span>
          <button
            onClick={toggleFlag}
            disabled={flagging}
            title={isWatched ? 'Remove from watchlist' : 'Add to watchlist to track later'}
            className={`leading-none transition-colors ${isWatched ? 'text-yellow-400 hover:text-yellow-600' : 'text-gray-600 hover:text-yellow-400'}`}
          >
            {isWatched ? '★' : '☆'}
          </button>
          {alert.source === 'live' && (
            <button
              onClick={() => dismissAlert(alert.id)}
              className="text-gray-600 hover:text-gray-400 leading-none"
              title="Dismiss"
            >
              ✕
            </button>
          )}
        </div>
      </div>

      {/* Row 2: username */}
      <button
        onClick={openUser}
        className="mt-1 text-gray-200 font-mono hover:text-accent-purple hover:underline text-left w-full truncate"
        title={alert.username || (alert.affectedUsers[0] ?? 'unknown')}
      >
        {alert.username || (alert.affectedUsers[0] ?? 'unknown')}
      </button>

      {/* Row 3: description */}
      <div className="mt-0.5 text-gray-500 leading-snug truncate" title={alert.description}>
        {alert.description}
      </div>

      {/* Row 4: score + why-flagged toggle */}
      <div className="mt-1 flex items-center justify-between">
        <span className="text-gray-600">
          Score: <span className="text-gray-400 font-mono">{alert.confidence.toFixed(0)}</span>
        </span>
        {hasExplanation && (
          <button
            onClick={() => setShowExplanation((v) => !v)}
            className="text-gray-600 hover:text-gray-400 text-[10px] underline underline-offset-2"
          >
            {showExplanation ? 'hide' : 'why flagged?'}
          </button>
        )}
      </div>

      {showExplanation && hasExplanation && (
        <div className="mt-2 pt-2 border-t border-surface-3">
          <div className="text-[10px] text-gray-600 mb-1 uppercase tracking-wider">Top signals</div>
          {alert.explanation.map((item) => (
            <ExplanationBar key={item.signal} item={item} severity={alert.severity} />
          ))}
        </div>
      )}
    </div>
  );
}

/** Convert a flagged_users DB row to an Alert shape for rendering */
function dbRowToAlert(row: Record<string, unknown>): Alert {
  const signals = Array.isArray(row.signals) ? (row.signals as string[]) : [];
  return {
    id: `hist-${row.id}`,
    severity: mapSeverity(Number(row.threat_score ?? 0)),
    signal: signals[0] ?? '',
    description: signals.length > 0 ? `Signals: ${signals.join(', ')}` : 'Flagged by detection engine',
    affectedUsers: [String(row.username ?? '')],
    userId: String(row.user_id ?? ''),
    username: String(row.username ?? ''),
    confidence: Number(row.threat_score ?? 0),
    timestamp: Number(row.flagged_at ?? 0),
    dismissed: false,
    explanation: [],
    source: 'history',
    sessionFlagCount: 0,
    flagCount: row.flag_count != null ? Number(row.flag_count) : undefined,
  };
}

function mapSeverity(score: number): Alert['severity'] {
  if (score >= 90) return 'critical';
  if (score >= 70) return 'high';
  if (score >= 50) return 'medium';
  return 'low';
}

function sortAlerts(alerts: Alert[], sortBy: string, sortDir: string): Alert[] {
  const dir = sortDir === 'asc' ? 1 : -1;
  return [...alerts].sort((a, b) => {
    if (sortBy === 'score') return dir * (a.confidence - b.confidence);
    if (sortBy === 'flagCount') return dir * ((a.flagCount ?? a.sessionFlagCount ?? 0) - (b.flagCount ?? b.sessionFlagCount ?? 0));
    // 'age' — newer first by default (desc), so we compare timestamps
    return dir * (a.timestamp - b.timestamp);
  });
}

export function ThreatPanel({ port, ipcSecret }: { port: number; ipcSecret: string }) {
  const alerts = useChatStore((s) => s.alerts);
  const channel = useChatStore((s) => s.channel);
  const dataRefreshKey = useChatStore((s) => s.dataRefreshKey);
  const { prefs } = useThreatPrefs();
  const [historyAlerts, setHistoryAlerts] = useState<Alert[]>([]);

  const setWatchedUsers = useChatStore((s) => s.setWatchedUsers);

  useEffect(() => {
    const headers = { 'X-IPC-Secret': ipcSecret };

    async function fetchHistory() {
      try {
        const params = new URLSearchParams({ limit: '100' });
        if (prefs.maxAgeDays > 0) params.set('max_age_days', String(prefs.maxAgeDays));
        // Only show threats flagged on the current channel (backend excludes __sim__ by default)
        if (channel) params.set('channel', channel);
        const res = await fetch(`http://127.0.0.1:${port}/api/threats?${params}`, { headers });
        if (!res.ok) return;
        const data = await res.json();
        if (Array.isArray(data.threats)) {
          setHistoryAlerts((data.threats as Record<string, unknown>[]).map(dbRowToAlert));
        }
      } catch { /* non-critical */ }
    }

    async function fetchWatchlist() {
      try {
        const res = await fetch(`http://127.0.0.1:${port}/api/watchlist`, { headers });
        if (!res.ok) return;
        const data = await res.json();
        if (Array.isArray(data.watched)) setWatchedUsers(data.watched);
      } catch { /* non-critical */ }
    }

    fetchHistory();
    fetchWatchlist();
  }, [port, ipcSecret, prefs.maxAgeDays, channel, setWatchedUsers, dataRefreshKey]);

  const liveVisible = prefs.showLive
    ? alerts.filter((a) => !a.dismissed)
    : [];

  // Deduplicate: if a userId has a live alert, suppress the historical entry
  const liveUserIds = new Set(liveVisible.map((a) => a.userId).filter(Boolean));
  const filteredHistory = prefs.showHistory
    ? historyAlerts.filter((a) => !liveUserIds.has(a.userId))
    : [];

  const sortedLive = sortAlerts(liveVisible, prefs.sortBy, prefs.sortDir);
  const sortedHistory = sortAlerts(filteredHistory, prefs.sortBy, prefs.sortDir);

  const totalCount = sortedLive.length + sortedHistory.length;

  return (
    <div className="flex flex-col">
      <div className="px-3 py-1.5 border-b border-surface-3 text-xs text-gray-500 flex items-center justify-between">
        <span>THREATS</span>
        {totalCount > 0 && (
          <span className="bg-red-600 text-white rounded-full px-1.5 text-[10px] font-bold">
            {totalCount}
          </span>
        )}
      </div>

      <div className="overflow-y-auto max-h-72 p-2">
        {totalCount === 0 ? (
          <div className="flex flex-col items-center justify-center py-4 text-gray-600 text-xs text-center px-4">
            <div className="text-2xl mb-2 opacity-30">✓</div>
            No active threats
          </div>
        ) : (
          <>
            {sortedLive.map((alert) => (
              <AlertCard key={alert.id} alert={alert} port={port} ipcSecret={ipcSecret} />
            ))}

            {sortedHistory.length > 0 && (
              <>
                {sortedLive.length > 0 && (
                  <div className="text-[10px] text-gray-600 uppercase tracking-wider px-1 pt-1 pb-1 border-t border-surface-3 mt-1 mb-1">
                    Previous sessions
                  </div>
                )}
                {sortedHistory.map((alert) => (
                  <AlertCard key={alert.id} alert={alert} showAbsTime port={port} ipcSecret={ipcSecret} />
                ))}
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}
