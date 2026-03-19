/**
 * AutomodQueueWidget — review AutoMod-held messages before they're blocked.
 *
 * Messages arrive via the `automod_hold` WebSocket event.
 * Keyboard shortcuts: A = Allow focused item, D = Deny focused item.
 * Clicking a row focuses it; focused item highlighted in blue.
 */

import { useEffect, useRef, useState } from 'react';
import { useChatStore } from '../store/chatStore';
import type { AutomodHeldMessage } from '../store/chatStore';

const CATEGORY_LABELS: Record<string, string> = {
  aggressive:          'Aggressive',
  bullying:            'Bullying',
  disability:          'Disability',
  identity:            'Identity',
  profanity:           'Profanity',
  racial:              'Racial',
  sexual:              'Sexual',
  sexual_based_terms:  'Sexual terms',
  swearing:            'Swearing',
};

interface Props {
  port: number;
  ipcSecret: string;
}

export function AutomodQueueWidget({ port, ipcSecret }: Props) {
  const queue = useChatStore((s) => s.automodQueue);
  const resolveAutomodHeld = useChatStore((s) => s.resolveAutomodHeld);
  const [focusedId, setFocusedId] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  // Auto-focus first item when queue gets a new message
  useEffect(() => {
    if (queue.length > 0 && !focusedId) {
      setFocusedId(queue[0].messageId);
    }
    if (queue.length === 0) setFocusedId(null);
  }, [queue.length]); // eslint-disable-line react-hooks/exhaustive-deps

  // Keyboard shortcuts: A = allow, D = deny
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      // Ignore if typing in an input
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      if (!focusedId || busy) return;
      if (e.key === 'a' || e.key === 'A') { e.preventDefault(); decide(focusedId, 'ALLOW'); }
      if (e.key === 'd' || e.key === 'D') { e.preventDefault(); decide(focusedId, 'DENY'); }
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [focusedId, busy]); // eslint-disable-line react-hooks/exhaustive-deps

  async function decide(messageId: string, action: 'ALLOW' | 'DENY') {
    setBusy(messageId);
    const endpoint = action === 'ALLOW' ? 'approve' : 'deny';
    try {
      await fetch(`http://127.0.0.1:${port}/api/automod/${endpoint}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-IPC-Secret': ipcSecret },
        body: JSON.stringify({ message_id: messageId }),
      });
    } catch { /* ignore */ }
    resolveAutomodHeld(messageId);
    // Focus next in queue
    const remaining = queue.filter((m) => m.messageId !== messageId);
    setFocusedId(remaining.length > 0 ? remaining[0].messageId : null);
    setBusy(null);
  }

  if (queue.length === 0) return null;

  return (
    <div ref={containerRef} className="flex flex-col border-t border-surface-3">
      <div className="px-3 py-1.5 text-[10px] font-semibold text-yellow-500 uppercase tracking-wider flex items-center justify-between bg-yellow-950/20">
        <span>AutoMod Queue</span>
        <div className="flex items-center gap-2">
          <span className="text-yellow-600 text-[10px]">[A] allow  [D] deny</span>
          <span className="bg-yellow-600 text-black rounded-full px-1.5 text-[10px] font-bold">
            {queue.length}
          </span>
        </div>
      </div>

      <div className="overflow-y-auto max-h-52">
        {queue.map((msg) => (
          <AutomodRow
            key={msg.messageId}
            msg={msg}
            focused={focusedId === msg.messageId}
            busy={busy === msg.messageId}
            onFocus={() => setFocusedId(msg.messageId)}
            onAllow={() => decide(msg.messageId, 'ALLOW')}
            onDeny={() => decide(msg.messageId, 'DENY')}
          />
        ))}
      </div>
    </div>
  );
}

function AutomodRow({
  msg,
  focused,
  busy,
  onFocus,
  onAllow,
  onDeny,
}: {
  msg: AutomodHeldMessage;
  focused: boolean;
  busy: boolean;
  onFocus: () => void;
  onAllow: () => void;
  onDeny: () => void;
}) {
  const levelColor = msg.level >= 4 ? 'text-red-400' : msg.level >= 3 ? 'text-orange-400' : 'text-yellow-400';
  const categoryLabel = CATEGORY_LABELS[msg.category] ?? msg.category;

  return (
    <div
      onClick={onFocus}
      className={`px-3 py-2 border-b border-surface-3 last:border-0 cursor-pointer transition-colors ${
        focused ? 'bg-blue-950/30 border-l-2 border-l-blue-500' : 'hover:bg-surface-2 border-l-2 border-l-transparent'
      }`}
    >
      <div className="flex items-center justify-between mb-1">
        <span className="text-xs text-gray-300 font-mono">{msg.username}</span>
        <div className="flex items-center gap-1.5">
          {categoryLabel && (
            <span className={`text-[10px] ${levelColor}`}>{categoryLabel} L{msg.level}</span>
          )}
        </div>
      </div>
      <div className="text-xs text-gray-400 break-words leading-snug mb-1.5">
        {msg.content}
      </div>
      {focused && !busy && (
        <div className="flex items-center gap-2">
          <button
            onClick={(e) => { e.stopPropagation(); onAllow(); }}
            className="text-[10px] px-2 py-0.5 bg-green-900/40 hover:bg-green-800/60 border border-green-700/50 text-green-400 rounded"
          >
            Allow (A)
          </button>
          <button
            onClick={(e) => { e.stopPropagation(); onDeny(); }}
            className="text-[10px] px-2 py-0.5 bg-red-900/40 hover:bg-red-800/60 border border-red-700/50 text-red-400 rounded"
          >
            Deny (D)
          </button>
        </div>
      )}
      {busy && <div className="text-[10px] text-gray-500">Processing…</div>}
    </div>
  );
}
