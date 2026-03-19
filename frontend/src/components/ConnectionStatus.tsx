import { useChatStore } from '../store/chatStore';
import clsx from 'clsx';

const WS_LABELS: Record<string, string> = {
  connected: 'Connected',
  connecting: 'Connecting...',
  disconnected: 'Disconnected',
  error: 'Error',
};

const WS_COLORS: Record<string, string> = {
  connected: 'bg-accent-green',
  connecting: 'bg-accent-yellow animate-pulse',
  disconnected: 'bg-gray-500',
  error: 'bg-accent-red',
};

export function ConnectionStatus() {
  const wsState = useChatStore((s) => s.wsState);
  const twitchConnected = useChatStore((s) => s.twitchConnected);
  const channel = useChatStore((s) => s.channel);
  const backendConnected = useChatStore((s) => s.backendConnected);

  return (
    <div className="flex items-center gap-4 text-xs text-gray-400">
      {/* Backend */}
      <div className="flex items-center gap-1.5">
        <span
          className={clsx(
            'h-2 w-2 rounded-full',
            backendConnected ? 'bg-accent-green' : 'bg-gray-500'
          )}
        />
        <span>Engine</span>
      </div>

      {/* WebSocket */}
      <div className="flex items-center gap-1.5">
        <span className={clsx('h-2 w-2 rounded-full', WS_COLORS[wsState] ?? 'bg-gray-500')} />
        <span>{WS_LABELS[wsState] ?? wsState}</span>
      </div>

      {/* Twitch */}
      <div className="flex items-center gap-1.5">
        <span
          className={clsx(
            'h-2 w-2 rounded-full',
            twitchConnected ? 'bg-accent-purple' : 'bg-gray-500'
          )}
        />
        <span>{twitchConnected && channel ? `#${channel}` : 'Twitch'}</span>
      </div>
    </div>
  );
}
