/**
 * ChannelEventFeed — displays recent Twitch subscription events (new subs,
 * resubs, gift subs) in a compact scrollable list in the right sidebar.
 */

import { useChatStore, type ChannelEvent } from '../store/chatStore';

function tierLabel(tier?: string): string {
  if (tier === '2000') return 'T2';
  if (tier === '3000') return 'T3';
  return 'T1';
}

function EventRow({ ev }: { ev: ChannelEvent }) {
  const time = new Date(ev.timestamp * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

  if (ev.type === 'subscription_new') {
    const label = ev.isGift ? 'gifted sub' : 'new sub';
    return (
      <div className="flex items-start gap-2 py-1 px-2 hover:bg-surface-2 rounded text-xs">
        <span className="text-[10px] text-gray-500 shrink-0 mt-0.5">{time}</span>
        <span className="text-yellow-400">★</span>
        <span className="min-w-0">
          <span className="text-white font-medium">{ev.username}</span>
          <span className="text-gray-400"> {label} </span>
          <span className="text-gray-500">{tierLabel(ev.tier)}</span>
        </span>
      </div>
    );
  }

  if (ev.type === 'subscription_resub') {
    return (
      <div className="flex items-start gap-2 py-1 px-2 hover:bg-surface-2 rounded text-xs">
        <span className="text-[10px] text-gray-500 shrink-0 mt-0.5">{time}</span>
        <span className="text-blue-400">↺</span>
        <span className="min-w-0">
          <span className="text-white font-medium">{ev.username}</span>
          <span className="text-gray-400"> resubbed </span>
          <span className="text-gray-500">{tierLabel(ev.tier)}</span>
          {ev.cumulativeMonths ? (
            <span className="text-gray-500"> · {ev.cumulativeMonths}mo</span>
          ) : null}
          {ev.message ? (
            <div className="text-gray-400 italic truncate mt-0.5">{ev.message}</div>
          ) : null}
        </span>
      </div>
    );
  }

  if (ev.type === 'subscription_gift') {
    const gifter = ev.anonymous ? 'Anonymous' : ev.username;
    return (
      <div className="flex items-start gap-2 py-1 px-2 hover:bg-surface-2 rounded text-xs">
        <span className="text-[10px] text-gray-500 shrink-0 mt-0.5">{time}</span>
        <span className="text-green-400">♥</span>
        <span className="min-w-0">
          <span className="text-white font-medium">{gifter}</span>
          <span className="text-gray-400"> gifted </span>
          <span className="text-white font-medium">{ev.count ?? 1}</span>
          <span className="text-gray-400"> sub{(ev.count ?? 1) !== 1 ? 's' : ''} </span>
          <span className="text-gray-500">{tierLabel(ev.tier)}</span>
        </span>
      </div>
    );
  }

  return null;
}

export function ChannelEventFeed() {
  const events = useChatStore((s) => s.channelEvents);

  if (events.length === 0) return null;

  return (
    <div className="border-t border-surface-3">
      <div className="text-[10px] text-gray-500 uppercase tracking-wider px-3 pt-2 pb-1">
        Channel Events
      </div>
      <div className="max-h-40 overflow-y-auto">
        {events.map((ev) => (
          <EventRow key={ev.id} ev={ev} />
        ))}
      </div>
    </div>
  );
}
