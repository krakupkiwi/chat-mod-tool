/**
 * Zustand store — single source of truth for the React dashboard.
 *
 * Two slices:
 *   backendSlice  — connection state, port, IPC secret
 *   chatSlice     — incoming messages, connection status, health score
 */

import { create } from 'zustand';

// -------------------------------------------------------------------------
// Types
// -------------------------------------------------------------------------

export interface BackendConfig {
  port: number;
  ipcSecret: string;
}

export interface ChatFragment {
  type: 'text' | 'emote' | 'cheermote' | 'mention';
  text: string;
  emote_id?: string;
}

export interface ChatMessage {
  id: string;
  userId: string;
  username: string;
  content: string;
  channel: string;
  timestamp: number;
  threatScore: number;
  flags: string[];
  color?: string;
  fragments: ChatFragment[];
}

export type ConnectionState = 'disconnected' | 'connecting' | 'connected' | 'error';

export interface ClusterInfo {
  cluster_id: string;
  user_ids: string[];
  size: number;
  sample_message: string;
  channel?: string;
}

export interface EventLogEntry {
  id: string;
  type: 'cluster' | 'health_escalation' | 'threat';
  timestamp: number;
  channel?: string;
  // cluster
  clusterSize?: number;
  clusterSample?: string;
  clusterId?: string;
  userIds?: string[];
  // health escalation
  fromLevel?: string;
  toLevel?: string;
  // threat
  username?: string;
  userId?: string;
  severity?: string;
  description?: string;
}

export interface HealthSnapshot {
  score: number;
  riskScore: number;
  level: 'healthy' | 'elevated' | 'suspicious' | 'likely_attack' | 'critical';
  levelDuration: number;
  trend: 'worsening' | 'stable' | 'improving';
  messagesPerMinute: number;
  activeUsers: number;
  duplicateRatio: number;
  activeSignals: string[];
  metricScores: Record<string, number>;
  clusters: ClusterInfo[];
}

// -------------------------------------------------------------------------
// Store shape
// -------------------------------------------------------------------------

export interface ResponseState {
  dryRunMode: boolean;
  detectionSuppressed: boolean;
  suppressionReason: string | null;
}

export interface PerfSnapshot {
  msg_per_min: number;
  tick_p50_ms: number | null;
  tick_p95_ms: number | null;
  tick_p99_ms: number | null;
  queue_depth: number;
  ws_clients: number;
  memory_mb: number | null;
}

interface Store {
  // Backend connection
  backendConfig: BackendConfig | null;
  backendConnected: boolean;
  setBackendConfig: (config: BackendConfig) => void;
  setBackendConnected: (v: boolean) => void;

  // Twitch connection
  twitchConnected: boolean;
  channel: string | null;         // set when EventSub actually connects
  configuredChannel: string | null; // set from /api/config — survives Twitch disconnection
  setTwitchConnected: (v: boolean, channel?: string) => void;
  setConfiguredChannel: (ch: string | null) => void;

  // WebSocket state
  wsState: ConnectionState;
  setWsState: (s: ConnectionState) => void;

  // Chat messages (capped at 2000 in memory)
  messages: ChatMessage[];
  addMessage: (msg: ChatMessage) => void;
  clearMessages: () => void;

  // Health score
  health: HealthSnapshot | null;
  setHealth: (h: HealthSnapshot) => void;

  // Response state (from health_update.response_state)
  responseState: ResponseState;
  setResponseState: (s: ResponseState) => void;

  // Alerts
  alerts: Alert[];
  addAlert: (a: Alert) => void;
  dismissAlert: (id: string) => void;
  updateAlert: (userId: string, patch: Partial<Alert>) => void;

  // Moderation actions
  moderationActions: ModerationAction[];
  addModerationAction: (a: ModerationAction) => void;
  undoModerationAction: (actionId: string) => void;

  // Channel events (subs, resubs, gift subs)
  channelEvents: ChannelEvent[];
  addChannelEvent: (e: ChannelEvent) => void;

  // Selected user for detail panel
  selectedUser: SelectedUser | null;
  setSelectedUser: (u: SelectedUser | null) => void;

  // Performance telemetry (from health_update.perf)
  perf: PerfSnapshot | null;
  setPerf: (p: PerfSnapshot) => void;

