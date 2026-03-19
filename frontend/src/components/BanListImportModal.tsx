/**
 * BanListImportModal — import a shared ban list (plaintext or JSON) and
 * enqueue bans for all resolved, non-whitelisted users.
 *
 * Flow:
 *  1. Paste or type a list of usernames (or a CommanderRoot JSON export)
 *  2. Click "Preview" — backend resolves via Helix, de-dups against whitelist
 *     and existing bans, returns targets
 *  3. Review: see target count, skip counts, first 50 names
 *  4. Click "Ban N users" → enqueues via transactional moderation engine
 *
 * Formats supported: plain text (one per line), JSON array, CommanderRoot export.
 * Respects dry-run mode — no Twitch calls until live mode is enabled.
 */

import { useState } from 'react';
import { useChatStore } from '../store/chatStore';

interface BanTarget {
  user_id: string;
  username: string;
}

interface PreviewResult {
  parsed: number;
  resolved: number;
  unresolved: number;
  skipped_whitelist: number;
  skipped_already_banned: number;
  targets: BanTarget[];
}

interface Props {
  port: number;
  ipcSecret: string;
  onClose: () => void;
}

type Phase = 'input' | 'previewing' | 'preview' | 'executing' | 'done';

export function BanListImportModal({ port, ipcSecret, onClose }: Props) {
  const responseState = useChatStore((s) => s.responseState);
  const [phase, setPhase] = useState<Phase>('input');
  const [text, setText] = useState('');
  const [reason, setReason] = useState('Shared ban list import');
  const [preview, setPreview] = useState<PreviewResult | null>(null);
  const [enqueued, setEnqueued] = useState(0);
  const [error, setError] = useState('');

  async function runPreview() {
    if (!text.trim()) return;
    setPhase('previewing');
    setError('');
    try {
      const res = await fetch(`http://127.0.0.1:${port}/api/moderation/import-banlist/preview`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-IPC-Secret': ipcSecret },
        body: JSON.stringify({ text }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setError(data.detail ?? `Error ${res.status}`);
        setPhase('input');
        return;
      }
      const data: PreviewResult = await res.json();
      setPreview(data);
      setPhase('preview');
    } catch {
      setError('Network error');
      setPhase('input');
    }
  }

  async function execute() {
    if (!preview || preview.targets.length === 0) return;
    setPhase('executing');
    try {
      const res = await fetch(`http://127.0.0.1:${port}/api/moderation/import-banlist/execute`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-IPC-Secret': ipcSecret },
        body: JSON.stringify({
          user_ids: preview.targets.map((t) => t.user_id),
          reason: reason.trim() || 'Shared ban list import',
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
      <div className="w-[520px] max-h-[80vh] bg-surface-1 border border-surface-3 rounded-lg shadow-2xl flex flex-col overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-surface-3 shrink-0">
          <div className="flex items-center gap-2">
            <span className="text-gray-300 font-bold text-sm">Ban List Import</span>
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
              <p className="text-xs text-gray-500">
                Paste a ban list — one username per line, or a JSON array from CommanderRoot / Twitch exports.
                Users on your whitelist and already-banned users are automatically skipped.
              </p>

              <div>
                <label className="text-xs text-gray-400 block mb-1">Ban list</label>
                <textarea
                  autoFocus
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  rows={8}
                  placeholder={'username1\nusername2\n…  or  ["user1","user2",…]'}
                  className="w-full text-xs bg-surface border border-surface-3 rounded px-3 py-2 text-gray-200 focus:outline-none focus:border-accent-purple font-mono placeholder:text-gray-600 resize-none"
                />
              </div>

              <div>
                <label className="text-xs text-gray-400 block mb-1">Reason (shown in mod log)</label>
                <input
                  type="text"
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  className="w-full text-xs bg-surface border border-surface-3 rounded px-2 py-1 text-gray-400 focus:outline-none focus:border-accent-purple"
                />
              </div>

              {error && <div className="text-xs text-red-400">{error}</div>}

              <button
                onClick={runPreview}
                disabled={phase === 'previewing' || !text.trim()}
                className="w-full text-sm py-1.5 bg-surface-2 hover:bg-surface-3 border border-surface-3 text-gray-300 rounded disabled:opacity-40 transition-colors"
              >
                {phase === 'previewing' ? 'Resolving usernames…' : 'Preview'}
              </button>
            </div>
          )}

          {/* Preview phase */}
          {phase === 'preview' && preview && (
            <div className="space-y-3">
              {/* Summary */}
              <div className="flex items-start justify-between">
                <div className="space-y-0.5">
                  <div className="text-sm text-gray-200">
                    <span className="font-bold text-white">{preview.targets.length}</span> user
                    {preview.targets.length !== 1 ? 's' : ''} to ban
                  </div>
                  <div className="text-[10px] text-gray-500 space-y-0.5">
                    <div>{preview.parsed} parsed · {preview.resolved} resolved on Twitch</div>
                    {preview.unresolved > 0 && <div className="text-yellow-600">{preview.unresolved} not found on Twitch</div>}
                    {preview.skipped_whitelist > 0 && <div className="text-green-700">{preview.skipped_whitelist} skipped (whitelist)</div>}
                    {preview.skipped_already_banned > 0 && <div className="text-gray-600">{preview.skipped_already_banned} already banned</div>}
                  </div>
                </div>
                <button onClick={() => setPhase('input')} className="text-xs text-gray-500 hover:text-gray-300 shrink-0">
                  ← Back
                </button>
              </div>

              {preview.targets.length === 0 ? (
                <div className="text-xs text-gray-600 py-4 text-center">
                  No new users to ban after deduplication.
                </div>
              ) : (
                <>
                  {/* Target list (capped at 50) */}
                  <div className="border border-surface-3 rounded overflow-hidden">
                    {preview.targets.slice(0, 50).map((t) => (
                      <div key={t.user_id} className="flex items-center gap-2 px-3 py-1 border-b border-surface-3 last:border-0 text-xs">
                        <span className="text-gray-200 font-mono w-36 truncate shrink-0">{t.username}</span>
                        <span className="text-gray-600 text-[10px]">id:{t.user_id}</span>
                      </div>
                    ))}
                    {preview.targets.length > 50 && (
                      <div className="px-3 py-1.5 text-xs text-gray-600">
                        …and {preview.targets.length - 50} more
                      </div>
                    )}
                  </div>

                  <button
                    onClick={execute}
                    className="w-full text-sm py-2 rounded font-bold bg-red-900/50 hover:bg-red-800/70 border border-red-700/60 text-red-300 transition-colors"
                  >
                    Ban {preview.targets.length} user{preview.targets.length !== 1 ? 's' : ''}
                    {responseState.dryRunMode ? ' (dry-run)' : ''}
                  </button>
                </>
              )}
            </div>
          )}

          {/* Executing */}
          {phase === 'executing' && (
            <div className="text-center py-8 text-gray-400 text-sm">Queuing bans…</div>
          )}

          {/* Done */}
          {phase === 'done' && (
            <div className="text-center py-8 space-y-2">
              <div className="text-green-400 text-lg">✓</div>
              <div className="text-sm text-gray-200">
                Queued <span className="font-bold">{enqueued}</span> ban{enqueued !== 1 ? 's' : ''}
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
