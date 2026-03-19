/**
 * NukeModal — retroactive bulk moderation by phrase or regex.
 *
 * Flow:
 *  1. Enter pattern + options (lookback, action, duration)
 *  2. Click "Preview" — fetches matching users from recent messages
 *  3. Review target list
 *  4. Click "Execute" — enqueues timeout/ban for all matched users
 *
 * Triggered via a "Nuke" button in the dashboard header.
 * All actions go through the transactional mod queue and respect dry-run mode.
 */

import { useState } from 'react';
import { useChatStore } from '../store/chatStore';

interface NukeTarget {
  user_id: string;
  username: string;
  match_count: number;
  sample_message: string;
}

interface Props {
  port: number;
  ipcSecret: string;
  onClose: () => void;
}

type Phase = 'input' | 'previewing' | 'preview' | 'executing' | 'done';

export function NukeModal({ port, ipcSecret, onClose }: Props) {
  const responseState = useChatStore((s) => s.responseState);
  const [phase, setPhase] = useState<Phase>('input');
  const [pattern, setPattern] = useState('');
  const [useRegex, setUseRegex] = useState(false);
  const [lookback, setLookback] = useState(300);
  const [action, setAction] = useState<'timeout' | 'ban'>('timeout');
  const [duration, setDuration] = useState(300);
  const [reason, setReason] = useState('');
  const [targets, setTargets] = useState<NukeTarget[]>([]);
  const [enqueued, setEnqueued] = useState(0);
  const [patternError, setPatternError] = useState('');

  function validatePattern(): boolean {
    if (!pattern.trim()) { setPatternError('Pattern required'); return false; }
    if (useRegex) {
      try { new RegExp(pattern); setPatternError(''); return true; }
      catch (e) { setPatternError(String(e)); return false; }
    }
    setPatternError('');
    return true;
  }

  async function preview() {
    if (!validatePattern()) return;
    setPhase('previewing');
    try {
      const res = await fetch(`http://127.0.0.1:${port}/api/moderation/nuke/preview`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-IPC-Secret': ipcSecret },
        body: JSON.stringify({ pattern, use_regex: useRegex, lookback_seconds: lookback }),
      });
      const data = await res.json();
      setTargets(data.targets ?? []);
      setPhase('preview');
    } catch {
      setPhase('input');
    }
  }

  async function execute() {
    setPhase('executing');
    try {
      const res = await fetch(`http://127.0.0.1:${port}/api/moderation/nuke/execute`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-IPC-Secret': ipcSecret },
        body: JSON.stringify({
          pattern,
          use_regex: useRegex,
          lookback_seconds: lookback,
          action,
          duration_seconds: duration,
          reason: reason.trim() || `Nuke: ${pattern.slice(0, 80)}`,
        }),
      });
      const data = await res.json();
      setEnqueued(data.enqueued ?? 0);
      setPhase('done');
    } catch {
      setPhase('preview');
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="w-[480px] max-h-[80vh] bg-surface-1 border border-surface-3 rounded-lg shadow-2xl flex flex-col overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-surface-3 shrink-0">
          <div className="flex items-center gap-2">
            <span className="text-red-400 font-bold text-sm">☢ Nuke Tool</span>
            {responseState.dryRunMode && (
              <span className="text-[10px] text-gray-600 italic">dry-run</span>
            )}
          </div>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-200 text-lg leading-none">×</button>
        </div>

        <div className="flex-1 overflow-y-auto p-4">
          {/* Input phase */}
          {(phase === 'input' || phase === 'previewing') && (
            <div className="space-y-3">
              <div>
                <label className="text-xs text-gray-400 block mb-1">Pattern</label>
                <input
                  autoFocus
                  type="text"
                  value={pattern}
                  onChange={(e) => setPattern(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter') preview(); }}
                  placeholder={useRegex ? 'Regex pattern…' : 'Phrase to match…'}
                  className="w-full text-sm bg-surface border border-surface-3 rounded px-3 py-1.5 text-gray-200 focus:outline-none focus:border-accent-purple font-mono placeholder:text-gray-600"
                />
                {patternError && <div className="text-xs text-red-400 mt-0.5">{patternError}</div>}
              </div>

              <div className="flex items-center gap-4 flex-wrap">
                <label className="flex items-center gap-1.5 text-xs text-gray-400 cursor-pointer">
                  <input type="checkbox" checked={useRegex} onChange={(e) => setUseRegex(e.target.checked)} className="accent-accent-purple" />
                  Use regex
                </label>

                <div className="flex items-center gap-1.5">
                  <span className="text-xs text-gray-500">Lookback:</span>
                  <select
                    value={lookback}
                    onChange={(e) => setLookback(Number(e.target.value))}
                    className="text-xs bg-surface border border-surface-3 rounded px-1.5 py-0.5 text-gray-300 focus:outline-none"
                  >
                    <option value={60}>1 min</option>
                    <option value={300}>5 min</option>
                    <option value={600}>10 min</option>
                    <option value={1800}>30 min</option>
                    <option value={3600}>1 hour</option>
                    <option value={86400}>24 hours</option>
                  </select>
                </div>

                <div className="flex items-center gap-1.5">
                  <span className="text-xs text-gray-500">Action:</span>
                  <select
                    value={action}
                    onChange={(e) => setAction(e.target.value as 'timeout' | 'ban')}
                    className="text-xs bg-surface border border-surface-3 rounded px-1.5 py-0.5 text-gray-300 focus:outline-none"
                  >
                    <option value="timeout">Timeout</option>
                    <option value="ban">Ban</option>
                  </select>
                </div>

                {action === 'timeout' && (
                  <div className="flex items-center gap-1.5">
                    <span className="text-xs text-gray-500">Duration:</span>
                    <select
                      value={duration}
                      onChange={(e) => setDuration(Number(e.target.value))}
                      className="text-xs bg-surface border border-surface-3 rounded px-1.5 py-0.5 text-gray-300 focus:outline-none"
                    >
                      <option value={60}>1 min</option>
                      <option value={300}>5 min</option>
                      <option value={600}>10 min</option>
                      <option value={3600}>1 hour</option>
                      <option value={86400}>24 hours</option>
                    </select>
                  </div>
                )}
              </div>

              <div>
                <label className="text-xs text-gray-400 block mb-1">Reason (optional)</label>
                <input
                  type="text"
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  placeholder="Reason shown in moderation log"
                  className="w-full text-xs bg-surface border border-surface-3 rounded px-2 py-1 text-gray-400 focus:outline-none focus:border-accent-purple placeholder:text-gray-600"
                />
              </div>

              <button
                onClick={preview}
                disabled={phase === 'previewing' || !pattern.trim()}
                className="w-full text-sm py-1.5 bg-surface-2 hover:bg-surface-3 border border-surface-3 text-gray-300 rounded disabled:opacity-40 transition-colors"
              >
                {phase === 'previewing' ? 'Searching…' : 'Preview Matches'}
              </button>
            </div>
          )}

          {/* Preview phase */}
          {phase === 'preview' && (
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <div>
                  <span className="text-sm text-gray-200">
                    Found <span className="font-bold text-white">{targets.length}</span> user{targets.length !== 1 ? 's' : ''}
                  </span>
                  <div className="text-xs text-gray-500 mt-0.5 font-mono">"{pattern}"</div>
                </div>
                <button onClick={() => setPhase('input')} className="text-xs text-gray-500 hover:text-gray-300">← Back</button>
              </div>

              {targets.length === 0 ? (
                <div className="text-xs text-gray-600 py-4 text-center">No matches in the lookback window.</div>
              ) : (
                <div className="border border-surface-3 rounded overflow-hidden">
                  {targets.slice(0, 30).map((t) => (
                    <div key={t.user_id} className="flex items-center gap-2 px-3 py-1.5 border-b border-surface-3 last:border-0 text-xs">
                      <span className="text-gray-200 font-mono w-28 truncate shrink-0">{t.username}</span>
                      <span className="text-gray-600 shrink-0">{t.match_count}×</span>
                      <span className="text-gray-500 truncate italic">"{t.sample_message}"</span>
                    </div>
                  ))}
                  {targets.length > 30 && (
                    <div className="px-3 py-1.5 text-xs text-gray-600">…and {targets.length - 30} more</div>
                  )}
                </div>
              )}

              {targets.length > 0 && (
                <button
                  onClick={execute}
                  disabled={phase === 'executing'}
                  className={`w-full text-sm py-2 rounded font-bold transition-colors ${
                    action === 'ban'
                      ? 'bg-red-900/50 hover:bg-red-800/70 border border-red-700/60 text-red-300'
                      : 'bg-orange-900/40 hover:bg-orange-800/60 border border-orange-700/50 text-orange-300'
                  } disabled:opacity-40`}
                >
                  {action === 'ban'
                    ? `Ban ${targets.length} user${targets.length !== 1 ? 's' : ''}${responseState.dryRunMode ? ' (dry-run)' : ''}`
                    : `Timeout ${targets.length} user${targets.length !== 1 ? 's' : ''}${responseState.dryRunMode ? ' (dry-run)' : ''}`}
                </button>
              )}
            </div>
          )}

          {/* Executing */}
          {phase === 'executing' && (
            <div className="text-center py-8 text-gray-400 text-sm">Queuing actions…</div>
          )}

          {/* Done */}
          {phase === 'done' && (
            <div className="text-center py-8 space-y-2">
              <div className="text-green-400 text-lg">✓</div>
              <div className="text-sm text-gray-200">
                Queued <span className="font-bold">{enqueued}</span> {action}(s)
                {responseState.dryRunMode ? ' (dry-run — no Twitch calls)' : ''}
              </div>
              <button onClick={onClose} className="mt-4 text-xs text-gray-500 hover:text-gray-300">Close</button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
