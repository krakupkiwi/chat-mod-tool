/**
 * ActionLog — shows recent moderation actions with undo button.
 *
 * Displayed in the right panel below ThreatPanel.
 * Each row shows action type, username, reason, triggered-by, status.
 * Completed bans/timeouts show an Undo button that calls DELETE /api/moderation/undo/{dbId}.
 */

import { useChatStore } from '../store/chatStore';
import type { ModerationAction } from '../store/chatStore';

const ACTION_COLORS: Record<string, string> = {
  ban: 'text-red-400',
  timeout: 'text-orange-400',
  warn: 'text-yellow-300',
  delete: 'text-yellow-400',
  slow_mode: 'text-blue-400',
  followers_only: 'text-blue-400',
  emote_only: 'text-blue-400',
  sub_only: 'text-blue-400',
  unique_chat: 'text-blue-400',
};

const STATUS_COLORS: Record<string, string> = {
  pending: 'text-gray-400',
  completed: 'text-green-400',
  failed: 'text-red-500',
  undone: 'text-gray-500',
};

function formatDuration(seconds: number | null): string {
  if (seconds == null) return '';
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  return `${Math.round(seconds / 3600)}h`;
}

function parseAttribution(triggeredBy: string): string {
  if (triggeredBy.startsWith('auto:')) return 'auto';
  if (triggeredBy === 'manual:nuke') return 'nuke';
  if (triggeredBy === 'manual:watchlist') return 'watchlist';
  if (triggeredBy === 'manual:cluster') return 'cluster';
  if (triggeredBy.startsWith('manual:')) {
    const suffix = triggeredBy.slice('manual:'.length);
    return suffix || 'mod';
  }
  return triggeredBy;
}

function ActionRow({
  action,
  port,
  ipcSecret,
}: {
  action: ModerationAction;
  port: number;
  ipcSecret: string;
}) {
  const undoModerationAction = useChatStore((s) => s.undoModerationAction);
  const isAuto = action.triggeredBy.startsWith('auto:');
  const attribution = parseAttribution(action.triggeredBy);
  const canUndo =
    action.status === 'completed' &&
    action.dbId != null &&
    (action.actionType === 'ban' || action.actionType === 'timeout');

  async function handleUndo() {
    if (!canUndo || action.dbId == null) return;
    try {
      const res = await fetch(
        `http://127.0.0.1:${port}/api/moderation/undo/${action.dbId}`,
        {
          method: 'POST',
          headers: { 'X-IPC-Secret': ipcSecret },
        }
      );
      if (res.ok) {
        undoModerationAction(action.actionId);
      }
    } catch {
      // ignore
    }
  }

  return (
    <div className="flex items-start gap-2 px-3 py-2 border-b border-surface-3 last:border-0 hover:bg-surface-2 group">
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5 flex-wrap">
          <span className={`text-xs font-bold uppercase ${ACTION_COLORS[action.actionType] ?? 'text-gray-400'}`}>
            {action.actionType}
          </span>
          {action.durationSeconds != null && (
            <span className="text-xs text-gray-500">({formatDuration(action.durationSeconds)})</span>
          )}
          <span className="text-xs text-gray-200 font-mono truncate max-w-[100px]">
            {action.username}
          </span>
          {action.dryRun && (
            <span className="text-xs text-gray-600 italic">dry-run</span>
          )}
        </div>

        <div className="text-xs text-gray-500 truncate mt-0.5">{action.reason}</div>

        <div className="flex items-center gap-2 mt-0.5">
          <span className={`text-xs ${STATUS_COLORS[action.status] ?? 'text-gray-400'}`}>
            {action.status}
          </span>
          <span className={`text-xs ${isAuto ? 'text-gray-600' : 'text-gray-500'}`}>
            {attribution}
          </span>
          {action.confidence != null && (
            <span className="text-xs text-gray-600">
              {action.confidence.toFixed(0)}%
            </span>
          )}
        </div>
      </div>

      {canUndo && (
        <button
          onClick={handleUndo}
          className="text-xs text-gray-600 hover:text-accent-purple opacity-0 group-hover:opacity-100 transition-opacity shrink-0 mt-0.5"
          title="Undo this action"
        >
          Undo
        </button>
      )}
    </div>
  );
}

interface ActionLogProps {
  port: number;
  ipcSecret: string;
}

export function ActionLog({ port, ipcSecret }: ActionLogProps) {
  const actions = useChatStore((s) => s.moderationActions);

  if (actions.length === 0) return null;

  return (
    <div className="flex flex-col">
      <div className="px-3 py-1.5 text-xs font-semibold text-gray-500 uppercase tracking-wide border-b border-surface-3">
        Actions ({actions.length})
      </div>
      <div className="overflow-y-auto max-h-48">
        {actions.map((action) => (
          <ActionRow
            key={action.actionId}
            action={action}
            port={port}
            ipcSecret={ipcSecret}
          />
        ))}
      </div>
    </div>
  );
}
