/**
 * FollowerAuditModal — scan recent followers against the KnownBotRegistry
 * and remove suspected bot followers.
 *
 * Flow:
 *  1. Enter max_followers count, click "Scan"
 *  2. Backend fetches followers from Helix, checks each against Bloom filter
 *  3. Results: list of suspected bots with username + follow date
 *  4. Click "Remove N followers" → backend deletes them via DELETE /channels/followers
 *
 * Requires moderator:read:followers + moderator:manage:followers scopes.
 * If the KnownBotRegistry isn't loaded yet, the scan will return 0 results.
 */

import { useState } from 'react';

interface BotFollower {
  user_id: string;
  username: string;
  display_name: string;
  followed_at: string;
}

interface AuditResult {
  scanned: number;
  suspected_bots: number;
  registry_loaded: boolean;
  registry_size: number;
  followers: BotFollower[];
}

interface Props {
  port: number;
  ipcSecret: string;
  onClose: () => void;
}

type Phase = 'input' | 'scanning' | 'results' | 'removing' | 'done';

export function FollowerAuditModal({ port, ipcSecret, onClose }: Props) {
  const [phase, setPhase] = useState<Phase>('input');
  const [maxFollowers, setMaxFollowers] = useState(500);
  const [result, setResult] = useState<AuditResult | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [removeResult, setRemoveResult] = useState<{ removed: number; failed: number } | null>(null);
  const [confirmRemove, setConfirmRemove] = useState(false);

  async function scan() {
    setPhase('scanning');
    setResult(null);
    setSelected(new Set());
    setConfirmRemove(false);
    try {
      const res = await fetch(
        `http://127.0.0.1:${port}/api/followers/audit?max_followers=${maxFollowers}`,
        { headers: { 'X-IPC-Secret': ipcSecret } },
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: AuditResult = await res.json();
      setResult(data);
      // Select all by default
      setSelected(new Set(data.followers.map((f) => f.user_id)));
      setPhase('results');
    } catch {
      setPhase('input');
    }
  }

  async function removeSelected() {
    if (!result || selected.size === 0) return;
    setPhase('removing');
    setConfirmRemove(false);
    try {
      const res = await fetch(`http://127.0.0.1:${port}/api/followers/remove`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-IPC-Secret': ipcSecret },
        body: JSON.stringify({ user_ids: [...selected] }),
      });
      const data = await res.json();
      setRemoveResult({ removed: data.removed ?? 0, failed: data.failed ?? 0 });
      setPhase('done');
    } catch {
      setPhase('results');
    }
  }

  function toggleUser(userId: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(userId)) next.delete(userId);
      else next.add(userId);
      return next;
    });
  }

  function formatDate(iso: string) {
    if (!iso) return '';
    try {
      return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
    } catch {
      return iso;
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="w-[520px] max-h-[80vh] bg-surface-1 border border-surface-3 rounded-lg shadow-2xl flex flex-col overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-surface-3 shrink-0">
          <span className="text-gray-300 font-bold text-sm">Follower Bot Audit</span>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-200 text-lg leading-none">×</button>
        </div>

        <div className="flex-1 overflow-y-auto p-4">
          {/* Input phase */}
          {(phase === 'input' || phase === 'scanning') && (
            <div className="space-y-4">
              <p className="text-xs text-gray-500">
                Scans your most recent followers against the KnownBotRegistry (12M+ known bot
                usernames). Suspected bots can be removed from your follower list.
              </p>

              <div className="flex items-center gap-3">
                <span className="text-xs text-gray-400 shrink-0">Scan up to</span>
                <select
                  value={maxFollowers}
                  onChange={(e) => setMaxFollowers(Number(e.target.value))}
                  className="text-xs bg-surface border border-surface-3 rounded px-2 py-1 text-gray-300 focus:outline-none"
                >
                  <option value={100}>100 followers</option>
                  <option value={200}>200 followers</option>
                  <option value={500}>500 followers</option>
                  <option value={1000}>1 000 followers</option>
                </select>
                <span className="text-xs text-gray-600">(most recent)</span>
              </div>

              <div className="text-[10px] text-gray-600 space-y-0.5">
                <div>Requires: <span className="text-gray-500 font-mono">moderator:read:followers</span></div>
                <div>Removal requires: <span className="text-gray-500 font-mono">moderator:manage:followers</span></div>
              </div>

              <button
                onClick={scan}
                disabled={phase === 'scanning'}
                className="w-full text-sm py-1.5 bg-surface-2 hover:bg-surface-3 border border-surface-3 text-gray-300 rounded disabled:opacity-40 transition-colors"
              >
                {phase === 'scanning' ? 'Scanning…' : 'Scan Followers'}
              </button>
            </div>
          )}

          {/* Results phase */}
          {phase === 'results' && result && (
            <div className="space-y-3">
              {/* Summary */}
              <div className="flex items-center justify-between">
                <div>
                  <span className="text-sm text-gray-200">
                    <span className="font-bold text-white">{result.suspected_bots}</span> suspected bot
                    {result.suspected_bots !== 1 ? 's' : ''} of{' '}
                    <span className="text-gray-400">{result.scanned}</span> scanned
                  </span>
                  {!result.registry_loaded && (
                    <div className="text-[10px] text-yellow-500 mt-0.5">
                      Warning: bot registry not yet loaded — results may be incomplete
                    </div>
                  )}
                  {result.registry_loaded && (
                    <div className="text-[10px] text-gray-600 mt-0.5">
                      Registry: {result.registry_size.toLocaleString()} known bots
                    </div>
                  )}
                </div>
                <button onClick={() => setPhase('input')} className="text-xs text-gray-500 hover:text-gray-300">
                  ← Back
                </button>
              </div>

              {result.followers.length === 0 ? (
                <div className="text-xs text-gray-600 py-6 text-center">
                  No suspected bots found in the last {result.scanned} followers.
                </div>
              ) : (
                <>
                  {/* Select all toggle */}
                  <div className="flex items-center gap-2 text-[10px] text-gray-500">
                    <input
                      type="checkbox"
                      checked={selected.size === result.followers.length}
                      onChange={(e) =>
                        setSelected(
                          e.target.checked
                            ? new Set(result.followers.map((f) => f.user_id))
                            : new Set(),
                        )
                      }
                      className="accent-accent-purple"
                    />
                    Select all ({result.followers.length})
                  </div>

                  <div className="border border-surface-3 rounded overflow-hidden">
                    {result.followers.slice(0, 50).map((f) => (
                      <label
                        key={f.user_id}
                        className="flex items-center gap-2 px-3 py-1.5 border-b border-surface-3 last:border-0 text-xs cursor-pointer hover:bg-surface-2"
                      >
                        <input
                          type="checkbox"
                          checked={selected.has(f.user_id)}
                          onChange={() => toggleUser(f.user_id)}
                          className="accent-accent-purple shrink-0"
                        />
                        <span className="text-gray-200 font-mono w-32 truncate shrink-0">
                          {f.display_name || f.username}
                        </span>
                        <span className="text-gray-600 text-[10px]">
                          followed {formatDate(f.followed_at)}
                        </span>
                      </label>
                    ))}
                    {result.followers.length > 50 && (
                      <div className="px-3 py-1.5 text-xs text-gray-600">
                        …and {result.followers.length - 50} more (all selectable above)
                      </div>
                    )}
                  </div>

                  {selected.size > 0 && (
                    confirmRemove ? (
                      <div className="flex items-center gap-2">
                        <span className="text-xs text-red-400">
                          Remove {selected.size} follower{selected.size !== 1 ? 's' : ''}?
                        </span>
                        <button
                          onClick={removeSelected}
                          className="text-[10px] px-2 py-0.5 bg-red-900/50 border border-red-700/60 text-red-300 rounded hover:bg-red-800/70"
                        >
                          Yes, remove
                        </button>
                        <button
                          onClick={() => setConfirmRemove(false)}
                          className="text-[10px] text-gray-500 hover:text-gray-300"
                        >
                          Cancel
                        </button>
                      </div>
                    ) : (
                      <button
                        onClick={() => setConfirmRemove(true)}
                        className="w-full text-sm py-2 rounded font-bold bg-red-900/40 hover:bg-red-800/60 border border-red-700/50 text-red-300 transition-colors"
                      >
                        Remove {selected.size} follower{selected.size !== 1 ? 's' : ''}
                      </button>
                    )
                  )}
                </>
              )}
            </div>
          )}

          {/* Removing */}
          {phase === 'removing' && (
            <div className="text-center py-8 text-gray-400 text-sm">
              Removing followers… this may take a moment.
            </div>
          )}

          {/* Done */}
          {phase === 'done' && removeResult && (
            <div className="text-center py-8 space-y-2">
              <div className="text-green-400 text-lg">✓</div>
              <div className="text-sm text-gray-200">
                Removed <span className="font-bold">{removeResult.removed}</span> follower
                {removeResult.removed !== 1 ? 's' : ''}
                {removeResult.failed > 0 && (
                  <span className="text-red-400 ml-1">({removeResult.failed} failed)</span>
                )}
              </div>
              {removeResult.failed > 0 && (
                <div className="text-[10px] text-gray-600">
                  Failures may indicate missing <span className="font-mono">moderator:manage:followers</span> scope.
                  Use Re-auth to grant it.
                </div>
              )}
              <button onClick={onClose} className="mt-4 text-xs text-gray-500 hover:text-gray-300">
                Close
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
