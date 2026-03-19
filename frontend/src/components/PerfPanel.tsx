/**
 * PerfPanel — collapsible backend performance monitor.
 *
 * Displays live telemetry from the Python backend:
 *   msg/min  |  tick p95  |  queue depth  |  memory  |  WS clients
 *
 * Data arrives in the `perf` key of every `health_update` WebSocket message (1Hz).
 * Intended for debugging and stream monitoring — collapsed by default.
 */

import { useState } from 'react';
import { useChatStore, type PerfSnapshot } from '../store/chatStore';

// ---------------------------------------------------------------------------
// Tick latency badge
// ---------------------------------------------------------------------------

function TickBadge({ ms }: { ms: number | null }) {
  if (ms === null) return <span className="text-gray-600">—</span>;
  const color =
    ms >= 40 ? 'text-red-400 bg-red-950/40' :
    ms >= 25 ? 'text-yellow-400 bg-yellow-950/30' :
               'text-green-400 bg-green-950/30';
  return (
    <span className={`font-mono text-[11px] px-1.5 py-0.5 rounded ${color}`}>
      {ms.toFixed(1)}ms
    </span>
  );
}

// ---------------------------------------------------------------------------
// Queue depth bar
// ---------------------------------------------------------------------------

function QueueBar({ depth }: { depth: number }) {
  const pct = Math.min((depth / 10000) * 100, 100);
  const barColor =
    pct >= 75 ? 'bg-red-500' :
    pct >= 40 ? 'bg-yellow-500' :
                'bg-green-500';
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1.5 bg-surface-3 rounded overflow-hidden">
        <div className={`h-full rounded transition-all ${barColor}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="font-mono text-[11px] text-gray-400 w-10 text-right">{depth}</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Row helper
// ---------------------------------------------------------------------------

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="text-[11px] text-gray-500">{label}</span>
      <span className="text-[11px] text-gray-300">{children}</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Panel
// ---------------------------------------------------------------------------

function PerfContent({ perf }: { perf: PerfSnapshot }) {
  return (
    <div className="px-3 py-2 space-y-2">
      <Row label="Msg / min">
        <span className="font-mono">{perf.msg_per_min}</span>
      </Row>
      <Row label="Tick p50">
        <TickBadge ms={perf.tick_p50_ms} />
      </Row>
      <Row label="Tick p95">
        <TickBadge ms={perf.tick_p95_ms} />
      </Row>
      <Row label="Tick p99">
        <TickBadge ms={perf.tick_p99_ms} />
      </Row>
      <div>
        <div className="text-[11px] text-gray-500 mb-1">Queue depth</div>
        <QueueBar depth={perf.queue_depth} />
      </div>
      <Row label="Memory">
        {perf.memory_mb != null ? (
          <span className={`font-mono ${perf.memory_mb > 850 ? 'text-red-400' : perf.memory_mb > 700 ? 'text-yellow-400' : ''}`}>
            {perf.memory_mb.toFixed(0)} MB
          </span>
        ) : '—'}
      </Row>
      <Row label="WS clients">
        <span className="font-mono">{perf.ws_clients}</span>
      </Row>
    </div>
  );
}

export function PerfPanel() {
  const perf = useChatStore((s) => s.perf);
  const [open, setOpen] = useState(false);

  if (!perf) return null;

  return (
    <div className="border-t border-surface-3">
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center justify-between px-3 py-1.5 text-[10px] text-gray-500 uppercase tracking-wider hover:text-gray-300 transition-colors"
      >
        <span>Performance</span>
        <span>{open ? '▲' : '▼'}</span>
      </button>
      {open && <PerfContent perf={perf} />}
    </div>
  );
}
