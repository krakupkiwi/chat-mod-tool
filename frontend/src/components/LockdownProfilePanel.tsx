/**
 * LockdownProfilePanel — quick-access panel for applying named chat-mode
 * profiles. Lives below ChatModeBar in the chat column.
 *
 * Shows stored profiles as pill buttons. Clicking applies all non-null
 * mode settings in one shot.  A compact inline form lets the user create
 * new profiles without leaving the dashboard.  Profiles marked
 * auto_on_raid fire automatically on the backend when a raid arrives.
 */

import { useEffect, useState } from 'react';

interface Profile {
  id: number;
  name: string;
  auto_on_raid: boolean;
  emote_only: number | null;
  sub_only: number | null;
  unique_chat: number | null;
  slow_mode: number | null;
  slow_mode_wait_time: number | null;
  followers_only: number | null;
  followers_only_duration: number | null;
}

interface Props {
  port: number;
  ipcSecret: string;
}

const MODE_LABELS: Record<string, string> = {
  emote_only: 'Emote',
  sub_only: 'Sub',
  unique_chat: 'Unique',
  slow_mode: 'Slow',
  followers_only: 'Follower',
};

function profileSummary(p: Profile): string {
  const parts: string[] = [];
  const modes: [string, number | null][] = [
    ['emote_only', p.emote_only],
    ['sub_only', p.sub_only],
    ['unique_chat', p.unique_chat],
    ['slow_mode', p.slow_mode],
    ['followers_only', p.followers_only],
  ];
  for (const [key, val] of modes) {
    if (val === null) continue;
    parts.push(`${val ? '+' : '−'}${MODE_LABELS[key]}`);
  }
  return parts.join(' ') || 'no modes';
}

