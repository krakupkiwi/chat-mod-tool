/**
 * ChatInput — lets mods type and send messages to the monitored Twitch channel.
 * Sits below the chat feed. Sends via POST /api/chat/send.
 */

import { useRef, useState } from 'react';

const MAX_LEN = 500;

interface Props {
  port: number;
  ipcSecret: string;
  disabled?: boolean;
}

export function ChatInput({ port, ipcSecret, disabled }: Props) {
  const [value, setValue] = useState('');
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const remaining = MAX_LEN - value.length;
  const canSend = value.trim().length > 0 && !sending && !disabled;

  async function send() {
    if (!canSend) return;
    const msg = value.trim();
    setSending(true);
    setError(null);
    try {
      const res = await fetch(`http://127.0.0.1:${port}/api/chat/send`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-IPC-Secret': ipcSecret },
        body: JSON.stringify({ message: msg }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setError(data.detail ?? `Error ${res.status}`);
        return;
      }
      setValue('');
    } catch {
      setError('Network error — check backend connection');
    } finally {
      setSending(false);
      inputRef.current?.focus();
    }
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  return (
    <div className="shrink-0 border-t border-surface-3 bg-surface-1 px-3 py-2">
      {error && (
        <div className="text-xs text-red-400 mb-1.5">{error}</div>
      )}
      <div className="flex items-center gap-2">
        <input
          ref={inputRef}
          type="text"
          value={value}
          onChange={(e) => { setValue(e.target.value.slice(0, MAX_LEN)); setError(null); }}
          onKeyDown={onKeyDown}
          placeholder={disabled ? 'Not connected to Twitch…' : 'Send a message…'}
          disabled={disabled || sending}
          className="flex-1 bg-surface-2 border border-surface-3 rounded px-3 py-1.5 text-sm text-gray-100 placeholder-gray-600 focus:outline-none focus:border-accent-purple disabled:opacity-40"
        />
        <button
          onClick={send}
          disabled={!canSend}
          className="shrink-0 px-3 py-1.5 rounded bg-accent-purple text-white text-sm font-semibold disabled:opacity-40 hover:bg-purple-500 transition-colors"
        >
          {sending ? '…' : 'Chat'}
        </button>
      </div>
      {value.length > MAX_LEN * 0.8 && (
        <div className={`text-[11px] mt-1 text-right ${remaining <= 0 ? 'text-red-400' : 'text-gray-500'}`}>
          {remaining} left
        </div>
      )}
    </div>
  );
}
