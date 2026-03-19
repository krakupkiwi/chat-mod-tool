/**
 * ChatModeBar — quick-toggle buttons for Twitch channel chat modes.
 *
 * Each mode is independently togglable — multiple can be active at the same time.
 * State is fetched from GET /api/moderation/chat-settings:
 *   - On mount
 *   - When Twitch connects (or reconnects)
 *   - Every 15 seconds while connected (live-sync if another mod changes a setting)
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useChatStore } from '../store/chatStore';

type Mode = 'emote_only' | 'sub_only' | 'unique_chat' | 'slow_mode' | 'followers_only';

interface ModeConfig {
  mode: Mode;
  label: string;
  title: string;
}

const MODES: ModeConfig[] = [
  { mode: 'emote_only',     label: 'Emote',      title: 'Emote-only mode' },
  { mode: 'sub_only',       label: 'Sub',         title: 'Subscribers-only mode' },
  { mode: 'unique_chat',    label: 'Unique',      title: 'Unique chat (no repeated messages)' },
  { mode: 'slow_mode',      label: 'Slow',        title: 'Slow mode (30s default)' },
  { mode: 'followers_only', label: 'Followers',   title: 'Followers-only mode' },
];

interface ChatModeBarProps {
  port: number;
  ipcSecret: string;
}

export function ChatModeBar({ port, ipcSecret }: ChatModeBarProps) {
  const [activeModes, setActiveModes] = useState<Set<Mode>>(new Set());
  const [busy, setBusy] = useState<Mode | null>(null);
  const twitchConnected = useChatStore((s) => s.twitchConnected);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchSettings = useCallback(async () => {
    try {
      const res = await fetch(`http://127.0.0.1:${port}/api/moderation/chat-settings`, {
        headers: { 'X-IPC-Secret': ipcSecret },
      });
      if (!res.ok) return;
      const data = await res.json();
      const active = new Set<Mode>();
      if (data.emote_mode)       active.add('emote_only');
      if (data.subscriber_mode)  active.add('sub_only');
      if (data.unique_chat_mode) active.add('unique_chat');
      if (data.slow_mode)        active.add('slow_mode');
      if (data.follower_mode)    active.add('followers_only');
      setActiveModes(active);
    } catch {
      // non-critical — UI degrades gracefully
    }
  }, [port, ipcSecret]);

  // Fetch whenever Twitch connects (covers initial connect and reconnects)
  useEffect(() => {
    if (!twitchConnected) return;
    fetchSettings();
  }, [twitchConnected, fetchSettings]);

  // Poll every 15 seconds while connected to catch external changes
  // (e.g. another mod or bot toggling a mode via Twitch chat commands)
  useEffect(() => {
    if (!twitchConnected) {
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = null;
      return;
    }
    pollRef.current = setInterval(fetchSettings, 15_000);
    return () => {
      if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    };
  }, [twitchConnected, fetchSettings]);

  async function toggle(mode: Mode) {
    if (busy) return;
    const enabling = !activeModes.has(mode);
    setBusy(mode);
    // Optimistic update
    setActiveModes((prev) => {
      const next = new Set(prev);
      if (enabling) next.add(mode); else next.delete(mode);
      return next;
    });
    try {
      const body: Record<string, unknown> = { mode, enabled: enabling };
      if (enabling && mode === 'slow_mode') body.duration = 30;
      if (enabling && mode === 'followers_only') body.duration = 0;
      const res = await fetch(`http://127.0.0.1:${port}/api/moderation/chat-mode`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-IPC-Secret': ipcSecret },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        // Revert optimistic update on failure
        setActiveModes((prev) => {
          const next = new Set(prev);
          if (enabling) next.delete(mode); else next.add(mode);
          return next;
        });
      }
    } catch {
      // Revert on network error too
      setActiveModes((prev) => {
        const next = new Set(prev);
        if (enabling) next.delete(mode); else next.add(mode);
        return next;
      });
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="flex items-center gap-1 px-3 py-1 border-b border-surface-3 bg-surface-1 shrink-0">
      <span className="text-[10px] text-gray-600 uppercase tracking-wider mr-1 shrink-0">Chat</span>
      {MODES.map(({ mode, label, title }) => {
        const isActive = activeModes.has(mode);
        const isBusy = busy === mode;
        return (
          <button
            key={mode}
            title={title}
            disabled={!!busy}
            onClick={() => toggle(mode)}
            className={`
              text-[10px] px-2 py-0.5 rounded border transition-colors shrink-0
              disabled:opacity-50
              ${isActive
                ? 'bg-accent-purple/20 border-accent-purple/60 text-accent-purple'
                : 'bg-surface-2 border-surface-3 text-gray-500 hover:border-gray-500 hover:text-gray-300'}
            `}
          >
            {isBusy ? '…' : label}
          </button>
        );
      })}
    </div>
  );
}
