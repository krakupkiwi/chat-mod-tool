import { lazy, Suspense, useCallback, useEffect, useState } from 'react';
import { useBackend } from './hooks/useBackend';
import { useWebSocket } from './hooks/useWebSocket';
import { useChatStore, type ConnectionState } from './store/chatStore';
import { ActionLog } from './components/ActionLog';
import { BotNetworkGraph } from './components/BotNetworkGraph';
import { ChatFeed } from './components/ChatFeed';
import { ChatModeBar } from './components/ChatModeBar';
import { ClusterPanel } from './components/ClusterPanel';
import { ConnectionStatus } from './components/ConnectionStatus';
import { HealthScoreMeter } from './components/HealthScoreMeter';
import { HealthTimeline } from './components/HealthTimeline';
import { SetupScreen } from './components/SetupScreen';
import { SettingsDrawer } from './components/SettingsDrawer';
import { SignalBreakdown } from './components/SignalBreakdown';
import { PerfPanel } from './components/PerfPanel';
import { Splash } from './components/Splash';
import { ThreatPanel } from './components/ThreatPanel';
import { UserDetailPanel } from './components/UserDetailPanel';
import { ChannelEventFeed } from './components/ChannelEventFeed';
import { ChatInput } from './components/ChatInput';
import { AutomodQueueWidget } from './components/AutomodQueueWidget';
import { ChannelBar } from './components/ChannelBar';
import { LockdownProfilePanel } from './components/LockdownProfilePanel';
import { BanListImportModal } from './components/BanListImportModal';
import { DataManagerModal } from './components/DataManagerModal';
import { FollowerAuditModal } from './components/FollowerAuditModal';
import { NukeModal } from './components/NukeModal';
import { UnbanRequestPanel } from './components/UnbanRequestPanel';
import { WatchlistPanel } from './components/WatchlistPanel';

// Recharts (~450KB) is only needed on the Analytics tab — split it into its own chunk
const StatsPage = lazy(() => import('./components/StatsPage').then((m) => ({ default: m.StatsPage })));

function ChannelControl({ port, ipcSecret }: { port: number; ipcSecret: string }) {
  const configuredChannel = useChatStore((s) => s.configuredChannel);
  const setConfiguredChannel = useChatStore((s) => s.setConfiguredChannel);
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState('');
  const [saving, setSaving] = useState(false);

  async function save() {
    const ch = value.trim().replace(/^#/, '');
    if (!ch) return;
    setSaving(true);
    try {
      await fetch(`http://127.0.0.1:${port}/api/config`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json', 'X-IPC-Secret': ipcSecret },
        body: JSON.stringify({ default_channel: ch }),
      });
      setConfiguredChannel(ch);
      setEditing(false);
    } catch {
      // keep editing open on error
    } finally {
      setSaving(false);
    }
  }

  if (editing) {
    return (
      <div className="flex items-center gap-2">
        <span className="text-white font-bold tracking-wide">TwitchIDS</span>
        <input
          type="text"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') save(); if (e.key === 'Escape') setEditing(false); }}
          placeholder="channelname"
          className="bg-surface-2 border border-surface-3 rounded px-2 py-0.5 text-sm text-white w-36 focus:outline-none focus:border-accent-purple"
          autoFocus
        />
        <button onClick={save} disabled={saving || !value.trim()} className="text-xs text-accent-purple disabled:opacity-40">
          {saving ? '…' : 'Save'}
        </button>
        <button onClick={() => setEditing(false)} className="text-xs text-gray-500 hover:text-gray-300">✕</button>
      </div>
    );
  }

  return (
    <div className="flex items-center gap-3">
      <span className="text-white font-bold tracking-wide">TwitchIDS</span>
      {configuredChannel ? (
        <button
          title="Click to change channel"
          onClick={() => { setValue(configuredChannel); setEditing(true); }}
          className="text-accent-purple text-sm hover:underline"
        >
          #{configuredChannel}
        </button>
      ) : (
        <button
          onClick={() => { setValue(''); setEditing(true); }}
          className="text-gray-500 text-sm hover:text-gray-300"
        >
          + set channel
        </button>
      )}
    </div>
  );
}

