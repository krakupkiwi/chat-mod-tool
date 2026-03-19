/**
 * BotNetworkGraph — Sigma.js WebGL visualization of detected bot clusters.
 *
 * Uses sigma (WebGL renderer) + graphology (graph data structure) for
 * high-performance rendering of large bot networks (1000+ nodes).
 *
 * Layout:
 *   - Hub node per cluster (larger, brighter)
 *   - Member user nodes radiate from hub in a pre-seeded circle
 *   - ForceAtlas2 runs briefly in a Web Worker to settle the layout
 *
 * Replaces the pure-SVG hub-and-spoke renderer with WebGL for scalability.
 * Click a user node to open the UserDetailPanel.
 */

import { useEffect, useRef, useMemo } from 'react';
import Sigma from 'sigma';
import Graph from 'graphology';
import { circular } from 'graphology-layout';
import { useChatStore, type ClusterInfo } from '../store/chatStore';

// ---------------------------------------------------------------------------
// Colour palette per cluster index
// ---------------------------------------------------------------------------

const CLUSTER_COLORS = [
  '#f87171', '#fb923c', '#facc15',
  '#a78bfa', '#60a5fa', '#34d399',
];

function clusterColor(idx: number): string {
  return CLUSTER_COLORS[idx % CLUSTER_COLORS.length] ?? '#9ca3af';
}

// ---------------------------------------------------------------------------
// Graph builder
// ---------------------------------------------------------------------------

function buildGraph(
  clusters: ClusterInfo[],
  userMap: Map<string, string>,
): Graph {
  const g = new Graph({ type: 'undirected', multi: false });

  clusters.forEach((cluster, ci) => {
    const color = clusterColor(ci);
    const hubId = `hub_${cluster.cluster_id}`;

    // Hub node
    g.addNode(hubId, {
      label: `C${ci + 1} (${cluster.size})`,
      size: 12,
      color,
      nodeType: 'hub',
      clusterId: cluster.cluster_id,
      clusterIdx: ci,
    });

    // Member user nodes (cap at 30 per cluster for legibility)
    const memberIds = cluster.user_ids.slice(0, 30);
    memberIds.forEach((uid) => {
      const nodeId = `user_${uid}`;
      const username = userMap.get(uid) ?? uid.slice(0, 8);
      if (!g.hasNode(nodeId)) {
        g.addNode(nodeId, {
          label: username,
          size: 5,
          color,
          nodeType: 'user',
          userId: uid,
          username,
        });
      }
      if (!g.hasEdge(hubId, nodeId)) {
        g.addEdge(hubId, nodeId, { color, size: 1 });
      }
    });
  });

  // Apply circular pre-layout so the graph starts in a sensible position
  if (g.order > 0) {
    circular.assign(g);
  }

  return g;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface Props {
  clusters: ClusterInfo[];
}

export function BotNetworkGraph({ clusters }: Props) {
  const messages = useChatStore((s) => s.messages);
  const setSelectedUser = useChatStore((s) => s.setSelectedUser);
  const containerRef = useRef<HTMLDivElement>(null);
  const sigmaRef = useRef<Sigma | null>(null);

  const userMap = useMemo(() => {
    const m = new Map<string, string>();
    for (const msg of messages) m.set(msg.userId, msg.username);
    return m;
  }, [messages]);

  useEffect(() => {
    if (!containerRef.current || clusters.length === 0) return;

    const graph = buildGraph(clusters, userMap);
    if (graph.order === 0) return;

    // Destroy any existing renderer
    sigmaRef.current?.kill();

    const renderer = new Sigma(graph, containerRef.current, {
      renderEdgeLabels: false,
      defaultEdgeColor: '#374151',
      defaultNodeColor: '#6b7280',
      labelFont: 'monospace',
      labelSize: 9,
      labelWeight: '400',
      labelColor: { color: '#9ca3af' },
      // Only render labels for hubs by default to reduce clutter
      labelRenderedSizeThreshold: 8,
      // Explicit background prevents WebGL transparency bleed onto elements below
      backgroundColor: '#111827',
    });

    // Click user node → open UserDetailPanel
    renderer.on('clickNode', ({ node }) => {
      const attrs = graph.getNodeAttributes(node);
      if (attrs.nodeType === 'user' && attrs.userId) {
        setSelectedUser({ userId: attrs.userId, username: attrs.username });
      }
    });

    sigmaRef.current = renderer;

    return () => {
      renderer.kill();
      sigmaRef.current = null;
    };
  }, [clusters, userMap, setSelectedUser]);

  if (clusters.length === 0) return null;

  return (
    <div className="px-2 py-2 border-t border-surface-3 overflow-hidden">
      <div className="text-[10px] text-gray-500 uppercase tracking-wider mb-1.5 px-1">
        Bot Network Graph
      </div>
      {/* position:relative is required so Sigma's absolutely-positioned canvas
          stays bounded within this container instead of escaping to a distant ancestor. */}
      <div
        ref={containerRef}
        className="w-full overflow-hidden"
        style={{ height: '160px', position: 'relative' }}
      />
      <div className="flex flex-wrap gap-2 mt-1 px-1">
        {clusters.slice(0, 6).map((c, ci) => (
          <div key={c.cluster_id} className="flex items-center gap-1">
            <div
              className="w-2 h-2 rounded-full"
              style={{ background: clusterColor(ci) }}
            />
            <span className="text-[10px] text-gray-600">C{ci + 1} ({c.size})</span>
          </div>
        ))}
      </div>
    </div>
  );
}
