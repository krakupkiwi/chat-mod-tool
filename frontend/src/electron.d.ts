/**
 * Type declarations for window.electronAPI (exposed via contextBridge in preload.js).
 * Only available when running inside Electron — undefined in browser dev mode.
 */

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
}

interface Window {
  electronAPI?: ElectronAPI;
}