function ReauthButton({ port, ipcSecret }: { port: number; ipcSecret: string }) {
  const [state, setState] = useState<'idle' | 'waiting'>('idle');

  async function handleReauth() {
    setState('waiting');
    try {
      await fetch(`http://127.0.0.1:${port}/api/auth/reauth`, {
        method: 'POST',
        headers: { 'X-IPC-Secret': ipcSecret },
      });
    } catch {
      setState('idle');
      return;
    }

    // Poll until the backend reports authenticated (PKCE flow completed).
    // Uses exponential backoff to avoid ~200 requests over 5 minutes.
    const AUTH_POLL_INTERVALS = [1500, 2000, 3000, 5000, 10000, 30000];
    let attempt = 0;
    let timeoutId: ReturnType<typeof setTimeout>;
    let giveUpId: ReturnType<typeof setTimeout>;

    const pollAuth = async () => {
      try {
        const res = await fetch(`http://127.0.0.1:${port}/api/auth/status`, {
          headers: { 'X-IPC-Secret': ipcSecret },
        });
        const data = await res.json();
        if (data.authenticated) {
          clearTimeout(giveUpId);
          window.location.reload();
          return;
        }
      } catch { /* keep polling */ }
      const delay = AUTH_POLL_INTERVALS[Math.min(attempt++, AUTH_POLL_INTERVALS.length - 1)];
      timeoutId = setTimeout(pollAuth, delay);
    };

    pollAuth();

    // Give up after 5 minutes
    giveUpId = setTimeout(() => { clearTimeout(timeoutId); setState('idle'); }, 300_000);
  }

  return (
    <button
      onClick={handleReauth}
      disabled={state === 'waiting'}
      title="Re-authorize with Twitch to update granted scopes"
      className="text-xs text-gray-500 hover:text-gray-300 disabled:opacity-40 transition-colors"
    >
      {state === 'waiting' ? 'Waiting for browser auth…' : 'Re-auth'}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Connection progress bar (footer)
// ---------------------------------------------------------------------------

const STEPS = [
  { key: 'backend',  label: 'Backend' },
  { key: 'ws',       label: 'IDS Engine' },
  { key: 'twitch',   label: 'Twitch' },
  { key: 'ready',    label: 'Ready' },
] as const;

function ConnectionProgress({
  twitchConnected,
  wsState,
  displayChannel,
}: {
  twitchConnected: boolean;
  wsState: ConnectionState;
  displayChannel: string | null;
}) {
  // Derive current step index
  const stepIdx = twitchConnected ? 3 : wsState === 'connected' ? 2 : wsState === 'connecting' ? 1 : 0;
  const pct = Math.round((stepIdx / (STEPS.length - 1)) * 100);

  const statusText = twitchConnected
    ? `Monitoring #${displayChannel}`
    : wsState === 'connected'
      ? displayChannel ? `Connecting to #${displayChannel}…` : 'Authenticating with Twitch…'
      : wsState === 'connecting'
        ? 'Connecting to IDS engine…'
        : wsState === 'error'
          ? 'Connection error — retrying…'
          : 'Disconnected';

  if (twitchConnected) {
    return <span className="text-gray-500">{statusText}</span>;
  }

  return (
    <div className="flex items-center gap-2">
      {/* Step dots */}
      <div className="flex items-center gap-1">
        {STEPS.map((step, i) => (
          <div key={step.key} className="flex items-center gap-1">
            <div
              className={`w-1.5 h-1.5 rounded-full transition-colors duration-500 ${
                i <= stepIdx ? 'bg-accent-purple' : 'bg-surface-3'
              }`}
            />
            <span className={`text-[10px] ${i <= stepIdx ? 'text-gray-400' : 'text-gray-600'}`}>
              {step.label}
            </span>
            {i < STEPS.length - 1 && (
              <div className={`w-4 h-px mx-0.5 transition-colors duration-500 ${i < stepIdx ? 'bg-accent-purple' : 'bg-surface-3'}`} />
            )}
          </div>
        ))}
      </div>
      {/* Progress bar */}
      <div className="w-16 h-0.5 bg-surface-3 rounded-full overflow-hidden">
        <div
          className="h-full bg-accent-purple transition-all duration-700 ease-out"
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-gray-500">{statusText}</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Toolbar SVG icons
// ---------------------------------------------------------------------------

function IcnDatabase() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <ellipse cx="12" cy="5" rx="9" ry="3" />
      <path d="M21 5v6c0 1.66-4.03 3-9 3S3 12.66 3 11V5" />
      <path d="M21 11v6c0 1.66-4.03 3-9 3S3 18.66 3 17v-6" />
    </svg>
  );
}

function IcnClipboard() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2" />
      <rect x="9" y="3" width="6" height="4" rx="1" />
      <path d="M9 12h6M9 16h4" />
    </svg>
  );
}