  // User watchlist
  watchedUsers: WatchedUser[];
  setWatchedUsers: (users: WatchedUser[]) => void;
  addWatchedUser: (u: WatchedUser) => void;
  removeWatchedUser: (userId: string) => void;

  // AutoMod held messages queue
  automodQueue: AutomodHeldMessage[];
  addAutomodHeld: (m: AutomodHeldMessage) => void;
  resolveAutomodHeld: (messageId: string) => void;

  // Multi-channel: active filter (null = show all channels)
  activeChannel: string | null;
  setActiveChannel: (ch: string | null) => void;

  // Event log — session history of clusters, health escalations, threats
  eventLog: EventLogEntry[];
  addEventLogEntry: (e: EventLogEntry) => void;
  clearEventLog: () => void;

  // Incremented after a data purge or manual refresh — causes data-fetching
  // components (ThreatPanel, WatchlistPanel, etc.) to re-fetch immediately.
  dataRefreshKey: number;
  bumpDataRefreshKey: () => void;
  clearAlerts: () => void;

  /** Reset all transient state when switching profiles (App.tsx onProfileSwitched). */
  reset: () => void;
}

export interface AlertExplanation {
  signal: string;
  contribution: number;  // 0-100%
  label: string;
}

export interface Alert {
  id: string;
  severity: 'low' | 'medium' | 'high' | 'critical';
  signal: string;
  description: string;
  affectedUsers: string[];
  userId: string;
  username: string;
  confidence: number;
  timestamp: number;
  dismissed: boolean;
  explanation: AlertExplanation[];
  source: 'live' | 'history';
  flagCount?: number;      // total times in DB (history)
  sessionFlagCount: number; // times flagged this session (live)
}

export interface SelectedUser {
  userId: string;
  username: string;
  color?: string;
}

export type ChannelEventType = 'subscription_new' | 'subscription_resub' | 'subscription_gift';

export interface ChannelEvent {
  id: string;
  type: ChannelEventType;
  username: string;
  timestamp: number;
  // sub / resub
  tier?: string;
  isGift?: boolean;
  cumulativeMonths?: number;
  months?: number;
  message?: string;
  // gift sub
  count?: number;
  cumulativeTotal?: number | null;
  anonymous?: boolean;
}

export interface AutomodHeldMessage {
  messageId: string;
  userId: string;
  username: string;
  content: string;
  category: string;
  level: number;
  heldAt: number;
}

export interface WatchedUser {
  user_id: string;
  username: string;
  added_at: number;
  note: string;
  priority: 'normal' | 'high';
}

export interface ModerationAction {
  actionId: string;
  dbId: number | null;
  actionType: 'ban' | 'timeout' | 'delete' | 'slow_mode' | 'followers_only';
  username: string;
  userId: string;
  channel: string;
  durationSeconds: number | null;
  reason: string;
  triggeredBy: string;
  confidence: number | null;
  status: 'pending' | 'completed' | 'failed' | 'undone';
  dryRun: boolean;
  timestamp: number;
}

const MAX_MESSAGES = 2000;

// -------------------------------------------------------------------------
// Store
// -------------------------------------------------------------------------

