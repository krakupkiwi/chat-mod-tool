/**
 * ClusterPanel — shows active semantic clusters detected by the engine.
 * Each cluster shows "Timeout All" and "Ban All" buttons with a confirmation step.
 */

import { useState } from 'react';
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
  const [state, setState] = useState<ActionState>('idle');
  const [result, setResult] = useState('');

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
        <span className="text-red-400 text-xs font-bold">{cluster.size} accounts</span>
        <span className="text-gray-600 text-[10px] font-mono">{cluster.cluster_id}</span>
      </div>
      <div className="text-gray-400 text-[11px] truncate italic mb-1.5">
        "{cluster.sample_message}"
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
  if (clusters.length === 0) return null;

  return (
    <div className="px-3 py-2 border-t border-surface-3">
      <div className="text-[10px] text-gray-500 uppercase tracking-wider mb-2">
        Active Clusters ({clusters.length})
      </div>
      <div className="flex flex-col gap-2">
        {clusters.map((c) => (
          <ClusterCard key={c.cluster_id} cluster={c} port={port} ipcSecret={ipcSecret} />
        ))}
      </div>
    </div>
  );
}
