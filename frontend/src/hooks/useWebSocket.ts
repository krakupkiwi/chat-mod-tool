/**
 * useWebSocket — connects to the Python FastAPI WebSocket endpoint.
 *
 * Features:
 *   - Auto-reconnect with exponential backoff (up to 30s)
 *   - Routes incoming events to the Zustand store
 *   - Cleans up on unmount
 *   - Exposes connection state
 */

import { useEffect, useRef, useCallback } from 'react';
import { useChatStore } from '../store/chatStore';
import type { ChatMessage, ChatFragment, HealthSnapshot, Alert, ModerationAction, PerfSnapshot, ChannelEvent, AutomodHeldMessage } from '../store/chatStore';

const RECONNECT_DELAYS = [1000, 2000, 5000, 10000, 30000];

export function useWebSocket(port: number | null, ipcSecret: string | null) {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectAttempt = useRef(0);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const unmounted = useRef(false);

  const { setWsState, addMessage, clearMessages, setHealth, setResponseState, addAlert, setTwitchConnected, addModerationAction, setPerf, addChannelEvent, addAutomodHeld, addEventLogEntry } = useChatStore();
  const currentChannelRef = useRef<string | null>(null);
  const prevLevelRef = useRef<string>('healthy');
  const seenClusterIds = useRef<Set<string>>(new Set());

  const connect = useCallback(() => {
    if (!port || !ipcSecret || unmounted.current) return;

    setWsState('connecting');

    const url = `ws://127.0.0.1:${port}/ws?secret=${encodeURIComponent(ipcSecret)}`;
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      reconnectAttempt.current = 0;
      setWsState('connected');
    };

    ws.onclose = (event) => {
      wsRef.current = null;
      if (unmounted.current) return;

      setWsState('disconnected');

      // Don't reconnect on auth failure (4003 = forbidden)
      if (event.code === 4003) {
        setWsState('error');
        return;
      }

      scheduleReconnect();
    };

    ws.onerror = () => {
      // onclose will fire after onerror — let that handle reconnect
      setWsState('error');
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        handleEvent(data);
      } catch {
        // Ignore malformed frames
      }
    };
  }, [port, ipcSecret]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleEvent = useCallback((data: Record<string, unknown>) => {
    switch (data.type) {
      case 'chat_messages_batch': {
        const messages = Array.isArray(data.messages) ? data.messages as Record<string, unknown>[] : [];
        for (const m of messages) {
          const rawFragments = Array.isArray(m.fragments) ? m.fragments as ChatFragment[] : [];
          const msg: ChatMessage = {
            id: String(m.message_id ?? Math.random()),
            userId: String(m.user_id ?? ''),
            username: String(m.username ?? ''),
            content: String(m.content ?? ''),
            channel: String(m.channel ?? ''),
            timestamp: Number(m.ts ?? Date.now() / 1000),
            threatScore: Number(m.threat_score ?? 0),
            flags: Array.isArray(m.badges) ? (m.badges as string[]) : [],
            color: m.color ? String(m.color) : undefined,
            fragments: rawFragments,
          };
          addMessage(msg);
        }
        break;
      }

      case 'chat_message': {
        const rawFragments = Array.isArray(data.fragments) ? data.fragments as ChatFragment[] : [];
        const msg: ChatMessage = {
          id: String(data.message_id ?? Math.random()),
          userId: String(data.user_id ?? ''),
          username: String(data.username ?? ''),
          content: String(data.content ?? ''),
          channel: String(data.channel ?? ''),
          timestamp: Number(data.ts ?? Date.now() / 1000),
          threatScore: Number(data.threat_score ?? 0),
          flags: Array.isArray(data.badges) ? (data.badges as string[]) : [],
          color: data.color ? String(data.color) : undefined,
          fragments: rawFragments,
        };
        addMessage(msg);
        break;
      }

      case 'connection_status': {
        const connected = Boolean(data.connected);
        const newChannel = data.channel ? String(data.channel) : undefined;
        // Clear history when switching to a different channel
        if (newChannel && newChannel !== currentChannelRef.current) {
          clearMessages();
          currentChannelRef.current = newChannel;
        }
        setTwitchConnected(connected, newChannel);
        break;
      }

      case 'health_update': {
        const h = data.health as Record<string, unknown> | undefined;
        const activity = data.chat_activity as Record<string, unknown> | undefined;
        const signals = data.signals as Record<string, unknown> | undefined;

        if (h) {
          const clustersData = data.clusters as Record<string, unknown> | undefined;
          const rawClusters = clustersData?.clusters;
          const snapshot: HealthSnapshot = {
            score: Number(h.score ?? 100),
            riskScore: Number(h.risk_score ?? 0),
            level: (h.level as HealthSnapshot['level']) ?? 'healthy',
            levelDuration: Number(h.level_duration_seconds ?? 0),
            trend: (h.trend as HealthSnapshot['trend']) ?? 'stable',
            messagesPerMinute: Number(activity?.messages_per_minute ?? 0),
            activeUsers: Number(activity?.active_users ?? 0),
            duplicateRatio: Number(activity?.duplicate_ratio ?? 0),
            activeSignals: Array.isArray(signals?.active) ? (signals.active as string[]) : [],
            metricScores: (signals && typeof signals === 'object')
              ? Object.fromEntries(
                  Object.entries(signals)
                    .filter(([k]) => k !== 'active')
                    .map(([k, v]) => [k, Number(v)])
                )
              : {},
            clusters: Array.isArray(rawClusters) ? (rawClusters as import('../store/chatStore').ClusterInfo[]) : [],
          };
          setHealth(snapshot);

          // Log health escalations (suspicious or worse)
          const LEVEL_RANK: Record<string, number> = { healthy: 0, elevated: 1, suspicious: 2, likely_attack: 3, critical: 4 };
          const prevRank = LEVEL_RANK[prevLevelRef.current] ?? 0;
          const newRank = LEVEL_RANK[snapshot.level] ?? 0;
          if (newRank > prevRank && newRank >= 2) {
            addEventLogEntry({
              id: String(Math.random()),
              type: 'health_escalation',
              timestamp: Date.now() / 1000,
              fromLevel: prevLevelRef.current,
              toLevel: snapshot.level,
            });
          }
          prevLevelRef.current = snapshot.level;

          // Log newly detected clusters
          for (const cluster of snapshot.clusters) {
            if (!seenClusterIds.current.has(cluster.cluster_id)) {
              seenClusterIds.current.add(cluster.cluster_id);
              addEventLogEntry({
                id: String(Math.random()),
                type: 'cluster',
                timestamp: Date.now() / 1000,
                channel: cluster.channel,
                clusterSize: cluster.size,
                clusterSample: cluster.sample_message,
                clusterId: cluster.cluster_id,
                userIds: cluster.user_ids,
              });
            }
          }

          // Push health level to Electron main for tray icon + notifications
          window.electronAPI?.sendTrayUpdate(snapshot.score, snapshot.level);
        }

        const rs = data.response_state as Record<string, unknown> | undefined;
        if (rs) {
          setResponseState({
            dryRunMode: Boolean(rs.dry_run_mode ?? true),
            detectionSuppressed: Boolean(rs.detection_suppressed ?? false),
            suppressionReason: rs.suppression_reason != null ? String(rs.suppression_reason) : null,
          });
        }

        const perfData = data.perf as Record<string, unknown> | undefined;
        if (perfData) {
          const perf: PerfSnapshot = {
            msg_per_min: Number(perfData.msg_per_min ?? 0),
            tick_p50_ms: perfData.tick_p50_ms != null ? Number(perfData.tick_p50_ms) : null,
            tick_p95_ms: perfData.tick_p95_ms != null ? Number(perfData.tick_p95_ms) : null,
            tick_p99_ms: perfData.tick_p99_ms != null ? Number(perfData.tick_p99_ms) : null,
            queue_depth: Number(perfData.queue_depth ?? 0),
            ws_clients: Number(perfData.ws_clients ?? 0),
            memory_mb: perfData.memory_mb != null ? Number(perfData.memory_mb) : null,
          };
          setPerf(perf);
        }
        break;
      }

      case 'threat_alert': {
        const alert: Alert = {
          id: String(data.alert_id ?? Math.random()),
          severity: mapSeverity(String(data.severity ?? 'low')),
          signal: String(data.signal ?? ''),
          description: String(data.description ?? ''),
          affectedUsers: Array.isArray(data.affected_users) ? (data.affected_users as string[]) : [],
          userId: String(data.user_id ?? ''),
          username: String(data.username ?? (data.affected_users as string[])?.[0] ?? ''),
          confidence: Number(data.confidence ?? 0),
          timestamp: Number(data.timestamp ?? Date.now() / 1000),
          dismissed: false,
          explanation: Array.isArray(data.explanation) ? data.explanation as Alert['explanation'] : [],
          source: 'live',
          sessionFlagCount: 1, // addAlert will increment this if the user is already in the list
        };
        addAlert(alert);
        addEventLogEntry({
          id: `threat_${alert.id}`,
          type: 'threat',
          timestamp: alert.timestamp,
          channel: String(data.channel ?? ''),
          username: alert.username,
          userId: alert.userId,
          severity: alert.severity,
          description: alert.description,
        });
        break;
      }

      case 'moderation_action': {
        const action: ModerationAction = {
          actionId: String(data.action_id ?? Math.random()),
          dbId: data.db_id != null ? Number(data.db_id) : null,
          actionType: (data.action_type as ModerationAction['actionType']) ?? 'timeout',
          username: String(data.username ?? ''),
          userId: String(data.user_id ?? ''),
          channel: String(data.channel ?? ''),
          durationSeconds: data.duration_seconds != null ? Number(data.duration_seconds) : null,
          reason: String(data.reason ?? ''),
          triggeredBy: String(data.triggered_by ?? ''),
          confidence: data.confidence != null ? Number(data.confidence) : null,
          status: (data.status as ModerationAction['status']) ?? 'completed',
          dryRun: Boolean(data.dry_run),
          timestamp: Number(data.timestamp ?? Date.now() / 1000),
        };
        addModerationAction(action);
        break;
      }

      case 'automod_hold': {
        const held: AutomodHeldMessage = {
          messageId: String(data.message_id ?? ''),
          userId: String(data.user_id ?? ''),
          username: String(data.username ?? ''),
          content: String(data.content ?? ''),
          category: String(data.category ?? ''),
          level: Number(data.level ?? 0),
          heldAt: Number(data.ts ?? Date.now() / 1000),
        };
        addAutomodHeld(held);
        break;
      }

      case 'twitch_event': {
        const evType = String(data.twitch_event_type ?? '');
        if (evType === 'subscription_new' || evType === 'subscription_resub' || evType === 'subscription_gift') {
          const ev: ChannelEvent = {
            id: String(Math.random()),
            type: evType as ChannelEvent['type'],
            username: String(data.username ?? 'anonymous'),
            timestamp: Date.now() / 1000,
            tier: data.tier ? String(data.tier) : undefined,
            isGift: data.is_gift != null ? Boolean(data.is_gift) : undefined,
            cumulativeMonths: data.cumulative_months != null ? Number(data.cumulative_months) : undefined,
            months: data.months != null ? Number(data.months) : undefined,
            message: data.message ? String(data.message) : undefined,
            count: data.count != null ? Number(data.count) : undefined,
            cumulativeTotal: data.cumulative_total != null ? Number(data.cumulative_total) : undefined,
            anonymous: data.anonymous != null ? Boolean(data.anonymous) : undefined,
          };
          addChannelEvent(ev);
        }
        break;
      }
    }
  }, [addMessage, clearMessages, setHealth, setResponseState, addAlert, setTwitchConnected, addModerationAction, setPerf, addChannelEvent, addAutomodHeld, addEventLogEntry]);

  const scheduleReconnect = useCallback(() => {
    if (unmounted.current) return;
    const delay = RECONNECT_DELAYS[Math.min(reconnectAttempt.current, RECONNECT_DELAYS.length - 1)];
    reconnectAttempt.current += 1;
    reconnectTimer.current = setTimeout(connect, delay);
  }, [connect]);

  // Connect when port/secret become available
  useEffect(() => {
    if (!port || !ipcSecret) return;
    unmounted.current = false;
    connect();

    return () => {
      unmounted.current = true;
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
      wsRef.current?.close();
    };
  }, [port, ipcSecret, connect]);
}

function mapSeverity(s: string): Alert['severity'] {
  if (s === 'critical') return 'critical';
  if (s === 'high') return 'high';
  if (s === 'medium') return 'medium';
  return 'low';
}
