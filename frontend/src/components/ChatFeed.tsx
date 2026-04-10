/**
 * ChatFeed — Twitch-style inline chat with natural message wrapping.
 *
 * Layout mirrors Twitch's popout chat:
 *   [badge icons]  Username:  message text that wraps naturally
 *
 * Threat highlighting (IDS overlay):
 *   40–59  subtle yellow-tinted left bar
 *   60–74  orange bar
 *   75+    red bar + faint background
 *
 * Scroll model (mirrors Twitch):
 *   Live    — auto-pins to bottom; useLayoutEffect scrolls before paint (no drift)
 *   Paused  — frozen snapshot; identical DOM slice as live (no jump on pause transition)
 *   Resume  — user scrolls to bottom or clicks "Resume ↓"
 *
 * Key fixes vs prior version:
 *   - useLayoutEffect instead of useEffect: scroll correction is synchronous,
 *     eliminating the one-frame drift visible at high message rates.
 *   - Freeze = same slice as live: switching live→paused renders identical nodes
 *     (React no-ops the reconcile), so scrollTop never jumps.
 *   - programmaticRef guards every own scrollTop write from re-triggering pause.
 *   - Pause logic lives exclusively in onScroll (removed the useEffect duplicate
 *     that could race with onScroll and cause conflicting state updates).
 *   - ChatRow is memoised: only the one new row mounts per message; 249 others skip.
 */

import { memo, useCallback, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useChatStore, type ChatMessage, type ChatFragment, type SelectedUser } from '../store/chatStore';

// ---------------------------------------------------------------------------
// Badge icons (inline SVG, no asset files)
// ---------------------------------------------------------------------------

function BadgeIcon({ type }: { type: 'mod' | 'vip' | 'sub' | 'broadcaster' }) {
  if (type === 'broadcaster') {
    return (
      <svg width="18" height="18" viewBox="0 0 18 18" className="inline-block align-middle mr-0.5 shrink-0" aria-label="Broadcaster">
        <rect width="18" height="18" rx="2" fill="#e91916" />
        <path d="M5 5h8v1.5H9.8v6H8.2V6.5H5V5z" fill="white" />
      </svg>
    );
  }
  if (type === 'mod') {
    return (
      <svg width="18" height="18" viewBox="0 0 18 18" className="inline-block align-middle mr-0.5 shrink-0" aria-label="Moderator">
        <rect width="18" height="18" rx="2" fill="#00ad03" />
        <path d="M9 3L11 7.5H15.5L11.75 10.25L13.25 15L9 12L4.75 15L6.25 10.25L2.5 7.5H7L9 3Z" fill="white" />
      </svg>
    );
  }
  if (type === 'vip') {
    return (
      <svg width="18" height="18" viewBox="0 0 18 18" className="inline-block align-middle mr-0.5 shrink-0" aria-label="VIP">
        <rect width="18" height="18" rx="2" fill="#e005b9" />
        <path d="M9 4l2 4h4l-3.25 2.5 1.25 4.5L9 12.5 5 15l1.25-4.5L3 8h4z" fill="white" />
      </svg>
    );
  }
  // sub
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" className="inline-block align-middle mr-0.5 shrink-0" aria-label="Subscriber">
      <rect width="18" height="18" rx="2" fill="#9147ff" />
      <path d="M9 3.5L10.5 7H14L11 9.25l1.25 4L9 11.25 5.75 13.25 7 9.25 4 7h3.5z" fill="white" />
    </svg>
  );
}

function parseBadges(flags: string[]): ('broadcaster' | 'mod' | 'vip' | 'sub')[] {
  const badges: ('broadcaster' | 'mod' | 'vip' | 'sub')[] = [];
  for (const f of flags) {
    const key = f.split('/')[0].toLowerCase();
    if (key === 'broadcaster') badges.push('broadcaster');
    else if (key === 'moderator') badges.push('mod');
    else if (key === 'vip') badges.push('vip');
    else if (key === 'subscriber' || key === 'sub') badges.push('sub');
  }
  return badges;
}

// ---------------------------------------------------------------------------
// Threat styling
// ---------------------------------------------------------------------------

