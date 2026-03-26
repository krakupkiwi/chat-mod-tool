/**
 * ClusterPanel — shows active semantic clusters detected by the engine.
 * Each cluster shows "Timeout All" and "Ban All" buttons with a confirmation step.
 */

import { useMemo, useState } from 'react';
import { useChatStore } from '../store/chatStore';
import type { ClusterInfo } from '../store/chatStore';


interface Props {
  clusters: ClusterInfo[];
  port: number;
  ipcSecret: string;
}

type ActionState = 'idle' | 'confirm-timeout' | 'confirm-ban' | 'busy' | 'done';

function ClusterCard({
  cluster,
  port,
  ipcSecret,
}: {
  cluster: ClusterInfo;
  port: number;
  ipcSecret: string;
}) {
  const responseState = useChatStore((s) => s.responseState);
  const messages     = useChatStore((s) => s.messages);
  const setSelectedUser = useChatStore((s) => s.setSelectedUser);
  const [state, setState] = useState<ActionState>('idle');
  const [result, setResult] = useState('');

  // Build user_id → username map from recent messages
  const userMap = useMemo(() => {
    const m = new Map<string, string>();
    for (const msg of messages) m.set(msg.userId, msg.username);
    return m;
  }, [messages]);

  async function execute(type: 'timeout' | 'ban') {
    setState('busy');
    const url = `http://127.0.0.1:${port}/api/moderation/cluster/${type}`;
    try {
      const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-IPC-Secret': ipcSecret },
        body: JSON.stringify({
          cluster_id: cluster.cluster_id,
          user_ids: cluster.user_ids,
          duration_seconds: 300,
          reason: `Coordinated bot cluster ${cluster.cluster_id}`,
        }),
      });
      const data = await res.json();
      setResult(`${type === 'ban' ? 'Banned' : 'Timed out'} ${data.enqueued} accounts${responseState.dryRunMode ? ' (dry-run)' : ''}`);
      setState('done');
    } catch {
      setState('idle');
    }
  }

  return (
    <div className="border border-red-900/50 rounded bg-red-950/20 px-2 py-1.5">
      <div className="flex items-center justify-between mb-1">
        <div className="flex items-center gap-1.5">
          <span className="text-red-400 text-xs font-bold">{cluster.size} accounts</span>
          {cluster.channel && (
            <span className="text-[9px] font-mono bg-surface-3 text-gray-400 rounded px-1 py-0.5">
              #{cluster.channel}
            </span>
          )}
        </div>
        <span className="text-gray-600 text-[10px] font-mono">{cluster.cluster_id}</span>
      </div>
      <div className="text-gray-400 text-[11px] truncate italic mb-1.5">
        "{cluster.sample_message}"
      </div>

      {/* Account list */}
      <div className="flex flex-wrap gap-1 mb-1.5">
        {cluster.user_ids.slice(0, 12).map((uid) => {
          const name = userMap.get(uid) ?? uid.slice(0, 10);
          return (
            <button
              key={uid}
              onClick={() => setSelectedUser({ userId: uid, username: name })}
              className="text-[10px] font-mono bg-surface-3 hover:bg-red-900/30 text-gray-300 hover:text-red-300 rounded px-1 py-0.5 transition-colors"
            >
              {name}
            </button>
          );
        })}
        {cluster.user_ids.length > 12 && (
          <span className="text-[10px] text-gray-600 self-center">
            +{cluster.user_ids.length - 12} more
          </span>
        )}
      </div>

      {state === 'done' ? (
        <div className="text-[10px] text-green-400">{result}</div>
      ) : state === 'busy' ? (
        <div className="text-[10px] text-gray-500">Executing…</div>
      ) : state === 'confirm-timeout' ? (
        <div className="flex items-center gap-1.5">
          <span className="text-[10px] text-orange-400">Timeout {cluster.size} users?</span>
          <button onClick={() => execute('timeout')} className="text-[10px] text-orange-400 hover:text-orange-200 font-bold">Yes</button>
          <button onClick={() => setState('idle')} className="text-[10px] text-gray-600 hover:text-gray-400">No</button>
        </div>
      ) : state === 'confirm-ban' ? (
        <div className="flex items-center gap-1.5">
          <span className="text-[10px] text-red-400">Ban {cluster.size} users?</span>
          <button onClick={() => execute('ban')} className="text-[10px] text-red-400 hover:text-red-200 font-bold">Yes</button>
          <button onClick={() => setState('idle')} className="text-[10px] text-gray-600 hover:text-gray-400">No</button>
        </div>
      ) : (
        <div className="flex items-center gap-1.5">
          <button
            onClick={() => setState('confirm-timeout')}
            className="text-[10px] px-1.5 py-0.5 bg-orange-900/30 hover:bg-orange-800/50 border border-orange-800/50 text-orange-400 rounded"
          >
            Timeout All
          </button>
          <button
            onClick={() => setState('confirm-ban')}
            className="text-[10px] px-1.5 py-0.5 bg-red-900/30 hover:bg-red-800/50 border border-red-800/50 text-red-400 rounded"
          >
            Ban All
          </button>
          {responseState.dryRunMode && (
            <span className="text-[10px] text-gray-600">dry-run</span>
          )}
        </div>
      )}
    </div>
  );
}

export function ClusterPanel({ clusters, port, ipcSecret }: Props) {
  const activeChannel = useChatStore((s) => s.activeChannel);

  const visible = activeChannel
    ? clusters.filter((c) => !c.channel || c.channel === activeChannel)
    : clusters;

  if (visible.length === 0) return null;

  return (
    <div className="px-3 py-2 border-t border-surface-3">
      <div className="text-[10px] text-gray-500 uppercase tracking-wider mb-2">
        Active Clusters ({visible.length}{activeChannel ? ` · #${activeChannel}` : ''})
      </div>
      <div className="flex flex-col gap-2">
        {visible.map((c) => (
          <ClusterCard key={c.cluster_id} cluster={c} port={port} ipcSecret={ipcSecret} />
        ))}
      </div>
    </div>
  );
}
