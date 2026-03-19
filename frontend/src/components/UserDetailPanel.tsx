/**
 * UserDetailPanel — click a username in chat to inspect them.
 *
 * Shows:
 *   - Account badges (mod, VIP, subscriber, new account)
 *   - Message count, account age, max threat score seen
 *   - All detection signals ever triggered
 *   - Last 25 messages in session
 *   - Flag history (previous detections)
 *   - Moderation actions taken against this user
 *   - Timeout and Ban action buttons
 */

import { useEffect, useRef, useState } from 'react';
import { useChatStore } from '../store/chatStore';
import type { WatchedUser } from '../store/chatStore';

// ---------------------------------------------------------------------------
// Types matching /api/users/{user_id} response
// ---------------------------------------------------------------------------

interface UserMessage {
  id: number;
  received_at: number;
  raw_text: string;
  emoji_count: number;
  url_count: number;
}

interface FlagEntry {
  id: number;
  flagged_at: number;
  channel: string;
  threat_score: number;
  signals: string;   // JSON array
  status: string;
}

interface ActionEntry {
  id: number;
  created_at: number;
  action_type: string;
  duration_seconds: number | null;
  reason: string;
  status: string;
  triggered_by: string;
  confidence: number | null;
}

interface UserProfile {
  user_id: string;
  username: string;
  account_age_days: number | null;
  is_subscriber: boolean;
  is_moderator: boolean;
  is_vip: boolean;
  total_messages: number;
  max_threat_score: number;
  reputation: number;
  signals_seen: string[];
  recent_messages: UserMessage[];
  flag_history: FlagEntry[];
  moderation_actions: ActionEntry[];
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatAge(days: number | null): string {
  if (days == null) return 'Unknown age';
  if (days < 1) return '< 1 day old';
  if (days < 30) return `${days}d old`;
  if (days < 365) return `${Math.floor(days / 30)}mo old`;
  return `${Math.floor(days / 365)}yr old`;
}

function formatTime(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function formatDate(ts: number): string {
  return new Date(ts * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function threatBadgeColor(score: number): string {
  if (score >= 75) return 'bg-red-900/50 text-red-300 border-red-700/50';
  if (score >= 60) return 'bg-orange-900/50 text-orange-300 border-orange-700/50';
  if (score >= 40) return 'bg-yellow-900/50 text-yellow-300 border-yellow-700/50';
  return 'bg-surface-3 text-gray-400 border-surface-3';
}

function parseSignals(signals: string): string[] {
  try { return JSON.parse(signals); } catch { return []; }
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function Badge({ label, color }: { label: string; color: string }) {
  return (
    <span className={`text-xs px-1.5 py-0.5 rounded border ${color}`}>{label}</span>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mt-3">
      <div className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1.5">{title}</div>
      {children}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Action buttons
// ---------------------------------------------------------------------------

interface ActionButtonsProps {
  userId: string;
  username: string;
  port: number;
  ipcSecret: string;
  onDone: () => void;
}

type PendingAction = { type: 'timeout' | 'ban' | 'warn'; duration?: number; defaultReason: string };

function ActionButtons({ userId, username, port, ipcSecret, onDone }: ActionButtonsProps) {
  const responseState = useChatStore((s) => s.responseState);
  const [state, setState] = useState<'idle' | 'confirm' | 'busy' | 'done'>('idle');
  const [pending, setPending] = useState<PendingAction | null>(null);
  const [reason, setReason] = useState('');
  const [lastAction, setLastAction] = useState<string>('');

  function startAction(type: 'timeout' | 'ban' | 'warn', duration?: number) {
    const defaultReason =
      type === 'timeout' ? `Timeout ${duration === 60 ? '1m' : duration === 300 ? '5m' : '1h'}`
      : type === 'ban' ? 'Manual ban'
      : 'Warning issued by moderator';
    setPending({ type, duration, defaultReason });
    setReason('');
    setState('confirm');
  }

  async function confirm() {
    if (!pending) return;
    setState('busy');
    const finalReason = reason.trim() || pending.defaultReason;
    try {
      const url = `http://127.0.0.1:${port}/api/moderation/${pending.type}`;
      const body = pending.type === 'timeout'
        ? { user_id: userId, username, duration_seconds: pending.duration ?? 300, reason: finalReason }
        : { user_id: userId, username, reason: finalReason };
      await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-IPC-Secret': ipcSecret },
        body: JSON.stringify(body),
      });
      setLastAction(
        pending.type === 'ban' ? 'Banned'
        : pending.type === 'warn' ? 'Warned'
        : `Timed out (${pending.duration}s)`
      );
      setState('done');
      setTimeout(onDone, 1500);
    } catch {
      setState('confirm');
    }
  }

  if (state === 'done') {
    return <div className="text-xs text-green-400 py-2">{lastAction} {responseState.dryRunMode ? '(dry-run)' : ''}</div>;
  }

  if (state === 'confirm' && pending) {
    return (
      <div className="space-y-2 mt-1">
        <div className="text-xs text-gray-400">
          <span className={pending.type === 'ban' ? 'text-red-400 font-bold' : pending.type === 'warn' ? 'text-yellow-400 font-bold' : 'text-orange-400 font-bold'}>
            {pending.type === 'timeout' ? `Timeout ${pending.duration === 60 ? '1m' : pending.duration === 300 ? '5m' : '1h'}` : pending.type.charAt(0).toUpperCase() + pending.type.slice(1)}
          </span>
          {' '}<span className="text-gray-300 font-mono">{username}</span>
        </div>
        <input
          autoFocus
          type="text"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') confirm(); if (e.key === 'Escape') setState('idle'); }}
          placeholder={pending.defaultReason}
          className="w-full text-xs bg-surface border border-surface-3 rounded px-2 py-1 text-gray-200 focus:outline-none focus:border-accent-purple placeholder:text-gray-600"
        />
        <div className="flex items-center gap-2">
          <button
            onClick={confirm}
            disabled={state === 'busy'}
            className={`text-xs px-2 py-1 rounded border font-bold disabled:opacity-40 transition-colors ${
              pending.type === 'ban' ? 'bg-red-900/50 border-red-700/60 text-red-300 hover:bg-red-800/70'
              : pending.type === 'warn' ? 'bg-yellow-900/40 border-yellow-700/50 text-yellow-300 hover:bg-yellow-800/60'
              : 'bg-orange-900/40 border-orange-700/50 text-orange-300 hover:bg-orange-800/60'
            }`}
          >
            Confirm
          </button>
          <button onClick={() => setState('idle')} className="text-xs text-gray-500 hover:text-gray-300">
            Cancel
          </button>
          {responseState.dryRunMode && <span className="text-xs text-gray-600 ml-auto">dry-run</span>}
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-wrap gap-2 mt-1">
      {(['60', '300', '3600'] as const).map((secs) => (
        <button
          key={secs}
          disabled={state === 'busy'}
          onClick={() => startAction('timeout', Number(secs))}
          className="text-xs px-2 py-1 bg-orange-900/40 hover:bg-orange-800/60 border border-orange-700/50 text-orange-300 rounded disabled:opacity-40 transition-colors"
        >
          Timeout {secs === '60' ? '1m' : secs === '300' ? '5m' : '1h'}
        </button>
      ))}
      <button
        disabled={state === 'busy'}
        onClick={() => startAction('warn')}
        className="text-xs px-2 py-1 bg-yellow-900/40 hover:bg-yellow-800/60 border border-yellow-700/50 text-yellow-300 rounded disabled:opacity-40 transition-colors"
        title="Send an anonymous Twitch warning"
      >
        Warn
      </button>
      <button
        disabled={state === 'busy'}
        onClick={() => startAction('ban')}
        className="text-xs px-2 py-1 bg-red-900/40 hover:bg-red-800/60 border border-red-700/50 text-red-300 rounded disabled:opacity-40 transition-colors"
      >
        Ban
      </button>
      {responseState.dryRunMode && (
        <span className="text-xs text-gray-600 self-center">dry-run</span>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Watch button
// ---------------------------------------------------------------------------

function WatchButton({
  userId,
  username,
  port,
  ipcSecret,
}: {
  userId: string;
  username: string;
  port: number;
  ipcSecret: string;
}) {
  const addWatchedUser = useChatStore((s) => s.addWatchedUser);
  const removeWatchedUser = useChatStore((s) => s.removeWatchedUser);
  const watchedUsers = useChatStore((s) => s.watchedUsers);
  const isWatching = watchedUsers.some((w) => w.user_id === userId);
  const [noteInput, setNoteInput] = useState('');
  const [showNote, setShowNote] = useState(false);
  const [busy, setBusy] = useState(false);

  async function watch() {
    setBusy(true);
    try {
      const res = await fetch(`http://127.0.0.1:${port}/api/watchlist`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-IPC-Secret': ipcSecret },
        body: JSON.stringify({ user_id: userId, username, note: noteInput.trim() }),
      });
      if (res.ok) {
        const entry: WatchedUser = {
          user_id: userId,
          username,
          added_at: Date.now() / 1000,
          note: noteInput.trim(),
          priority: 'normal',
        };
        addWatchedUser(entry);
        setShowNote(false);
        setNoteInput('');
      }
    } catch { /* ignore */ }
    setBusy(false);
  }

  async function unwatch() {
    setBusy(true);
    try {
      await fetch(`http://127.0.0.1:${port}/api/watchlist/${encodeURIComponent(userId)}`, {
        method: 'DELETE',
        headers: { 'X-IPC-Secret': ipcSecret },
      });
      removeWatchedUser(userId);
    } catch { /* ignore */ }
    setBusy(false);
  }

  if (isWatching) {
    return (
      <button
        disabled={busy}
        onClick={unwatch}
        className="text-xs px-2 py-1 bg-orange-900/30 hover:bg-orange-800/50 border border-orange-700/40 text-orange-400 rounded disabled:opacity-40 transition-colors"
      >
        {busy ? '…' : 'Watching — remove'}
      </button>
    );
  }

  if (showNote) {
    return (
      <div className="flex items-center gap-1.5">
        <input
          autoFocus
          type="text"
          value={noteInput}
          onChange={(e) => setNoteInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') watch(); if (e.key === 'Escape') setShowNote(false); }}
          placeholder="Optional note…"
          className="text-xs bg-surface-2 border border-surface-3 rounded px-2 py-0.5 text-white w-36 focus:outline-none focus:border-accent-purple"
        />
        <button
          disabled={busy}
          onClick={watch}
          className="text-xs text-accent-purple hover:text-white disabled:opacity-40"
        >
          {busy ? '…' : 'Watch'}
        </button>
        <button onClick={() => setShowNote(false)} className="text-xs text-gray-600 hover:text-gray-400">✕</button>
      </div>
    );
  }

  return (
    <button
      onClick={() => setShowNote(true)}
      className="text-xs px-2 py-1 bg-surface-2 hover:bg-surface-3 border border-surface-3 text-gray-400 hover:text-gray-200 rounded transition-colors"
    >
      + Watch
    </button>
  );
}

// ---------------------------------------------------------------------------
// Main panel
// ---------------------------------------------------------------------------

interface Props {
  port: number;
  ipcSecret: string;
}

export function UserDetailPanel({ port, ipcSecret }: Props) {
  const selectedUser = useChatStore((s) => s.selectedUser);
  const setSelectedUser = useChatStore((s) => s.setSelectedUser);
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  // Load profile when selectedUser changes
  useEffect(() => {
    if (!selectedUser) { setProfile(null); return; }
    setLoading(true);
    setError(null);
    fetch(`http://127.0.0.1:${port}/api/users/${encodeURIComponent(selectedUser.userId)}`, {
      headers: { 'X-IPC-Secret': ipcSecret },
    })
      .then((r) => {
        if (!r.ok) throw new Error(r.status === 404 ? 'No data yet for this user' : `Error ${r.status}`);
        return r.json();
      })
      .then((data: UserProfile) => { setProfile(data); setLoading(false); })
      .catch((e: Error) => { setError(e.message); setLoading(false); });
  }, [selectedUser?.userId]); // eslint-disable-line react-hooks/exhaustive-deps

  // Close on Escape
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') setSelectedUser(null);
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [setSelectedUser]);

  if (!selectedUser) return null;

  return (
    <div className="fixed inset-0 z-50 flex" onClick={(e) => { if (e.target === e.currentTarget) setSelectedUser(null); }}>
      {/* Backdrop */}
      <div className="flex-1 bg-black/40" onClick={() => setSelectedUser(null)} />

      {/* Panel */}
      <div
        ref={panelRef}
        className="w-80 h-full bg-surface-1 border-l border-surface-3 flex flex-col shadow-2xl overflow-hidden"
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-surface-3 shrink-0">
          <div className="flex items-center gap-2">
            <span
              className="font-semibold text-sm truncate max-w-[160px]"
              style={{ color: selectedUser.color ?? '#9147ff' }}
            >
              {selectedUser.username}
            </span>
          </div>
          <button
            onClick={() => setSelectedUser(null)}
            className="text-gray-500 hover:text-gray-200 text-lg leading-none"
            aria-label="Close"
          >
            ×
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-4 py-3">
          {loading && (
            <div className="text-xs text-gray-500 mt-4">Loading profile…</div>
          )}

          {error && (
            <div className="text-xs text-gray-500 mt-4">{error}</div>
          )}

          {profile && (
            <>
              {/* Badges row */}
              <div className="flex flex-wrap gap-1.5 mb-3">
                {profile.is_moderator && <Badge label="MOD" color="bg-green-900/50 text-green-300 border-green-700/50" />}
                {profile.is_vip && <Badge label="VIP" color="bg-purple-900/50 text-purple-300 border-purple-700/50" />}
                {profile.is_subscriber && <Badge label="SUB" color="bg-blue-900/50 text-blue-300 border-blue-700/50" />}
                {profile.account_age_days != null && profile.account_age_days < 7 && (
                  <Badge label="NEW" color="bg-yellow-900/50 text-yellow-300 border-yellow-700/50" />
                )}
                {profile.max_threat_score >= 40 && (
                  <Badge
                    label={`Score ${Math.round(profile.max_threat_score)}`}
                    color={threatBadgeColor(profile.max_threat_score)}
                  />
                )}
              </div>

              {/* Stats */}
              <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs mb-1">
                <div className="text-gray-500">Account age</div>
                <div className="text-gray-200">{formatAge(profile.account_age_days)}</div>
                <div className="text-gray-500">Messages</div>
                <div className="text-gray-200">{profile.total_messages.toLocaleString()}</div>
                <div className="text-gray-500">Reputation</div>
                <div className={profile.reputation >= 80 ? 'text-green-400' : profile.reputation >= 50 ? 'text-yellow-400' : 'text-red-400'}>
                  {profile.reputation}/100
                </div>
              </div>

              {/* Signals */}
              {profile.signals_seen.length > 0 && (
                <Section title="Signals detected">
                  <div className="flex flex-wrap gap-1">
                    {profile.signals_seen.map((s) => (
                      <span key={s} className="text-xs px-1.5 py-0.5 rounded bg-surface-3 text-yellow-300">
                        {s}
                      </span>
                    ))}
                  </div>
                </Section>
              )}

              {/* Actions */}
              <Section title="Actions">
                <ActionButtons
                  userId={profile.user_id}
                  username={profile.username}
                  port={port}
                  ipcSecret={ipcSecret}
                  onDone={() => setSelectedUser(null)}
                />
                <div className="mt-2">
                  <WatchButton
                    userId={profile.user_id}
                    username={profile.username}
                    port={port}
                    ipcSecret={ipcSecret}
                  />
                </div>
              </Section>

              {/* Recent messages */}
              <Section title={`Recent messages (${profile.recent_messages.length})`}>
                <div className="space-y-1">
                  {profile.recent_messages.map((m) => (
                    <div key={m.id} className="text-xs border-b border-surface-3 pb-1 last:border-0">
                      <div className="text-gray-500 mb-0.5">{formatTime(m.received_at)}</div>
                      <div className="text-gray-300 break-words leading-snug">{m.raw_text}</div>
                    </div>
                  ))}
                </div>
              </Section>

              {/* Flag history */}
              {profile.flag_history.length > 0 && (
                <Section title="Detection history">
                  <div className="space-y-2">
                    {profile.flag_history.map((f) => {
                      const currentChannel = useChatStore.getState().channel;
                      const isCrossChannel = currentChannel && f.channel && f.channel !== currentChannel;
                      return (
                        <div
                          key={f.id}
                          className={`text-xs border rounded px-2 py-1.5 ${isCrossChannel ? 'border-yellow-800/50 bg-yellow-950/20' : 'border-surface-3'}`}
                        >
                          <div className="flex items-center justify-between mb-1">
                            <div className="flex items-center gap-1.5">
                              <span className="text-gray-500">{formatDate(f.flagged_at)}</span>
                              {isCrossChannel && (
                                <span
                                  className="text-[10px] bg-yellow-900/40 text-yellow-500 rounded px-1 py-0.5"
                                  title={`Flagged on #${f.channel}`}
                                >
                                  #{f.channel}
                                </span>
                              )}
                            </div>
                            <span className={`font-mono font-bold ${f.threat_score >= 75 ? 'text-red-400' : f.threat_score >= 60 ? 'text-orange-400' : 'text-yellow-400'}`}>
                              {Math.round(f.threat_score)}
                            </span>
                          </div>
                          <div className="flex flex-wrap gap-1">
                            {parseSignals(f.signals).map((s) => (
                              <span key={s} className="bg-surface-3 rounded px-1 text-gray-400">{s}</span>
                            ))}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </Section>
              )}

              {/* Moderation actions */}
              {profile.moderation_actions.length > 0 && (
                <Section title="Moderation history">
                  <div className="space-y-1">
                    {profile.moderation_actions.map((a) => (
                      <div key={a.id} className="flex items-center gap-2 text-xs py-1 border-b border-surface-3 last:border-0">
                        <span className={`font-bold uppercase ${a.action_type === 'ban' ? 'text-red-400' : a.action_type === 'timeout' ? 'text-orange-400' : 'text-gray-400'}`}>
                          {a.action_type}
                        </span>
                        {a.duration_seconds && (
                          <span className="text-gray-500">{a.duration_seconds}s</span>
                        )}
                        <span className={`ml-auto ${a.status === 'completed' ? 'text-green-500' : a.status === 'failed' ? 'text-red-500' : 'text-gray-500'}`}>
                          {a.status}
                        </span>
                      </div>
                    ))}
                  </div>
                </Section>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
