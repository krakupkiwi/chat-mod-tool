/**
 * useThreatPrefs — localStorage-backed settings for the Threat Panel.
 * Shared between ThreatPanel (reads) and SettingsDrawer (reads + writes).
 */

import { useState, useCallback } from 'react';

export type ThreatSortBy = 'score' | 'age' | 'flagCount';
export type ThreatSortDir = 'desc' | 'asc';

export interface ThreatPrefs {
  showLive: boolean;
  showHistory: boolean;
  maxAgeDays: number;       // 3 | 7 | 14 | 30 | 0 (0 = no limit)
  sortBy: ThreatSortBy;
  sortDir: ThreatSortDir;
}

const KEY = 'threatPanelPrefs';

const DEFAULTS: ThreatPrefs = {
  showLive: true,
  showHistory: true,
  maxAgeDays: 14,
  sortBy: 'score',
  sortDir: 'desc',
};

function load(): ThreatPrefs {
  try {
    const raw = localStorage.getItem(KEY);
    if (raw) return { ...DEFAULTS, ...JSON.parse(raw) };
  } catch { /* ignore */ }
  return { ...DEFAULTS };
}

function save(prefs: ThreatPrefs) {
  try { localStorage.setItem(KEY, JSON.stringify(prefs)); } catch { /* ignore */ }
}

export function useThreatPrefs() {
  const [prefs, setPrefs] = useState<ThreatPrefs>(load);

  const update = useCallback(<K extends keyof ThreatPrefs>(key: K, value: ThreatPrefs[K]) => {
    setPrefs((prev) => {
      const next = { ...prev, [key]: value };
      save(next);
      return next;
    });
  }, []);

  return { prefs, update };
}
