/**
 * EventLog — session history of threats, clusters, and health escalations.
 *
 * Events are captured in useWebSocket and stored in the Zustand eventLog
 * (capped at 500). Unlike the live ClusterPanel (which resets every 10s) and
 * ThreatPanel (capped at 50), the EventLog never drops entries mid-session.
 */

import { useState } from 'react';
import { useChatStore, type EventLogEntry } from '../store/chatStore';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const LEVEL_LABEL: Record<string, string> = {
  healthy: 'Healthy',
  elevated: 'Elevated',
  suspicious: 'Suspicious',
  likely_attack: 'Likely Attack',
  critical: 'Critical',
};

const LEVEL_COLOR: Record<string, string> = {
  healthy: 'text-green-400',
  elevated: 'text-yellow-400',
  suspicious: 'text-orange-400',
  likely_attack: 'text-red-400',
  critical: 'text-red-500',
};

const SEVERITY_COLOR: Record<string, string> = {
  low: 'text-gray-400',
  medium: 'text-yellow-400',
  high: 'text-orange-400',
  critical: 'text-red-400',
};

function timeStr(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

// ---------------------------------------------------------------------------
// Entry renderers
// ---------------------------------------------------------------------------

function ClusterEntry({ entry }: { entry: EventLogEntry }) {
  const setSelectedUser = useChatStore((s) => s.setSelectedUser);
  const messages = useChatStore((s) => s.messages);
  const userMap = new Map(messages.map((m) => [m.userId, m.username]));

  return (
    <div className="border-l-2 border-orange-700/60 pl-2 py-1">
      <div className="flex items-center gap-1.5 mb-0.5">
        <span className="text-orange-400 text-[10px] font-bold">Cluster detected</span>
        {entry.channel && (
          <span className="text-[9px] font-mono bg-surface-3 text-gray-400 rounded px-1">
            #{entry.channel}
          </span>
        )}
        <span className="text-gray-600 text-[9px] ml-auto">{timeStr(entry.timestamp)}</span>
      </div>
      {entry.clusterSample && (
        <div className="text-gray-400 text-[10px] italic truncate mb-1">
          "{entry.clusterSample}"
        </div>
      )}
      <div className="flex flex-wrap gap-1">
        {(entry.userIds ?? []).slice(0, 10).map((uid) => {
          const name = userMap.get(uid) ?? uid.slice(0, 10);
          return (
            <button
              key={uid}
              onClick={() => setSelectedUser({ userId: uid, username: name })}
              className="text-[9px] font-mono bg-surface-3 hover:bg-orange-900/30 text-gray-300 hover:text-orange-300 rounded px-1 py-0.5 transition-colors"
            >
              {name}
            </button>
          );
        })}
        {(entry.userIds?.length ?? 0) > 10 && (
          <span className="text-[9px] text-gray-600 self-center">
            +{(entry.userIds?.length ?? 0) - 10} more
          </span>
        )}
      </div>
    </div>
  );
}

function HealthEscalationEntry({ entry }: { entry: EventLogEntry }) {
  return (
    <div className="border-l-2 border-yellow-700/60 pl-2 py-1">
      <div className="flex items-center gap-1.5">
        <span className="text-yellow-400 text-[10px] font-bold">Health escalated</span>
        <span className="text-gray-500 text-[10px]">
          <span className={LEVEL_COLOR[entry.fromLevel ?? ''] ?? 'text-gray-400'}>
            {LEVEL_LABEL[entry.fromLevel ?? ''] ?? entry.fromLevel}
          </span>
          {' → '}
          <span className={LEVEL_COLOR[entry.toLevel ?? ''] ?? 'text-gray-400'}>
            {LEVEL_LABEL[entry.toLevel ?? ''] ?? entry.toLevel}
          </span>
        </span>
        <span className="text-gray-600 text-[9px] ml-auto">{timeStr(entry.timestamp)}</span>
      </div>
    </div>
  );
}

function ThreatEntry({ entry }: { entry: EventLogEntry }) {
  const setSelectedUser = useChatStore((s) => s.setSelectedUser);

  return (
    <div className="border-l-2 border-red-700/60 pl-2 py-1">
      <div className="flex items-center gap-1.5 mb-0.5">
        <span className={`text-[10px] font-bold ${SEVERITY_COLOR[entry.severity ?? 'low']}`}>
          {entry.severity?.toUpperCase()} threat
        </span>
        {entry.channel && (
          <span className="text-[9px] font-mono bg-surface-3 text-gray-400 rounded px-1">
            #{entry.channel}
          </span>
        )}
        <span className="text-gray-600 text-[9px] ml-auto">{timeStr(entry.timestamp)}</span>
      </div>
      {entry.username && (
        <button
          onClick={() => setSelectedUser({ userId: entry.userId ?? '', username: entry.username ?? '' })}
          className="text-[10px] font-mono text-accent-purple hover:text-white transition-colors"
        >
          {entry.username}
        </button>
      )}
      {entry.description && (
        <div className="text-gray-500 text-[10px] mt-0.5 truncate">{entry.description}</div>
      )}
    </div>
  );
}

function Entry({ entry }: { entry: EventLogEntry }) {
  if (entry.type === 'cluster') return <ClusterEntry entry={entry} />;
  if (entry.type === 'health_escalation') return <HealthEscalationEntry entry={entry} />;
  return <ThreatEntry entry={entry} />;
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function EventLog() {
  const eventLog = useChatStore((s) => s.eventLog);
  const clearEventLog = useChatStore((s) => s.clearEventLog);
  const [collapsed, setCollapsed] = useState(false);

  return (
    <div className="border-t border-surface-3">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-1.5">
        <button
          onClick={() => setCollapsed((c) => !c)}
          className="flex items-center gap-1.5 text-[10px] text-gray-500 uppercase tracking-wider hover:text-gray-300 transition-colors"
        >
          <span>{collapsed ? '▶' : '▼'}</span>
          Event History
          {eventLog.length > 0 && (
            <span className="bg-surface-3 text-gray-400 rounded px-1">{eventLog.length}</span>
          )}
        </button>
        {!collapsed && eventLog.length > 0 && (
          <button
            onClick={clearEventLog}
            className="text-[9px] text-gray-600 hover:text-gray-400 transition-colors"
          >
            Clear
          </button>
        )}
      </div>

      {!collapsed && (
        <div className="px-3 pb-2 flex flex-col gap-1.5 max-h-64 overflow-y-auto">
          {eventLog.length === 0 ? (
            <div className="text-[10px] text-gray-600 py-2 text-center">
              No events yet this session
            </div>
          ) : (
            eventLog.map((e) => <Entry key={e.id} entry={e} />)
          )}
        </div>
      )}
    </div>
  );
}