function IcnUsers() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2" />
      <circle cx="9" cy="7" r="4" />
      <path d="M23 21v-2a4 4 0 00-3-3.87" />
      <path d="M16 3.13a4 4 0 010 7.75" />
    </svg>
  );
}

/**
 * Radiation / trefoil hazard symbol.
 * Three equal sectors at 120° intervals around a centre disc.
 * Sector geometry (24×24 canvas, centre 12,12, inner r=3, outer r=8.4):
 *   top         270°–330°  outer: (12,3.6)→(19.27,7.8)   inner: (14.6,10.5)→(12,9)
 *   bottom-right  30°–90°  outer: (19.27,16.2)→(12,20.4)  inner: (12,15)→(14.6,13.5)
 *   bottom-left 150°–210°  outer: (4.73,16.2)→(4.73,7.8)  inner: (9.4,10.5)→(9.4,13.5)
 * Outer arc CW (sweep=1), inner arc CCW (sweep=0).
 */
function IcnNuke() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
      <circle cx="12" cy="12" r="2.6" />
      <path d="M12 3.6 A8.4 8.4 0 0 1 19.27 7.8 L14.6 10.5 A3 3 0 0 0 12 9Z" />
      <path d="M19.27 16.2 A8.4 8.4 0 0 1 12 20.4 L12 15 A3 3 0 0 0 14.6 13.5Z" />
      <path d="M4.73 16.2 A8.4 8.4 0 0 1 4.73 7.8 L9.4 10.5 A3 3 0 0 0 9.4 13.5Z" />
    </svg>
  );
}

function IcnRefresh({ spinning = false }: { spinning?: boolean }) {
  return (
    <svg
      width="20" height="20" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"
      className={spinning ? 'animate-spin' : undefined}
    >
      <polyline points="23 4 23 10 17 10" />
      <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" />
    </svg>
  );
}

function IcnSettings() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 010 2.83 2 2 0 01-2.83 0l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83-2.83l.06-.06A1.65 1.65 0 004.68 15a1.65 1.65 0 00-1.51-1H3a2 2 0 010-4h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 012.83-2.83l.06.06A1.65 1.65 0 009 4.68a1.65 1.65 0 001-1.51V3a2 2 0 014 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 2.83l-.06.06A1.65 1.65 0 0019.4 9a1.65 1.65 0 001.51 1H21a2 2 0 010 4h-.09a1.65 1.65 0 00-1.51 1z" />
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Dashboard
// ---------------------------------------------------------------------------