function threatStyle(score: number): { bar: string; bg: string; scoreColor: string } {
  if (score >= 75) return {
    bar: 'border-l-[3px] border-l-red-500',
    bg: 'bg-red-950/25',
    scoreColor: 'text-red-400',
  };
  if (score >= 60) return {
    bar: 'border-l-[3px] border-l-orange-500',
    bg: 'bg-orange-950/20',
    scoreColor: 'text-orange-400',
  };
  if (score >= 40) return {
    bar: 'border-l-[3px] border-l-yellow-500',
    bg: '',
    scoreColor: 'text-yellow-400',
  };
  return { bar: 'border-l-[3px] border-l-transparent', bg: '', scoreColor: '' };
}

// ---------------------------------------------------------------------------
// Message fragment renderer
// ---------------------------------------------------------------------------

function Fragment({ frag }: { frag: ChatFragment }) {
  if (frag.type === 'emote' && frag.emote_id) {
    return (
      <img
        src={`https://static-cdn.jtvnw.net/emoticons/v2/${frag.emote_id}/default/dark/1.0`}
        alt={frag.text}
        title={frag.text}
        className="inline-block align-middle mx-0.5"
        style={{ width: 28, height: 28 }}
      />
    );
  }
  return <>{frag.text}</>;
}

// ---------------------------------------------------------------------------
// Single message row — memoised so only the new row mounts each tick
// ---------------------------------------------------------------------------

interface RowProps {
  msg: ChatMessage;
  onSelectUser: (u: SelectedUser) => void;
}

const ChatRow = memo(function ChatRow({ msg, onSelectUser }: RowProps) {
  const badges = parseBadges(msg.flags);
  const { bar, bg, scoreColor } = threatStyle(msg.threatScore);
  const color = msg.color ?? '#a970ff';

  return (
    <div className={`group px-3 py-[3px] hover:bg-white/[0.04] transition-colors ${bar} ${bg}`}>
      <span className="leading-[1.4] text-[13px] break-words">
        {badges.map((b, i) => <BadgeIcon key={i} type={b} />)}

        <button
          className="font-bold hover:underline decoration-dotted focus:outline-none"
          style={{ color }}
          onClick={() => onSelectUser({ userId: msg.userId, username: msg.username, color: msg.color })}
        >
          {msg.username}
        </button>

        <span className="text-gray-300 select-none">: </span>

        <span className="text-gray-100">
          {msg.fragments.length > 0
            ? msg.fragments.map((f, i) => <Fragment key={i} frag={f} />)
            : msg.content}
        </span>

        {msg.threatScore >= 40 && (
          <span className={`ml-1.5 text-[11px] font-mono font-semibold ${scoreColor} opacity-80`}>
            [{Math.round(msg.threatScore)}]
          </span>
        )}
      </span>
    </div>
  );
});

// ---------------------------------------------------------------------------
// Feed
// ---------------------------------------------------------------------------

/** Max messages rendered in the DOM at any time. */
const LIVE_CAP = 250;

/**
 * How close to the bottom (px) before we consider the user "at the bottom".
 * Matches Twitch's own threshold (roughly one row of slack).
 */
const AT_BOTTOM_THRESHOLD = 80;

