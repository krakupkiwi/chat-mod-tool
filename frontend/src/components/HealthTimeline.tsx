/**
 * HealthTimeline — 60-minute rolling area chart of chat health score.
 *
 * Subscribes to health_update events via the store and accumulates up to
 * 3600 data points (1/sec). Renders with Recharts AreaChart.
 *
 * Level colour bands:
 *   ≥ 80  healthy  — green
 *   ≥ 65  elevated — yellow
 *   ≥ 45  suspicious — orange
 *   < 45  critical — red
 */

import { useEffect, useRef, useState } from 'react';
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
} from 'recharts';
import { useChatStore } from '../store/chatStore';

interface DataPoint {
  t: number;   // unix seconds
  score: number;
  risk: number;
}

const MAX_POINTS = 3600; // 1 hour at 1/sec

function formatTime(t: number): string {
  const d = new Date(t * 1000);
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function scoreColor(score: number): string {
  if (score >= 80) return '#4ade80';  // green
  if (score >= 65) return '#facc15';  // yellow
  if (score >= 45) return '#fb923c';  // orange
  return '#f87171';                    // red
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function CustomDot(props: any) {
  // Don't render individual dots — too many points
  return null;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function CustomTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null;
  const score = payload[0]?.value as number;
  return (
    <div className="bg-surface-2 border border-surface-3 rounded px-2 py-1 text-xs">
      <div className="text-gray-400">{formatTime(label)}</div>
      <div style={{ color: scoreColor(score) }} className="font-mono font-bold">
        Health: {Math.round(score)}
      </div>
    </div>
  );
}

export function HealthTimeline() {
  const health = useChatStore((s) => s.health);
  const backendConfig = useChatStore((s) => s.backendConfig);
  const [points, setPoints] = useState<DataPoint[]>([]);
  const lastTs = useRef<number>(0);
  const bootstrapped = useRef(false);

  // Bootstrap from DB on mount (once backend config is available)
  useEffect(() => {
    if (bootstrapped.current || !backendConfig) return;
    bootstrapped.current = true;
    fetch(`http://127.0.0.1:${backendConfig.port}/api/stats/health?minutes=60`, {
      headers: { 'X-IPC-Secret': backendConfig.ipcSecret },
    })
      .then((r) => r.json())
      .then((data: { points: Array<{ recorded_at: number; health_score: number }> }) => {
        if (data.points?.length) {
          const historical: DataPoint[] = data.points.map((p) => ({
            t: p.recorded_at,
            score: p.health_score,
            risk: 100 - p.health_score,
          }));
          setPoints(historical);
          lastTs.current = historical[historical.length - 1].t;
        }
      })
      .catch(() => {/* ignore */});
  }, [backendConfig]);

  // Append live points from WebSocket
  useEffect(() => {
    if (!health) return;
    const now = Math.floor(Date.now() / 1000);
    if (now === lastTs.current) return;
    lastTs.current = now;

    setPoints((prev) => {
      const next = [...prev, { t: now, score: health.score, risk: health.riskScore }];
      return next.length > MAX_POINTS ? next.slice(next.length - MAX_POINTS) : next;
    });
  }, [health?.score]); // eslint-disable-line react-hooks/exhaustive-deps

  if (points.length < 2) {
    return (
      <div className="px-3 py-2">
        <div className="text-xs text-gray-500 uppercase tracking-wide mb-1">Health Timeline</div>
        <div className="h-20 flex items-center justify-center text-gray-600 text-xs">
          Collecting data…
        </div>
      </div>
    );
  }

  // Show only last 10 minutes for readability, but keep full history
  const display = points.slice(-600);
  const latestColor = scoreColor(points[points.length - 1].score);

  return (
    <div className="px-3 py-2">
      <div className="flex items-center justify-between mb-1">
        <span className="text-xs text-gray-500 uppercase tracking-wide">Health Timeline</span>
        <span className="text-xs text-gray-600">{points.length >= 600 ? '10 min' : `${points.length}s`}</span>
      </div>
      <ResponsiveContainer width="100%" height={72}>
        <AreaChart data={display} margin={{ top: 2, right: 2, left: -20, bottom: 0 }}>
          <defs>
            <linearGradient id="healthGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor={latestColor} stopOpacity={0.3} />
              <stop offset="95%" stopColor={latestColor} stopOpacity={0.02} />
            </linearGradient>
          </defs>
          <XAxis
            dataKey="t"
            hide
            type="number"
            domain={['dataMin', 'dataMax']}
          />
          <YAxis domain={[0, 100]} hide />
          <ReferenceLine y={80} stroke="#4ade8022" strokeDasharray="3 3" />
          <ReferenceLine y={45} stroke="#fb923c22" strokeDasharray="3 3" />
          <Tooltip content={<CustomTooltip />} />
          <Area
            type="monotone"
            dataKey="score"
            stroke={latestColor}
            strokeWidth={1.5}
            fill="url(#healthGrad)"
            dot={<CustomDot />}
            activeDot={{ r: 3, fill: latestColor }}
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