export const useChatStore = create<Store>((set) => ({
  // Backend
  backendConfig: null,
  backendConnected: false,
  setBackendConfig: (config) => set({ backendConfig: config, backendConnected: true }),
  setBackendConnected: (v) => set({ backendConnected: v }),

  // Twitch
  twitchConnected: false,
  channel: null,
  configuredChannel: null,
  setTwitchConnected: (v, channel) => set({ twitchConnected: v, channel: channel ?? null }),
  setConfiguredChannel: (ch) => set({ configuredChannel: ch }),

  // WebSocket
  wsState: 'disconnected',
  setWsState: (s) => set({ wsState: s }),

  // Messages
  messages: [],
  addMessage: (msg) =>
    set((state) => ({
      messages:
        state.messages.length >= MAX_MESSAGES
          ? [...state.messages.slice(-MAX_MESSAGES + 1), msg]
          : [...state.messages, msg],
    })),
  clearMessages: () => set({ messages: [] }),

  // Health
  health: null,
  setHealth: (h) => set({ health: h }),

  // Response state
  responseState: { dryRunMode: true, detectionSuppressed: false, suppressionReason: null },
  setResponseState: (s) => set({ responseState: s }),

  // Alerts
  alerts: [],
  addAlert: (a) =>
    set((state) => {
      // If a live alert for this user already exists (not dismissed), update it
      // in place rather than stacking duplicate cards.
      const existingIdx = a.userId
        ? state.alerts.findIndex((x) => x.userId === a.userId && x.source === 'live' && !x.dismissed)
        : -1;
      if (existingIdx !== -1) {
        const existing = state.alerts[existingIdx];
        const updated = {
          ...a,
          id: existing.id, // keep original id so the card doesn't remount
          sessionFlagCount: existing.sessionFlagCount + 1,
        };
        const next = [...state.alerts];
        next[existingIdx] = updated;
        return { alerts: next };
      }
      return { alerts: [{ ...a, sessionFlagCount: 1 }, ...state.alerts].slice(0, 50) };
    }),
  dismissAlert: (id) =>
    set((state) => ({
      alerts: state.alerts.map((a) => (a.id === id ? { ...a, dismissed: true } : a)),
    })),
  updateAlert: (userId, patch) =>
    set((state) => ({
      alerts: state.alerts.map((a) => (a.userId === userId && a.source === 'live' ? { ...a, ...patch } : a)),
    })),

  // Moderation actions
  moderationActions: [],
  addModerationAction: (a) =>
    set((state) => ({
      moderationActions: [a, ...state.moderationActions].slice(0, 100),
    })),
  undoModerationAction: (actionId) =>
    set((state) => ({
      moderationActions: state.moderationActions.map((a) =>
        a.actionId === actionId ? { ...a, status: 'undone' } : a
      ),
    })),

  // Channel events (subs / resubs / gift subs — capped at 50)
  channelEvents: [],
  addChannelEvent: (e) =>
    set((state) => ({
      channelEvents: [e, ...state.channelEvents].slice(0, 50),
    })),

  // Selected user
  selectedUser: null,
  setSelectedUser: (u) => set({ selectedUser: u }),

  // Performance telemetry
  perf: null,
  setPerf: (p) => set({ perf: p }),

  // AutoMod queue
  automodQueue: [],
  addAutomodHeld: (m) =>
    set((state) => ({
      automodQueue: state.automodQueue.some((x) => x.messageId === m.messageId)
        ? state.automodQueue
        : [m, ...state.automodQueue].slice(0, 50),
    })),
  resolveAutomodHeld: (messageId) =>
    set((state) => ({
      automodQueue: state.automodQueue.filter((m) => m.messageId !== messageId),
    })),

  // Active channel filter
  activeChannel: null,
  setActiveChannel: (ch) => set({ activeChannel: ch }),

  // Event log
  eventLog: [],
  addEventLogEntry: (e) =>
    set((state) => ({ eventLog: [e, ...state.eventLog].slice(0, 500) })),
  clearEventLog: () => set({ eventLog: [] }),

  // Data refresh key
  dataRefreshKey: 0,
  bumpDataRefreshKey: () => set((state) => ({ dataRefreshKey: state.dataRefreshKey + 1 })),
  clearAlerts: () => set({ alerts: [] }),

  // Profile switch — reset all transient live state (backend will re-hydrate)
  reset: () => set({
    backendConfig: null,
    backendConnected: false,
    twitchConnected: false,
    channel: null,
    configuredChannel: null,
    wsState: 'disconnected',
    messages: [],
    health: null,
    responseState: { dryRunMode: true, detectionSuppressed: false, suppressionReason: null },
    alerts: [],
    moderationActions: [],
    channelEvents: [],
    selectedUser: null,
    perf: null,
    watchedUsers: [],
    automodQueue: [],
    activeChannel: null,
    eventLog: [],
    dataRefreshKey: 0,
  }),

  // Watchlist
  watchedUsers: [],
  setWatchedUsers: (users) => set({ watchedUsers: users }),
  addWatchedUser: (u) =>
    set((state) => ({
      watchedUsers: state.watchedUsers.some((w) => w.user_id === u.user_id)
        ? state.watchedUsers.map((w) => (w.user_id === u.user_id ? u : w))
        : [u, ...state.watchedUsers],
    })),
  removeWatchedUser: (userId) =>
    set((state) => ({
      watchedUsers: state.watchedUsers.filter((w) => w.user_id !== userId),
    })),
}));