export function ChatFeed() {
  const allMessages = useChatStore((s) => s.messages);
  const activeChannel = useChatStore((s) => s.activeChannel);
  const setSelectedUser = useChatStore((s) => s.setSelectedUser);

  const scrollRef = useRef<HTMLDivElement>(null);

  // ── Two-state model: live (auto-scroll) or paused (frozen snapshot) ──────
  const [paused, setPaused] = useState(false);
  // Ref mirror so event handlers always read current value without stale closure.
  const pausedRef = useRef(false);

  // Snapshot of messages taken at the moment the user scrolled up.
  // Crucially, this is set to messages.slice(-LIVE_CAP) — the same slice that
  // was already rendered — so the DOM transition is a no-op (no jump).
  const [frozenMessages, setFrozenMessages] = useState<ChatMessage[]>([]);

  // messages.length at the time of pause, used to compute newCount.
  const frozenLengthRef = useRef(0);

  // Set to true immediately before any programmatic scrollTop write so the
  // subsequent scroll event is suppressed and doesn't trigger a false pause.
  const programmaticRef = useRef(false);

  // Always-current ref to the filtered messages — safe for use inside
  // useCallback (avoids stale closure without re-creating the callback).
  const messagesRef = useRef<ChatMessage[]>([]);

  // Channel filter — useMemo keeps the reference stable when messages haven't changed.
  const messages = useMemo(
    () => activeChannel
      ? allMessages.filter((m) => m.channel === activeChannel)
      : allMessages,
    [allMessages, activeChannel],
  );
  messagesRef.current = messages;

  // What to render: live trailing slice or the frozen snapshot.
  const visible = paused ? frozenMessages : messages.slice(-LIVE_CAP);
  const newCount = paused ? Math.max(0, messages.length - frozenLengthRef.current) : 0;

  // ── Auto-scroll (synchronous, before paint) ───────────────────────────────
  // useLayoutEffect runs after every commit, synchronously, before the browser
  // paints.  This is the only place we write scrollTop when in live mode.
  // Running after every render is cheap: two DOM reads + one conditional write.
  useLayoutEffect(() => {
    if (pausedRef.current) return;
    const el = scrollRef.current;
    if (!el) return;
    const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    if (distFromBottom > 1) {
      // Flag this scroll as ours so onScroll ignores it.
      programmaticRef.current = true;
      el.scrollTop = el.scrollHeight;
    }
  });

  // ── Scroll handler ────────────────────────────────────────────────────────
  const onScroll = useCallback(() => {
    // Swallow the event fired by our own scrollTop write.
    if (programmaticRef.current) {
      programmaticRef.current = false;
      return;
    }
    const el = scrollRef.current;
    if (!el) return;
    const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    const atBottom = distFromBottom < AT_BOTTOM_THRESHOLD;

    if (!atBottom && !pausedRef.current) {
      // User scrolled up → pause.
      // Freeze to the SAME slice currently rendered so the DOM doesn't change
      // and scrollTop stays exactly where it is.  Zero visible jump.
      pausedRef.current = true;
      const snap = messagesRef.current.slice(-LIVE_CAP);
      frozenLengthRef.current = messagesRef.current.length;
      setFrozenMessages(snap);
      setPaused(true);
    } else if (atBottom && pausedRef.current) {
      // User scrolled back to the bottom → resume.
      pausedRef.current = false;
      setPaused(false);
      // useLayoutEffect will pin us to the bottom on the next render.
    }
  }, []);

  // ── Resume button ─────────────────────────────────────────────────────────
  const resume = useCallback(() => {
    pausedRef.current = false;
    setPaused(false);
    // useLayoutEffect fires after the re-render (live slice → bottom).
  }, []);

  // ── Empty state ───────────────────────────────────────────────────────────
  if (messages.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-gray-500 gap-2">
        <span className="text-2xl">💬</span>
        <span className="text-sm">Waiting for chat messages…</span>
      </div>
    );
  }

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div className="relative h-full flex flex-col">
      {/*
        Scroll container.
        overflow-anchor: none — we manage scroll position ourselves via
        useLayoutEffect; browser anchoring would fight our writes.
      */}
      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto overscroll-contain"
        style={{ overflowAnchor: 'none' }}
        onScroll={onScroll}
      >
        {/* flex-col justify-end pushes messages to the bottom when the list
            is shorter than the viewport, matching Twitch's layout. */}
        <div className="min-h-full flex flex-col justify-end">
          {visible.map((msg) => (
            <ChatRow key={msg.id} msg={msg} onSelectUser={setSelectedUser} />
          ))}
        </div>
      </div>

      {/* Pause banner — matches Twitch's "Chat paused due to scrolling" UX */}
      {paused && (
        <div className="shrink-0 flex items-center justify-between px-3 py-1.5 bg-surface-2 border-t border-surface-3 text-xs text-gray-400">
          <span>
            Chat paused due to scrolling
            {newCount > 0 && (
              <span className="ml-1 text-accent-purple font-semibold">
                — {newCount} new {newCount === 1 ? 'message' : 'messages'}
              </span>
            )}
          </span>
          <button
            onClick={resume}
            className="text-accent-purple hover:text-purple-300 font-semibold transition-colors"
          >
            Resume ↓
          </button>
        </div>
      )}
    </div>
  );
}
