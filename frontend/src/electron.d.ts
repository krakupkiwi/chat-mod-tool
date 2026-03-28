/**
 * Type declarations for window.electronAPI (exposed via contextBridge in preload.js).
 * Only available when running inside Electron — undefined in browser dev mode.
 */

/** Metadata for an app profile (stored in index.json). */
interface ProfileMeta {
  id: string;
  name: string;
  created_at: number;    // Unix timestamp (seconds)
  last_used: number | null;
  encrypted: boolean;
}

/** Result of profile-select IPC. */
interface ProfileSelectResult {
  success: boolean;
  error?: 'incorrect_password' | 'backend_not_running' | string;
}

/** Result of profile export/import IPC. */
interface ProfileOpResult {
  success: boolean;
  error?: string;
}

interface ElectronAPI {
  onBackendReady: (callback: (data: { port: number; ipcSecret: string }) => void) => void;
  onBackendDisconnected: (callback: () => void) => void;
  onBackendReconnected: (callback: (data: { port: number; ipcSecret: string }) => void) => void;
  showNotification: (title: string, body: string) => void;
  openExternal: (url: string) => void;
  getVersion: () => Promise<string>;
  getBackendConfig: () => Promise<{ port: number; ipcSecret: string } | null>;
  sendTrayUpdate: (score: number, level: string) => void;
  onDetectionMuteChanged: (callback: (data: { muted: boolean }) => void) => void;
  onUpdateAvailable: (callback: (data: { version: string }) => void) => void;
  onUpdateDownloaded: (callback: (data: { version: string }) => void) => void;
  installUpdate: () => void;

  // ── Profile management ─────────────────────────────────────────────────────
  profiles: {
    list: () => Promise<ProfileMeta[]>;
    create: (name: string, opts?: { encrypted?: boolean; password?: string }) => Promise<{ id: string; profileDir: string }>;
    rename: (id: string, newName: string) => Promise<void>;
    delete: (id: string) => Promise<void>;
    select: (profileId: string, password?: string) => Promise<ProfileSelectResult>;
    export: (destPath: string, exportPassword?: string) => Promise<ProfileOpResult>;
    import: (srcPath: string, importPassword?: string, newName?: string) => Promise<{ id: string; profileDir: string }>;
  };

  /** Fired after a profile switch — use to reset Zustand store before new backend-ready arrives. */
  onProfileSwitched: (callback: (data: { profileId: string }) => void) => void;

  // ── Native file dialogs ────────────────────────────────────────────────────
  showSaveDialog: (opts: {
    title?: string;
    defaultPath?: string;
    filters?: Array<{ name: string; extensions: string[] }>;
  }) => Promise<{ canceled: boolean; filePath?: string }>;
  showOpenDialog: (opts: {
    title?: string;
    defaultPath?: string;
    filters?: Array<{ name: string; extensions: string[] }>;
    properties?: string[];
  }) => Promise<{ canceled: boolean; filePaths: string[] }>;
}

interface Window {
  electronAPI?: ElectronAPI;
}
