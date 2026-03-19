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
 * Natural-flow scrollable div, capped at RENDER_CAP messages for DOM performance.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
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
// Single message row
// ---------------------------------------------------------------------------

interface RowProps {
  msg: ChatMessage;
  onSelectUser: (u: SelectedUser) => void;
}

function ChatRow({ msg, onSelectUser }: RowProps) {
  const badges = parseBadges(msg.flags);
  const { bar, bg, scoreColor } = threatStyle(msg.threatScore);
  const color = msg.color ?? '#a970ff';

  return (
    <div className={`group px-3 py-[3px] hover:bg-white/[0.04] transition-colors ${bar} ${bg}`}>
      <span className="leading-[1.4] text-[13px] break-words">
        {/* Badges */}
        {badges.map((b, i) => <BadgeIcon key={i} type={b} />)}

        {/* Username */}
        <button
          className="font-bold hover:underline decoration-dotted focus:outline-none"
          style={{ color }}
          onClick={() => onSelectUser({ userId: msg.userId, username: msg.username, color: msg.color })}
        >
          {msg.username}
        </button>

        {/* Colon separator */}
        <span className="text-gray-300 select-none">: </span>

        {/* Message — render fragments if available, fall back to plain content */}
        <span className="text-gray-100">
          {msg.fragments.length > 0
            ? msg.fragments.map((f, i) => <Fragment key={i} frag={f} />)
            : msg.content}
        </span>

        {/* Threat score badge */}
        {msg.threatScore >= 40 && (
          <span className={`ml-1.5 text-[11px] font-mono font-semibold ${scoreColor} opacity-80`}>
            [{Math.round(msg.threatScore)}]
          </span>
        )}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Feed
// ---------------------------------------------------------------------------

// Only render the most recent N messages to keep DOM size bounded.
const RENDER_CAP = 150;

export function ChatFeed() {
  const allMessages = useChatStore((s) => s.messages);
  const activeChannel = useChatStore((s) => s.activeChannel);
  const setSelectedUser = useChatStore((s) => s.setSelectedUser);

  // Filter messages by active channel when a channel tab is selected
  const messages = activeChannel
    ? allMessages.filter((m) => m.channel === activeChannel)
    : allMessages;

  const scrollRef = useRef<HTMLDivElement>(null);

  // Paused state — mirrors Twitch: user scrolled up, chat freezes
  const pausedRef = useRef(false);
  const [paused, setPaused] = useState(false);
  const pausedAtLengthRef = useRef(0); // messages.length at the moment of pause
  const messagesLengthRef = useRef(messages.length);
  messagesLengthRef.current = messages.length;

  // Guard so scroll events fired by our own scrollTop writes don't trigger pause
  const programmaticRef = useRef(false);

  // How many new messages arrived while paused
  const newCount = paused ? Math.max(0, messages.length - pausedAtLengthRef.current) : 0;

  // What's rendered — live slice when playing, frozen slice when paused
  const visible = paused
    ? messages.slice(Math.max(0, pausedAtLengthRef.current - RENDER_CAP), pausedAtLengthRef.current)
    : messages.slice(-RENDER_CAP);

  // Scroll to bottom whenever a new message arrives and we are not paused
  useEffect(() => {
    if (pausedRef.current) return;
    const el = scrollRef.current;
    if (!el) return;
    // Only flag if we'll actually move — if already at bottom no scroll event fires
    // and the flag would stay set, blocking the user's next scroll.
    if (el.scrollHeight - el.scrollTop - el.clientHeight > 1) {
      programmaticRef.current = true;
    }
    el.scrollTop = el.scrollHeight;
  }, [messages.length]);

  const onScroll = useCallback(() => {
    // Consume the flag on the scroll event it was set for, not via rAF.
    // rAF fires between message arrivals under heavy load, leaving the flag true
    // while the user's scroll event fires — causing their scroll to be silently ignored.
    if (programmaticRef.current) {
      programmaticRef.current = false;
      return;
    }
    const el = scrollRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;

    if (!atBottom && !pausedRef.current) {
      // User scrolled up — pause
      pausedRef.current = true;
      pausedAtLengthRef.current = messagesLengthRef.current;
      setPaused(true);
    } else if (atBottom && pausedRef.current) {
      // User scrolled back to bottom — resume
      pausedRef.current = false;
      setPaused(false);
    }
  }, []);

  function resume() {
    pausedRef.current = false;
    setPaused(false);
    // Scroll to bottom after React re-renders the live slice
    requestAnimationFrame(() => {
      const el = scrollRef.current;
      if (!el) return;
      if (el.scrollHeight - el.scrollTop - el.clientHeight > 1) {
        programmaticRef.current = true;
      }
      el.scrollTop = el.scrollHeight;
    });
  }

  if (messages.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-gray-500 gap-2">
        <span className="text-2xl">💬</span>
        <span className="text-sm">Waiting for chat messages…</span>
      </div>
    );
  }

  return (
    <div className="relative h-full flex flex-col">
      {/* Scroll container — overflow-anchor disabled; we manage scroll ourselves */}
      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto overscroll-contain"
        style={{ overflowAnchor: 'none' }}
        onScroll={onScroll}
      >
        {/* Spacer pushes messages to the bottom when list is shorter than viewport */}
        <div className="min-h-full flex flex-col justify-end">
          {visible.map((msg) => (
            <ChatRow key={msg.id} msg={msg} onSelectUser={setSelectedUser} />
          ))}
        </div>
      </div>

      {/* Twitch-style pause banner — sits below chat, always visible when paused */}
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
