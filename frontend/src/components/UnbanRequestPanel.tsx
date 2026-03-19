/**
 * UnbanRequestPanel — review pending Twitch unban requests.
 *
 * Fetches from GET /api/unban-requests on open, polls every 60s.
 * Each request shows the user's message and approve/deny buttons.
 * Decisions are logged locally to SQLite via the backend.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

interface UnbanRequest {
  id: string;
  user_id: string;
  user_name: string;
  user_login: string;
  moderator_id?: string;
  moderator_name?: string;
  created_at: string;
  text: string;
  status: string;
}

interface Props {
  port: number;
  ipcSecret: string;
}

type DecisionState = 'idle' | 'confirming-approve' | 'confirming-deny' | 'busy' | 'done';

function UnbanRow({
  req,
  port,
  ipcSecret,
  onResolved,
}: {
  req: UnbanRequest;
  port: number;
  ipcSecret: string;
  onResolved: (id: string) => void;
}) {
  const [state, setState] = useState<DecisionState>('idle');
  const [resolution, setResolution] = useState('');

  async function resolve(decision: 'approve' | 'deny') {
    setState('busy');
    try {
      await fetch(`http://127.0.0.1:${port}/api/unban-requests/${encodeURIComponent(req.id)}/${decision}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-IPC-Secret': ipcSecret },
        body: JSON.stringify({
          resolution_text: resolution.trim(),
          user_id: req.user_id,
          username: req.user_login,
          request_text: req.text,
        }),
      });
      setState('done');
      setTimeout(() => onResolved(req.id), 800);
    } catch {
      setState('idle');
    }
  }

  const timeAgo = formatTimeAgo(req.created_at);

  return (
    <div className="px-3 py-2 border-b border-surface-3 last:border-0">
      <div className="flex items-center justify-between mb-1">
        <span className="text-xs text-gray-200 font-mono">{req.user_login ?? req.user_name}</span>
        <span className="text-[10px] text-gray-600">{timeAgo}</span>
      </div>
      {req.text && (
        <div className="text-xs text-gray-400 italic leading-snug mb-1.5 break-words">
          "{req.text}"
        </div>
      )}

      {state === 'done' ? (
        <div className="text-[10px] text-green-400">Decision sent</div>
      ) : state === 'busy' ? (
        <div className="text-[10px] text-gray-500">Processing…</div>
      ) : state === 'confirming-approve' || state === 'confirming-deny' ? (
        <div className="flex flex-col gap-1.5">
          <input
            autoFocus
            type="text"
            value={resolution}
            onChange={(e) => setResolution(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') resolve(state === 'confirming-approve' ? 'approve' : 'deny');
              if (e.key === 'Escape') setState('idle');
            }}
            placeholder="Optional message to user…"
            className="text-xs bg-surface-2 border border-surface-3 rounded px-2 py-0.5 text-white w-full focus:outline-none focus:border-accent-purple"
          />
          <div className="flex items-center gap-2">
            <button
              onClick={() => resolve(state === 'confirming-approve' ? 'approve' : 'deny')}
              className={`text-[10px] px-2 py-0.5 rounded border font-bold ${
                state === 'confirming-approve'
                  ? 'bg-green-900/40 border-green-700/50 text-green-400'
                  : 'bg-red-900/40 border-red-700/50 text-red-400'
              }`}
            >
              Confirm {state === 'confirming-approve' ? 'Approve' : 'Deny'}
            </button>
            <button onClick={() => setState('idle')} className="text-[10px] text-gray-600 hover:text-gray-400">
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <div className="flex items-center gap-2">
          <button
            onClick={() => setState('confirming-approve')}
            className="text-[10px] px-2 py-0.5 bg-green-900/30 hover:bg-green-800/50 border border-green-800/50 text-green-400 rounded"
          >
            Approve
          </button>
          <button
            onClick={() => setState('confirming-deny')}
            className="text-[10px] px-2 py-0.5 bg-red-900/30 hover:bg-red-800/50 border border-red-800/50 text-red-400 rounded"
          >
            Deny
          </button>
        </div>
      )}
    </div>
  );
}

export function UnbanRequestPanel({ port, ipcSecret }: Props) {
  const [requests, setRequests] = useState<UnbanRequest[]>([]);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const [missingScope, setMissingScope] = useState(false);
  // Ref so the interval callback sees the latest value without recreating the callback
  const missingScopeRef = useRef(false);

  const fetchRequests = useCallback(async () => {
    if (missingScopeRef.current) return;
    setLoading(true);
    try {
      const res = await fetch(`http://127.0.0.1:${port}/api/unban-requests`, {
        headers: { 'X-IPC-Secret': ipcSecret },
      });
      if (res.status === 403) {
        missingScopeRef.current = true;
        setMissingScope(true);
      } else if (res.ok) {
        const data = await res.json();
        setRequests(data.requests ?? []);
      }
    } catch { /* ignore */ }
    setLoading(false);
  }, [port, ipcSecret]);

  // Only poll while the panel is open — no background requests when collapsed
  useEffect(() => {
    if (!open) return;
    fetchRequests();
    const interval = setInterval(fetchRequests, 60_000);
    return () => clearInterval(interval);
  }, [open, fetchRequests]);

  function onResolved(id: string) {
    setRequests((r) => r.filter((req) => req.id !== id));
  }

  return (
    <div className="border-t border-surface-3">
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between px-3 py-1.5 text-[10px] font-semibold text-gray-500 uppercase tracking-wider hover:text-gray-300 transition-colors"
      >
        <span>Unban Requests</span>
        <div className="flex items-center gap-2">
          {loading && <span className="text-gray-600">…</span>}
          {missingScope && (
            <span className="text-yellow-700 text-[10px]" title="Re-auth required">⚠</span>
          )}
          {!missingScope && requests.length > 0 && (
            <span className="bg-purple-900/60 text-purple-300 rounded-full px-1.5 text-[10px]">
              {requests.length}
            </span>
          )}
          <span>{open ? '▴' : '▾'}</span>
        </div>
      </button>

      {open && (
        <div className="overflow-y-auto max-h-64">
          {missingScope ? (
            <div className="px-3 py-3 text-xs text-yellow-700 text-center">
              Requires <code className="text-yellow-600">moderator:manage:unban_requests</code> scope — click <span className="text-accent-purple">Re-auth</span> in the header.
            </div>
          ) : requests.length === 0 ? (
            <div className="px-3 py-3 text-xs text-gray-600 text-center">No pending requests</div>
          ) : (
            requests.map((req) => (
              <UnbanRow
                key={req.id}
                req={req}
                port={port}
                ipcSecret={ipcSecret}
                onResolved={onResolved}
              />
            ))
          )}
        </div>
      )}
    </div>
  );
}

function formatTimeAgo(isoDate: string): string {
  const seconds = Math.floor((Date.now() - new Date(isoDate).getTime()) / 1000);
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}
