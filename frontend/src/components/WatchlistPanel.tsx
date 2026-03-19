/**
 * WatchlistPanel — displays users flagged for heightened monitoring.
 *
 * Loaded from GET /api/watchlist on mount. Clicking a username opens
 * the UserDetailPanel. Users can be removed inline.
 */

import { useEffect } from 'react';
import { useChatStore } from '../store/chatStore';
import type { WatchedUser } from '../store/chatStore';

interface Props {
  port: number;
  ipcSecret: string;
}

function WatchedRow({
  user,
  port,
  ipcSecret,
}: {
  user: WatchedUser;
  port: number;
  ipcSecret: string;
}) {
  const setSelectedUser = useChatStore((s) => s.setSelectedUser);
  const removeWatchedUser = useChatStore((s) => s.removeWatchedUser);

  async function unwatch() {
    try {
      await fetch(`http://127.0.0.1:${port}/api/watchlist/${encodeURIComponent(user.user_id)}`, {
        method: 'DELETE',
        headers: { 'X-IPC-Secret': ipcSecret },
      });
      removeWatchedUser(user.user_id);
    } catch {
      // ignore
    }
  }

  return (
    <div className="flex items-center gap-2 px-3 py-1.5 border-b border-surface-3 last:border-0 hover:bg-surface-2 group">
      {user.priority === 'high' && (
        <span className="w-1.5 h-1.5 rounded-full bg-orange-500 shrink-0" title="High priority" />
      )}
      <button
        onClick={() => setSelectedUser({ userId: user.user_id, username: user.username })}
        className="text-xs text-gray-200 font-mono hover:text-accent-purple truncate flex-1 text-left"
      >
        {user.username}
      </button>
      {user.note && (
        <span className="text-[10px] text-gray-600 truncate max-w-[80px]" title={user.note}>
          {user.note}
        </span>
      )}
      <button
        onClick={unwatch}
        className="text-[10px] text-gray-600 hover:text-red-400 opacity-0 group-hover:opacity-100 transition-opacity shrink-0"
        title="Remove from watchlist"
      >
        ✕
      </button>
    </div>
  );
}

export function WatchlistPanel({ port, ipcSecret }: Props) {
  const watchedUsers = useChatStore((s) => s.watchedUsers);
  const setWatchedUsers = useChatStore((s) => s.setWatchedUsers);

  useEffect(() => {
    fetch(`http://127.0.0.1:${port}/api/watchlist`, {
      headers: { 'X-IPC-Secret': ipcSecret },
    })
      .then((r) => r.json())
      .then((data) => setWatchedUsers(data.watched ?? []))
      .catch(() => {});
  }, [port, ipcSecret]); // eslint-disable-line react-hooks/exhaustive-deps

  if (watchedUsers.length === 0) return null;

  return (
    <div className="flex flex-col border-t border-surface-3">
      <div className="px-3 py-1.5 text-[10px] font-semibold text-gray-500 uppercase tracking-wider flex items-center justify-between">
        <span>Watchlist</span>
        <span className="bg-orange-900/50 text-orange-400 rounded-full px-1.5 text-[10px]">
          {watchedUsers.length}
        </span>
      </div>
      <div className="overflow-y-auto max-h-40">
        {watchedUsers.map((u) => (
          <WatchedRow key={u.user_id} user={u} port={port} ipcSecret={ipcSecret} />
        ))}
      </div>
    </div>
  );
}
