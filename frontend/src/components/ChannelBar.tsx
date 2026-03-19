/**
 * ChannelBar — multi-channel tab strip with per-channel health indicators.
 *
 * Shows one tab per monitored channel (default + secondary).
 * Clicking a tab sets the active channel filter in the store, filtering
 * the chat feed and message counter.
 *
 * Each tab has:
 *   - Channel name
 *   - Colored dot: green (quiet), yellow (active), red (recent alerts)
 *   - msg/min badge
 *
 * "All" tab shows combined feed (no filter).
 * "+" button adds a secondary channel inline.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useChatStore } from '../store/chatStore';

interface ChannelStats {
  channel: string;
  messages_per_min: number;
  active_users: number;
  recent_alerts: number;
}

interface ChannelEntry {
  name: string;
  is_default: boolean;
  note: string;
}

interface Props {
  port: number;
  ipcSecret: string;
}

function activityColor(stats: ChannelStats | undefined): string {
  if (!stats) return 'bg-gray-600';
  if (stats.recent_alerts > 0) return 'bg-red-500';
  if (stats.messages_per_min > 5) return 'bg-green-500';
  if (stats.messages_per_min > 0) return 'bg-yellow-500';
  return 'bg-gray-600';
}

export function ChannelBar({ port, ipcSecret }: Props) {
  const activeChannel = useChatStore((s) => s.activeChannel);
  const setActiveChannel = useChatStore((s) => s.setActiveChannel);

  const [channels, setChannels] = useState<ChannelEntry[]>([]);
  const [stats, setStats] = useState<Record<string, ChannelStats>>({});
  const [showAdd, setShowAdd] = useState(false);
  const [addInput, setAddInput] = useState('');
  const [adding, setAdding] = useState(false);
  const [addError, setAddError] = useState('');
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchChannels = useCallback(async () => {
    try {
      const res = await fetch(`http://127.0.0.1:${port}/api/channels`, {
        headers: { 'X-IPC-Secret': ipcSecret },
      });
      if (res.ok) {
        const data = await res.json();
        setChannels(data.channels ?? []);
      }
    } catch { /* ignore */ }
  }, [port, ipcSecret]);

  const fetchStats = useCallback(async (channelList: ChannelEntry[]) => {
    const results: Record<string, ChannelStats> = {};
    await Promise.all(
      channelList.map(async (ch) => {
        try {
          const res = await fetch(
            `http://127.0.0.1:${port}/api/channels/${encodeURIComponent(ch.name)}/stats`,
            { headers: { 'X-IPC-Secret': ipcSecret } },
          );
          if (res.ok) {
            results[ch.name] = await res.json();
          }
        } catch { /* ignore */ }
      }),
    );
    setStats(results);
  }, [port, ipcSecret]);

  useEffect(() => {
    fetchChannels();
  }, [fetchChannels]);

  // Poll stats every 10 seconds
  useEffect(() => {
    if (channels.length === 0) return;
    fetchStats(channels);
    pollRef.current = setInterval(() => fetchStats(channels), 10_000);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [channels, fetchStats]);

  async function addChannel() {
    const name = addInput.trim().replace(/^#/, '');
    if (!name) return;
    setAdding(true);
    setAddError('');
    try {
      const res = await fetch(`http://127.0.0.1:${port}/api/channels`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-IPC-Secret': ipcSecret },
        body: JSON.stringify({ channel: name }),
      });
      if (res.ok) {
        setAddInput('');
        setShowAdd(false);
        await fetchChannels();
      } else {
        const data = await res.json().catch(() => ({}));
        setAddError(data.detail ?? `Error ${res.status}`);
      }
    } catch {
      setAddError('Network error');
    }
    setAdding(false);
  }

  async function removeChannel(name: string) {
    try {
      await fetch(`http://127.0.0.1:${port}/api/channels/${encodeURIComponent(name)}`, {
        method: 'DELETE',
        headers: { 'X-IPC-Secret': ipcSecret },
      });
      setChannels((prev) => prev.filter((c) => c.name !== name));
      // If the deleted channel was active, reset to All
      if (activeChannel === name) setActiveChannel(null);
    } catch { /* ignore */ }
  }

  // Only render the bar when there's more than one channel to switch between
  // (or when "Add" is open)
  const showBar = channels.length > 1 || showAdd;
  if (!showBar) {
    return (
      <div className="flex items-center px-3 py-0.5 border-b border-surface-3 bg-surface-1 shrink-0">
        <button
          onClick={() => setShowAdd(true)}
          className="text-[10px] text-gray-600 hover:text-accent-purple transition-colors"
          title="Monitor additional channels"
        >
          + Monitor another channel
        </button>
      </div>
    );
  }

  return (
    <div className="flex items-center gap-0.5 px-2 py-1 border-b border-surface-3 bg-surface-1 shrink-0 overflow-x-auto">
      {/* "All" tab */}
      <button
        onClick={() => setActiveChannel(null)}
        className={`flex items-center gap-1 px-2 py-0.5 rounded text-[11px] transition-colors shrink-0 ${
          activeChannel === null
            ? 'bg-surface-3 text-gray-200'
            : 'text-gray-500 hover:text-gray-300 hover:bg-surface-2'
        }`}
      >
        All
      </button>

      {/* Channel tabs */}
      {channels.map((ch) => {
        const s = stats[ch.name];
        const isActive = activeChannel === ch.name;
        return (
          <div key={ch.name} className="flex items-center group shrink-0">
            <button
              onClick={() => setActiveChannel(ch.name)}
              className={`flex items-center gap-1.5 px-2 py-0.5 rounded text-[11px] transition-colors ${
                isActive
                  ? 'bg-surface-3 text-gray-200'
                  : 'text-gray-500 hover:text-gray-300 hover:bg-surface-2'
              }`}
            >
              {/* Activity dot */}
              <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${activityColor(s)}`} />
              <span className="font-mono">#{ch.name}</span>
              {s && s.messages_per_min > 0 && (
                <span className="text-gray-600">{s.messages_per_min}/m</span>
              )}
              {s && s.recent_alerts > 0 && (
                <span className="text-red-400 font-bold">{s.recent_alerts}⚠</span>
              )}
            </button>
            {/* Remove button — only for secondary channels, visible on hover */}
            {!ch.is_default && (
              <button
                onClick={() => removeChannel(ch.name)}
                title={`Stop monitoring #${ch.name}`}
                className="opacity-0 group-hover:opacity-100 text-[9px] text-gray-600 hover:text-red-400 ml-0.5 transition-opacity"
              >
                ✕
              </button>
            )}
          </div>
        );
      })}

      {/* Add channel */}
      {showAdd ? (
        <div className="flex items-center gap-1 ml-1 shrink-0">
          <span className="text-[10px] text-gray-500">#</span>
          <input
            autoFocus
            type="text"
            value={addInput}
            onChange={(e) => { setAddInput(e.target.value); setAddError(''); }}
            onKeyDown={(e) => { if (e.key === 'Enter') addChannel(); if (e.key === 'Escape') setShowAdd(false); }}
            placeholder="channelname"
            className="text-[11px] bg-surface border border-surface-3 rounded px-1.5 py-0.5 text-gray-200 w-28 focus:outline-none focus:border-accent-purple font-mono"
          />
          <button
            onClick={addChannel}
            disabled={adding || !addInput.trim()}
            className="text-[10px] text-accent-purple hover:text-white disabled:opacity-40"
          >
            {adding ? '…' : 'Add'}
          </button>
          <button onClick={() => setShowAdd(false)} className="text-[10px] text-gray-600 hover:text-gray-400">✕</button>
          {addError && <span className="text-[10px] text-red-400">{addError}</span>}
        </div>
      ) : (
        <button
          onClick={() => setShowAdd(true)}
          title="Monitor another channel"
          className="ml-1 text-[11px] text-gray-600 hover:text-accent-purple transition-colors shrink-0"
        >
          +
        </button>
      )}
    </div>
  );
}