function Dashboard({ port, ipcSecret }: { port: number; ipcSecret: string }) {
  const health = useChatStore((s) => s.health);
  const messages = useChatStore((s) => s.messages);
  const activeChannel = useChatStore((s) => s.activeChannel);
  const twitchConnected = useChatStore((s) => s.twitchConnected);
  const wsState = useChatStore((s) => s.wsState);
  const channel = useChatStore((s) => s.channel);
  const configuredChannel = useChatStore((s) => s.configuredChannel);
  const responseState = useChatStore((s) => s.responseState);
  const displayChannel = channel || configuredChannel;
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [banListOpen, setBanListOpen] = useState(false);
  const [dataManagerOpen, setDataManagerOpen] = useState(false);
  const [nukeOpen, setNukeOpen] = useState(false);
  const [followerAuditOpen, setFollowerAuditOpen] = useState(false);
  const [activeTab, setActiveTab] = useState<'dashboard' | 'stats'>('dashboard');
  const [refreshing, setRefreshing] = useState(false);
  const closeSettings = useCallback(() => setSettingsOpen(false), []);
  const bumpDataRefreshKey = useChatStore((s) => s.bumpDataRefreshKey);
  const clearAlerts = useChatStore((s) => s.clearAlerts);

  function handleRefresh() {
    setRefreshing(true);
    clearAlerts();
    bumpDataRefreshKey();
    setTimeout(() => setRefreshing(false), 800);
  }

  const healthColor = () => {
    if (!health) return 'text-gray-400';
    if (health.score >= 80) return 'text-green-400';
    if (health.score >= 65) return 'text-yellow-400';
    if (health.score >= 45) return 'text-orange-400';
    return 'text-red-400';
  };

  const levelLabel: Record<string, string> = {
    healthy: 'Healthy',
    elevated: 'Elevated',
    suspicious: 'Suspicious',
    likely_attack: 'Likely Attack',
    critical: 'Critical',
  };

  return (
    <div className="flex flex-col h-screen bg-surface overflow-hidden">
      {/* Top bar */}
      <header className="flex items-center justify-between px-4 py-2 bg-surface-1 border-b border-surface-3 shrink-0">
        <ChannelControl port={port} ipcSecret={ipcSecret} />

        <div className="flex items-center gap-6">
          {/* Chat health score */}
          {health && (
            <div className="flex items-center gap-2">
              <span className="text-gray-400 text-xs">Chat Health</span>
              <span className={`text-2xl font-bold font-mono ${healthColor()}`}>
                {Math.round(health.score)}
              </span>
              <span className="text-gray-500 text-xs">{levelLabel[health.level] ?? health.level}</span>
            </div>
          )}

          <div className="flex items-center gap-1 bg-surface-2 rounded p-0.5">
            {(['dashboard', 'stats'] as const).map((tab) => (
              <button
                key={tab}
                onClick={() => setActiveTab(tab)}
                className={`text-xs px-3 py-1 rounded transition-colors ${
                  activeTab === tab
                    ? 'bg-surface-3 text-gray-200'
                    : 'text-gray-500 hover:text-gray-300'
                }`}
              >
                {tab === 'dashboard' ? 'Dashboard' : 'Analytics'}
              </button>
            ))}
          </div>
          <ReauthButton port={port} ipcSecret={ipcSecret} />

          <div className="flex items-center gap-0.5 ml-1">
            <button
              onClick={() => setDataManagerOpen(true)}
              title="Data Manager — browse, export, import"
              className="p-1.5 rounded text-gray-500 hover:text-blue-400 hover:bg-surface-2 transition-colors"
            >
              <IcnDatabase />
            </button>
            <button
              onClick={() => setBanListOpen(true)}
              title="Import shared ban list"
              className="p-1.5 rounded text-gray-500 hover:text-orange-400 hover:bg-surface-2 transition-colors"
            >
              <IcnClipboard />
            </button>
            <button
              onClick={() => setFollowerAuditOpen(true)}
              title="Follower Bot Audit — scan followers against known-bot list"
              className="p-1.5 rounded text-gray-500 hover:text-yellow-400 hover:bg-surface-2 transition-colors"
            >
              <IcnUsers />
            </button>
            <button
              onClick={() => setNukeOpen(true)}
              title="Nuke Tool — bulk moderation by phrase/regex"
              className="p-1.5 rounded text-gray-500 hover:text-red-400 hover:bg-surface-2 transition-colors"
            >
              <IcnNuke />
            </button>
            <button
              onClick={handleRefresh}
              disabled={refreshing}
              title="Refresh — reload threat history and clear live alerts"
              className={`p-1.5 rounded transition-colors ${refreshing ? 'text-accent-purple' : 'text-gray-500 hover:text-gray-200 hover:bg-surface-2'}`}
            >
              <IcnRefresh spinning={refreshing} />
            </button>
            <button
              onClick={() => setSettingsOpen(true)}
              title="Settings"
              className="p-1.5 rounded text-gray-500 hover:text-gray-200 hover:bg-surface-2 transition-colors"
            >
              <IcnSettings />
            </button>
          </div>
          <ConnectionStatus />
        </div>
      </header>
      <SettingsDrawer port={port} ipcSecret={ipcSecret} open={settingsOpen} onClose={closeSettings} />
      {dataManagerOpen && (
        <DataManagerModal
          port={port}
          ipcSecret={ipcSecret}
          onClose={() => setDataManagerOpen(false)}
          onOpenBanList={() => { setDataManagerOpen(false); setBanListOpen(true); }}
        />
      )}
      {banListOpen && <BanListImportModal port={port} ipcSecret={ipcSecret} onClose={() => setBanListOpen(false)} />}
      {nukeOpen && <NukeModal port={port} ipcSecret={ipcSecret} onClose={() => setNukeOpen(false)} />}
      {followerAuditOpen && (
        <FollowerAuditModal port={port} ipcSecret={ipcSecret} onClose={() => setFollowerAuditOpen(false)} />
      )}

      {/* Metrics strip */}
      {health && (
        <div className="flex items-center gap-6 px-4 py-1.5 bg-surface-1 border-b border-surface-3 text-xs text-gray-400 shrink-0">
          <Metric label="Msg/min" value={Math.round(health.messagesPerMinute)} />
          <Metric label="Users" value={health.activeUsers} />
          <Metric label="Dup ratio" value={`${(health.duplicateRatio * 100).toFixed(1)}%`} />
          <Metric label="Trend" value={health.trend} />
          {health.activeSignals.length > 0 && (
            <div className="flex items-center gap-1 ml-auto">
              <span className="text-yellow-500">Signals:</span>
              {health.activeSignals.map((s) => (
                <span key={s} className="bg-surface-3 rounded px-1.5 py-0.5 text-yellow-300">
                  {s}
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Main content */}
      <main className="flex flex-1 overflow-hidden">
        {activeTab === 'stats' ? (
          <Suspense fallback={<div className="flex items-center justify-center flex-1 text-gray-400">Loading analytics…</div>}>
            <StatsPage port={port} ipcSecret={ipcSecret} />
          </Suspense>
        ) : (
          <>
            {/* Chat feed — takes most of the space */}
            <div className="flex flex-col flex-1 overflow-hidden border-r border-surface-3">
              <div className="px-3 py-1.5 bg-surface-1 border-b border-surface-3 text-xs text-gray-500 shrink-0">
                {activeChannel ? `#${activeChannel}` : 'ALL CHANNELS'} —{' '}
                {activeChannel
                  ? messages.filter((m) => m.channel === activeChannel).length
                  : messages.length} messages
              </div>
              <ChannelBar port={port} ipcSecret={ipcSecret} />
              {twitchConnected && <ChatModeBar port={port} ipcSecret={ipcSecret} />}
              {twitchConnected && <LockdownProfilePanel port={port} ipcSecret={ipcSecret} />}
              <div className="flex-1 overflow-hidden">
                <ChatFeed />
              </div>
              <ChatInput port={port} ipcSecret={ipcSecret} disabled={!twitchConnected} />
            </div>

            {/* Right panel — health score + signals + threats */}
            <div className="w-72 flex flex-col bg-surface-1 overflow-y-auto">
              {health ? (
                <>
                  <HealthScoreMeter health={health} />
                  <div className="border-t border-surface-3">
                    <HealthTimeline />
                  </div>
                  <div className="border-t border-surface-3">
                    <SignalBreakdown health={health} />
                  </div>
                  <ClusterPanel clusters={health.clusters} port={port} ipcSecret={ipcSecret} />
                  <div className="shrink-0">
                    <BotNetworkGraph clusters={health.clusters} />
                  </div>
                  <AutomodQueueWidget port={port} ipcSecret={ipcSecret} />
                  <div className="border-t border-surface-3">
                    <ThreatPanel port={port} ipcSecret={ipcSecret} />
                  </div>
                  <ChannelEventFeed />
                  <WatchlistPanel port={port} ipcSecret={ipcSecret} />
                  <UnbanRequestPanel port={port} ipcSecret={ipcSecret} />
                  <div className="border-t border-surface-3">
                    <ActionLog port={port} ipcSecret={ipcSecret} />
                  </div>
                  <PerfPanel />
                </>
              ) : (
                <div className="flex-1 flex items-center justify-center text-gray-600 text-xs">
                  Waiting for detection data…
                </div>
              )}
            </div>
          </>
        )}
      </main>

      {/* Status bar */}
      <footer className="flex items-center px-4 py-1 bg-surface-1 border-t border-surface-3 text-xs shrink-0 gap-3">
        <ConnectionProgress twitchConnected={twitchConnected} wsState={wsState} displayChannel={displayChannel} />
        <span className="ml-auto flex items-center gap-2 text-gray-500">
          {responseState.detectionSuppressed && (
            <span className="text-yellow-500">
              Detection suppressed{responseState.suppressionReason ? ` (${responseState.suppressionReason})` : ''}
            </span>
          )}
          {responseState.dryRunMode
            ? <span className="text-gray-600">Dry-run — actions logged only</span>
            : <span className="text-green-600">Live moderation active</span>
          }
        </span>
      </footer>

      <UserDetailPanel port={port} ipcSecret={ipcSecret} />
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="flex items-center gap-1">
      <span className="text-gray-500">{label}</span>
      <span className="text-gray-200 font-mono">{value}</span>
    </div>
  );
}

export default function App() {
  useBackend();

  const backendConfig = useChatStore((s) => s.backendConfig);
  const setConfiguredChannel = useChatStore((s) => s.setConfiguredChannel);
  const setResponseState = useChatStore((s) => s.setResponseState);
  const responseState = useChatStore((s) => s.responseState);

  // Connect WebSocket once backend config is available
  useWebSocket(backendConfig?.port ?? null, backendConfig?.ipcSecret ?? null);

  // Sync tray Ctrl+M mute toggle back to the React response state
  useEffect(() => {
    window.electronAPI?.onDetectionMuteChanged(({ muted }) => {
      setResponseState({ ...responseState, dryRunMode: muted });
    });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Check if setup is needed (no backend yet or not connected to Twitch)
  const [checkingSetup, setCheckingSetup] = useState(true);
  const [needsSetup, setNeedsSetup] = useState(false);

  useEffect(() => {
    if (!backendConfig) return;

    async function checkAuth() {
      try {
        const res = await fetch(
          `http://127.0.0.1:${backendConfig!.port}/api/auth/status`,
          { headers: { 'X-IPC-Secret': backendConfig!.ipcSecret } }
        );
        if (!res.ok) {
          setNeedsSetup(true);
          return;
        }
        const data = await res.json();
        const needs = !data.authenticated || !data.client_id_configured;
        setNeedsSetup(needs);

        // Load configured channel from /api/config regardless of auth state
        if (!needs) {
          try {
            const cfgRes = await fetch(
              `http://127.0.0.1:${backendConfig!.port}/api/config`,
              { headers: { 'X-IPC-Secret': backendConfig!.ipcSecret } }
            );
            if (cfgRes.ok) {
              const cfg = await cfgRes.json();
              if (cfg.default_channel) setConfiguredChannel(cfg.default_channel);
            }
          } catch { /* non-critical */ }
        }
      } catch {
        setNeedsSetup(true);
      } finally {
        setCheckingSetup(false);
      }
    }

    checkAuth();
  }, [backendConfig]); // eslint-disable-line react-hooks/exhaustive-deps

  // While waiting for backend config
  if (!backendConfig) {
    return <Splash message="Starting backend… this usually takes a few seconds" />;
  }

  if (checkingSetup) {
    return <Splash message="Checking configuration…" />;
  }

  if (needsSetup) {
    return (
      <SetupScreen port={backendConfig.port} ipcSecret={backendConfig.ipcSecret} />
    );
  }

  return <Dashboard port={backendConfig.port} ipcSecret={backendConfig.ipcSecret} />;
}
