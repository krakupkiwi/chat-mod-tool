/**
 * StatsPage — analytics dashboard tab.
 *
 * Shows:
 *   - Session summary card (messages, users, flags, actions, health)
 *   - Health trend indicator
 *   - Top threats table
 *   - Most triggered signals
 *   - CSV export buttons
 */

import { useEffect, useState } from 'react';
import {
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  ReferenceLine,
} from 'recharts';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface SessionReport {
  hours: number;
  total_messages: number;
  unique_users: number;
  total_flagged: number;
  total_bans: number;
  total_timeouts: number;
  avg_health: number | null;
  min_health: number | null;
  max_health: number | null;
  health_trend: number | null;
  top_threats: { username: string; max_score: number; detections: number }[];
  top_signals: { signal: string; count: number }[];
}

interface TimelinePoint {
  bucket_ts: number;
  avg_health: number;
  min_health: number;
  avg_msg_per_min: number;
  avg_active_users: number;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatTs(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function trendLabel(trend: number | null): { text: string; color: string } {
  if (trend === null) return { text: '—', color: 'text-gray-500' };
  if (trend > 5) return { text: `+${trend.toFixed(1)} improving`, color: 'text-green-400' };
  if (trend < -5) return { text: `${trend.toFixed(1)} worsening`, color: 'text-red-400' };
  return { text: 'Stable', color: 'text-gray-400' };
}

function scoreColor(score: number): string {
  if (score >= 75) return 'text-red-400';
  if (score >= 60) return 'text-orange-400';
  if (score >= 40) return 'text-yellow-400';
  return 'text-gray-400';
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function StatCard({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <div className="bg-surface-2 border border-surface-3 rounded p-3 text-center">
      <div className="text-2xl font-bold font-mono text-gray-100">{value}</div>
      <div className="text-xs text-gray-500 mt-0.5">{label}</div>
      {sub && <div className="text-xs text-gray-600 mt-0.5">{sub}</div>}
    </div>
  );
}

function SectionHeader({ title }: { title: string }) {
  return (
    <div className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">{title}</div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

interface Props {
  port: number;
  ipcSecret: string;
}

const TIME_RANGES = [
  { label: '30m', hours: 0.5 },
  { label: '1h',  hours: 1 },
  { label: '2h',  hours: 2 },
  { label: '4h',  hours: 4 },
  { label: '8h',  hours: 8 },
  { label: '24h', hours: 24 },
] as const;

export function StatsPage({ port, ipcSecret }: Props) {
  const [hours, setHours] = useState(2);
  const [report, setReport] = useState<SessionReport | null>(null);
  const [timeline, setTimeline] = useState<TimelinePoint[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const base = `http://127.0.0.1:${port}`;
  const headers = { 'X-IPC-Secret': ipcSecret };

  useEffect(() => {
    setLoading(true);
    setError(null);

    Promise.all([
      fetch(`${base}/api/stats/session?hours=${hours}`, { headers }).then((r) => r.json()),
      fetch(`${base}/api/stats/timeline?hours=${hours}&bucket_minutes=5`, { headers }).then((r) => r.json()),
    ])
      .then(([sess, tl]) => {
        setReport(sess);
        setTimeline(tl.points ?? []);
        setLoading(false);
      })
      .catch((e: Error) => {
        setError(e.message);
        setLoading(false);
      });
  }, [hours]); // eslint-disable-line react-hooks/exhaustive-deps

  function downloadCsv(type: 'flagged_users' | 'moderation_actions') {
    const url = `${base}/api/stats/export/${type}?hours=${hours}`;
    // Build a temporary anchor with the secret in the header — we can't set
    // headers on a navigation, so fetch + blob approach needed.
    fetch(url, { headers })
      .then((r) => r.blob())
      .then((blob) => {
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = `${type}.csv`;
        a.click();
        URL.revokeObjectURL(a.href);
      });
  }

  const trend = report ? trendLabel(report.health_trend) : null;

  return (
    <div className="flex flex-col h-full overflow-y-auto px-4 py-4 gap-5">
      {/* Header + time range selector */}
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-gray-200">Session Analytics</h2>
        <div className="flex items-center gap-2">
          {TIME_RANGES.map(({ label, hours: h }) => (
            <button
              key={label}
              onClick={() => setHours(h)}
              className={`text-xs px-2 py-1 rounded border transition-colors ${
                hours === h
                  ? 'bg-accent-purple/20 border-accent-purple/50 text-accent-purple'
                  : 'border-surface-3 text-gray-500 hover:text-gray-300'
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {loading && <div className="text-xs text-gray-500 text-center py-8">Loading…</div>}
      {error && <div className="text-xs text-red-400 text-center py-8">{error}</div>}

      {report && !loading && (
        <>
          {/* Summary stats grid */}
          <div className="grid grid-cols-3 gap-2">
            <StatCard label="Messages" value={(report.total_messages ?? 0).toLocaleString()} />
            <StatCard label="Unique chatters" value={(report.unique_users ?? 0).toLocaleString()} />
            <StatCard label="Flagged" value={(report.total_flagged ?? 0).toLocaleString()} />
            <StatCard label="Bans" value={(report.total_bans ?? 0).toLocaleString()} />
            <StatCard label="Timeouts" value={(report.total_timeouts ?? 0).toLocaleString()} />
            <StatCard
              label="Avg health"
              value={report.avg_health ?? '—'}
              sub={`min ${report.min_health ?? '—'} / max ${report.max_health ?? '—'}`}
            />
          </div>

          {/* Health trend */}
          {trend && (
            <div className="flex items-center gap-2 text-xs">
              <span className="text-gray-500">Health trend:</span>
              <span className={trend.color}>{trend.text}</span>
            </div>
          )}

          {/* Timeline chart */}
          {timeline.length > 1 && (
            <div>
              <SectionHeader title="Health over time" />
              <ResponsiveContainer width="100%" height={100}>
                <AreaChart data={timeline} margin={{ top: 4, right: 4, bottom: 0, left: -24 }}>
                  <defs>
                    <linearGradient id="hg" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#4ade80" stopOpacity={0.3} />
                      <stop offset="95%" stopColor="#4ade80" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <XAxis
                    dataKey="bucket_ts"
                    tickFormatter={formatTs}
                    tick={{ fontSize: 9, fill: '#6b7280' }}
                    tickLine={false}
                    axisLine={false}
                    interval="preserveStartEnd"
                  />
                  <YAxis
                    domain={[0, 100]}
                    tick={{ fontSize: 9, fill: '#6b7280' }}
                    tickLine={false}
                    axisLine={false}
                  />
                  <Tooltip
                    content={({ active, payload }) => {
                      if (!active || !payload?.length) return null;
                      const d = payload[0].payload as TimelinePoint;
                      return (
                        <div className="bg-surface-2 border border-surface-3 rounded px-2 py-1 text-xs">
                          <div className="text-gray-400">{formatTs(d.bucket_ts)}</div>
                          <div className="text-green-400">Health {d.avg_health}</div>
                          <div className="text-gray-500">{d.avg_msg_per_min} msg/min</div>
                        </div>
                      );
                    }}
                  />
                  <ReferenceLine y={80} stroke="#4ade80" strokeDasharray="3 3" strokeOpacity={0.3} />
                  <ReferenceLine y={45} stroke="#fb923c" strokeDasharray="3 3" strokeOpacity={0.3} />
                  <Area
                    type="monotone"
                    dataKey="avg_health"
                    stroke="#4ade80"
                    strokeWidth={1.5}
                    fill="url(#hg)"
                    dot={false}
                    isAnimationActive={false}
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* Top threats */}
          {report.top_threats.length > 0 && (
            <div>
              <SectionHeader title="Top threats" />
              <div className="space-y-1">
                {report.top_threats.map((u) => (
                  <div key={u.username} className="flex items-center gap-2 text-xs py-1 border-b border-surface-3 last:border-0">
                    <span className="text-gray-300 flex-1 truncate">{u.username}</span>
                    <span className={`font-mono font-bold ${scoreColor(u.max_score)}`}>
                      {u.max_score}
                    </span>
                    <span className="text-gray-600">{u.detections}×</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Top signals */}
          {report.top_signals.length > 0 && (
            <div>
              <SectionHeader title="Top signals" />
              <div className="flex flex-wrap gap-1.5">
                {report.top_signals.map((s) => (
                  <div
                    key={s.signal}
                    className="flex items-center gap-1 bg-surface-2 border border-surface-3 rounded px-2 py-0.5 text-xs"
                  >
                    <span className="text-yellow-300">{s.signal}</span>
                    <span className="text-gray-600">{s.count}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* CSV exports */}
          <div>
            <SectionHeader title="Export data" />
            <div className="flex gap-2">
              <button
                onClick={() => downloadCsv('flagged_users')}
                className="text-xs px-3 py-1.5 bg-surface-2 hover:bg-surface-3 border border-surface-3 text-gray-300 rounded transition-colors"
              >
                Flagged users CSV
              </button>
              <button
                onClick={() => downloadCsv('moderation_actions')}
                className="text-xs px-3 py-1.5 bg-surface-2 hover:bg-surface-3 border border-surface-3 text-gray-300 rounded transition-colors"
              >
                Mod actions CSV
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
