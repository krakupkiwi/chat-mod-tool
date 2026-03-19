/**
 * SignalBreakdown — shows each detection signal's contribution to the
 * current risk score as a mini bar chart.
 */

import type { HealthSnapshot } from '../store/chatStore';

interface Props {
  health: HealthSnapshot;
}

// Display name and max value for each signal
const SIGNAL_CONFIG: Record<string, { label: string; max: number; color: string }> = {
  temporal_sync:    { label: 'Temporal Sync',    max: 25, color: '#ef4444' },
  duplicate_ratio:  { label: 'Duplicate Flood',  max: 35, color: '#f97316' },
  semantic_cluster: { label: 'Semantic Cluster', max: 25, color: '#a855f7' },
  velocity:         { label: 'Velocity Spike',   max: 30, color: '#3b82f6' },
  burst_anomaly:    { label: 'Burst Anomaly',    max: 25, color: '#eab308' },
  new_account:      { label: 'New Accounts',     max: 20, color: '#06b6d4' },
  entropy:          { label: 'Name Entropy',     max: 15, color: '#6b7280' },
};

function SignalBar({
  label,
  value,
  max,
  color,
  active,
}: {
  label: string;
  value: number;
  max: number;
  color: string;
  active: boolean;
}) {
  const pct = Math.min((value / max) * 100, 100);

  return (
    <div className="flex items-center gap-2 py-0.5">
      <div className="w-24 shrink-0 text-right">
        <span className={`text-[11px] ${active ? 'text-gray-200' : 'text-gray-600'}`}>
          {label}
        </span>
      </div>
      <div className="flex-1 h-2 bg-surface-3 rounded-full overflow-hidden">
        <div
          className="h-full rounded-full transition-all duration-700"
          style={{
            width: `${pct}%`,
            backgroundColor: active ? color : '#374151',
          }}
        />
      </div>
      <div className="w-8 text-right">
        <span className={`text-[11px] font-mono ${active ? 'text-gray-300' : 'text-gray-700'}`}>
          {value.toFixed(0)}
        </span>
      </div>
    </div>
  );
}

export function SignalBreakdown({ health }: Props) {
  const activeSet = new Set(health.activeSignals);

  return (
    <div className="px-3 py-2">
      <div className="text-[10px] text-gray-500 uppercase tracking-wider mb-2">
        Signal Breakdown
      </div>
      <div className="flex flex-col gap-0.5">
        {Object.entries(SIGNAL_CONFIG).map(([key, cfg]) => (
          <SignalBar
            key={key}
            label={cfg.label}
            value={health.metricScores[key] ?? 0}
            max={cfg.max}
            color={cfg.color}
            active={activeSet.has(key)}
          />
        ))}
      </div>
    </div>
  );
}