export function LockdownProfilePanel({ port, ipcSecret }: Props) {
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [applying, setApplying] = useState<number | null>(null);
  const [applied, setApplied] = useState<number | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [collapsed, setCollapsed] = useState(false);

  // Create-form state
  const [newName, setNewName] = useState('');
  const [newRaid, setNewRaid] = useState(false);
  const [newModes, setNewModes] = useState<Record<string, number | null>>({
    emote_only: null, sub_only: null, unique_chat: null,
    slow_mode: null, followers_only: null,
  });
  const [newSlowWait, setNewSlowWait] = useState(30);
  const [newFollowerDur, setNewFollowerDur] = useState(0);
  const [creating, setCreating] = useState(false);

  async function load() {
    try {
      const res = await fetch(`http://127.0.0.1:${port}/api/profiles`, {
        headers: { 'X-IPC-Secret': ipcSecret },
      });
      if (res.ok) {
        const data = await res.json();
        setProfiles(data.profiles ?? []);
      }
    } catch { /* ignore */ }
  }

  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function applyProfile(id: number) {
    setApplying(id);
    try {
      await fetch(`http://127.0.0.1:${port}/api/profiles/${id}/apply`, {
        method: 'POST',
        headers: { 'X-IPC-Secret': ipcSecret },
      });
      setApplied(id);
      setTimeout(() => setApplied(null), 2000);
    } catch { /* ignore */ }
    setApplying(null);
  }

  async function deleteProfile(id: number) {
    await fetch(`http://127.0.0.1:${port}/api/profiles/${id}`, {
      method: 'DELETE',
      headers: { 'X-IPC-Secret': ipcSecret },
    });
    setProfiles((prev) => prev.filter((p) => p.id !== id));
  }

  function cycleModeValue(mode: string) {
    setNewModes((prev) => {
      const cur = prev[mode];
      // null → 1 → 0 → null
      const next = cur === null ? 1 : cur === 1 ? 0 : null;
      return { ...prev, [mode]: next };
    });
  }

  async function submitCreate() {
    if (!newName.trim()) return;
    setCreating(true);
    try {
      const body: Record<string, unknown> = {
        name: newName.trim(),
        auto_on_raid: newRaid,
        slow_mode_wait_time: newModes.slow_mode === 1 ? newSlowWait : null,
        followers_only_duration: newModes.followers_only === 1 ? newFollowerDur : null,
      };
      for (const key of Object.keys(newModes)) {
        body[key] = newModes[key] === null ? null : Boolean(newModes[key]);
      }
      const res = await fetch(`http://127.0.0.1:${port}/api/profiles`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-IPC-Secret': ipcSecret },
        body: JSON.stringify(body),
      });
      if (res.ok) {
        setNewName('');
        setNewRaid(false);
        setNewModes({ emote_only: null, sub_only: null, unique_chat: null, slow_mode: null, followers_only: null });
        setShowCreate(false);
        load();
      }
    } catch { /* ignore */ }
    setCreating(false);
  }

  const modeValueLabel = (v: number | null) =>
    v === null ? '—' : v === 1 ? 'ON' : 'OFF';
  const modeValueColor = (v: number | null) =>
    v === null ? 'text-gray-600' : v === 1 ? 'text-green-400' : 'text-red-400';

  if (profiles.length === 0 && !showCreate) {
    return (
      <div className="px-3 py-1.5 border-b border-surface-3 flex items-center justify-between shrink-0">
        <span className="text-[10px] text-gray-600">Lockdown profiles</span>
        <button
          onClick={() => setShowCreate(true)}
          className="text-[10px] text-gray-500 hover:text-accent-purple"
        >
          + New profile
        </button>
      </div>
    );
  }

  return (
    <div className="border-b border-surface-3 shrink-0 bg-surface">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-1 border-b border-surface-3">
        <button
          onClick={() => setCollapsed((c) => !c)}
          className="text-[10px] text-gray-500 hover:text-gray-300 flex items-center gap-1"
        >
          <span className="text-[8px]">{collapsed ? '▶' : '▼'}</span>
          Lockdown Profiles
        </button>
        <button
          onClick={() => { setShowCreate((s) => !s); setCollapsed(false); }}
          className="text-[10px] text-gray-500 hover:text-accent-purple"
        >
          {showCreate ? 'Cancel' : '+ New'}
        </button>
      </div>

      {!collapsed && (
        <div className="px-3 py-1.5 space-y-1">
          {/* Profile pills */}
          {profiles.map((p) => (
            <div key={p.id} className="flex items-center gap-2 group">
              <button
                onClick={() => applyProfile(p.id)}
                disabled={applying === p.id}
                title={profileSummary(p)}
                className={`flex-1 text-left text-[11px] px-2 py-0.5 rounded border transition-colors truncate ${
                  applied === p.id
                    ? 'bg-green-900/40 border-green-700/50 text-green-300'
                    : 'bg-surface-2 border-surface-3 text-gray-300 hover:border-accent-purple hover:text-white'
                } disabled:opacity-50`}
              >
                <span className="font-medium">{p.name}</span>
                {p.auto_on_raid && (
                  <span className="ml-1.5 text-[9px] text-yellow-600" title="Auto-applies on incoming raid">
                    ⚡raid
                  </span>
                )}
                <span className="ml-2 text-[10px] text-gray-500">{profileSummary(p)}</span>
              </button>
              <button
                onClick={() => deleteProfile(p.id)}
                className="opacity-0 group-hover:opacity-100 text-gray-600 hover:text-red-400 text-xs transition-opacity shrink-0"
                title="Delete profile"
              >
                ×
              </button>
            </div>
          ))}

          {/* Inline create form */}
          {showCreate && (
            <div className="mt-1.5 border border-surface-3 rounded p-2 space-y-2 bg-surface-1">
              <input
                autoFocus
                type="text"
                placeholder="Profile name"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') submitCreate(); if (e.key === 'Escape') setShowCreate(false); }}
                className="w-full text-xs bg-surface border border-surface-3 rounded px-2 py-1 text-gray-200 focus:outline-none focus:border-accent-purple"
              />

              {/* Mode toggles */}
              <div className="flex flex-wrap gap-1.5">
                {(['emote_only', 'sub_only', 'unique_chat', 'slow_mode', 'followers_only'] as const).map((mode) => (
                  <button
                    key={mode}
                    onClick={() => cycleModeValue(mode)}
                    title="Click to cycle: skip → enable → disable"
                    className="flex items-center gap-1 text-[10px] px-1.5 py-0.5 bg-surface-2 border border-surface-3 rounded hover:border-gray-500 transition-colors"
                  >
                    <span className="text-gray-400">{MODE_LABELS[mode]}</span>
                    <span className={modeValueColor(newModes[mode])}>
                      {modeValueLabel(newModes[mode])}
                    </span>
                  </button>
                ))}
              </div>

              {/* Slow mode duration */}
              {newModes.slow_mode === 1 && (
                <div className="flex items-center gap-2">
                  <span className="text-[10px] text-gray-500">Slow wait:</span>
                  <input
                    type="number"
                    min={3}
                    max={120}
                    value={newSlowWait}
                    onChange={(e) => setNewSlowWait(Number(e.target.value))}
                    className="w-16 text-xs bg-surface border border-surface-3 rounded px-2 py-0.5 text-gray-300 focus:outline-none"
                  />
                  <span className="text-[10px] text-gray-600">s</span>
                </div>
              )}

              {/* Followers-only duration */}
              {newModes.followers_only === 1 && (
                <div className="flex items-center gap-2">
                  <span className="text-[10px] text-gray-500">Follower min age:</span>
                  <input
                    type="number"
                    min={0}
                    max={43200}
                    value={newFollowerDur}
                    onChange={(e) => setNewFollowerDur(Number(e.target.value))}
                    className="w-16 text-xs bg-surface border border-surface-3 rounded px-2 py-0.5 text-gray-300 focus:outline-none"
                  />
                  <span className="text-[10px] text-gray-600">min</span>
                </div>
              )}

              {/* Auto-raid toggle */}
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={newRaid}
                  onChange={(e) => setNewRaid(e.target.checked)}
                  className="accent-yellow-500"
                />
                <span className="text-[10px] text-gray-400">Auto-apply on incoming raid</span>
              </label>

              <div className="flex gap-2">
                <button
                  onClick={submitCreate}
                  disabled={creating || !newName.trim()}
                  className="text-xs px-3 py-1 bg-accent-purple/20 hover:bg-accent-purple/30 border border-accent-purple/50 text-accent-purple rounded disabled:opacity-40 transition-colors"
                >
                  {creating ? 'Saving…' : 'Save profile'}
                </button>
                <button
                  onClick={() => setShowCreate(false)}
                  className="text-xs px-2 py-1 text-gray-500 hover:text-gray-300"
                >
                  Cancel
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
