/**
 * HealthScoreMeter — large central health score gauge.
 *
 * Displays the Chat Health Score (0–100) with colour coding, level label,
 * trend indicator, and animated arc gauge.
 */

import type { HealthSnapshot } from '../store/chatStore';

interface Props {
  health: HealthSnapshot;
}

const LEVEL_CONFIG: Record<
  HealthSnapshot['level'],
  { label: string; color: string; bg: string }
> = {
  healthy:      { label: 'Healthy',       color: '#4ade80', bg: 'rgba(74,222,128,0.1)' },
  elevated:     { label: 'Elevated',      color: '#facc15', bg: 'rgba(250,204,21,0.1)' },
  suspicious:   { label: 'Suspicious',    color: '#fb923c', bg: 'rgba(251,146,60,0.1)' },
  likely_attack:{ label: 'Likely Attack', color: '#f87171', bg: 'rgba(248,113,113,0.1)' },
  critical:     { label: 'CRITICAL',      color: '#ef4444', bg: 'rgba(239,68,68,0.15)' },
};

const TREND_ICON: Record<HealthSnapshot['trend'], string> = {
  worsening: '▼',
  stable:    '●',
  improving: '▲',
};

const TREND_COLOR: Record<HealthSnapshot['trend'], string> = {
  worsening: 'text-red-400',
  stable:    'text-gray-500',
  improving: 'text-green-400',
};

export function HealthScoreMeter({ health }: Props) {
  const cfg = LEVEL_CONFIG[health.level] ?? LEVEL_CONFIG.healthy;
  const radius = 54;
  const circumference = 2 * Math.PI * radius;
  // Arc goes from 0 (100% health) to full circumference (0% health)
  const dashOffset = circumference * (health.score / 100);

  return (
    <div className="flex flex-col items-center py-4 px-3 select-none">
      {/* Arc gauge */}
      <div className="relative w-36 h-36">
        <svg className="w-full h-full -rotate-90" viewBox="0 0 128 128">
          {/* Track */}
          <circle
            cx="64" cy="64" r={radius}
            fill="none"
            stroke="#1e1e2e"
            strokeWidth="10"
          />
          {/* Progress arc */}
          <circle
            cx="64" cy="64" r={radius}
            fill="none"
            stroke={cfg.color}
            strokeWidth="10"
            strokeLinecap="round"
            strokeDasharray={circumference}
            strokeDashoffset={circumference - dashOffset}
            style={{ transition: 'stroke-dashoffset 0.8s ease, stroke 0.5s ease' }}
          />
        </svg>

        {/* Score text */}
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span
            className="text-4xl font-black font-mono leading-none"
            style={{ color: cfg.color }}
          >
            {Math.round(health.score)}
          </span>
          <span className="text-gray-500 text-[10px] mt-0.5">/ 100</span>
        </div>
      </div>

      {/* Level label */}
      <div
        className="mt-2 px-3 py-0.5 rounded text-xs font-bold tracking-wider uppercase"
        style={{ color: cfg.color, background: cfg.bg }}
      >
        {cfg.label}
      </div>

      {/* Trend + duration */}
      <div className="flex items-center gap-2 mt-2 text-xs">
        <span className={TREND_COLOR[health.trend]}>
          {TREND_ICON[health.trend]} {health.trend}
        </span>
        {health.levelDuration > 1 && (
          <span className="text-gray-600">
            {health.levelDuration}s
          </span>
        )}
      </div>
    </div>
  );
}
