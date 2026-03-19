'use strict';

/**
 * Preload script — runs in the renderer process with Node.js access,
 * but in an isolated context (contextIsolation: true).
 *
 * ONLY expose what the renderer legitimately needs via contextBridge.
 * Never expose:
 *   - arbitrary ipcRenderer.on/send
 *   - require()
 *   - fs, path, or any Node built-in
 *   - the full ipcRenderer object
 */

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  /**
   * Called once when the Python backend is ready.
   * Provides the port and IPC secret for WebSocket + REST connections.
   */
  onBackendReady: (callback) => {
    ipcRenderer.on('backend-ready', (_event, data) => callback(data));
  },

  /**
   * Called when the backend disconnects (Python crash, being restarted).
   */
  onBackendDisconnected: (callback) => {
    ipcRenderer.on('backend-disconnected', (_event) => callback());
  },

  /**
   * Called when the backend reconnects after a crash.
   */
  onBackendReconnected: (callback) => {
    ipcRenderer.on('backend-reconnected', (_event, data) => callback(data));
  },

  /**
   * Show a native Windows notification.
   */
  showNotification: (title, body) => {
    ipcRenderer.send('show-notification', { title, body });
  },

  /**
   * Open a URL in the default system browser (not in the Electron window).
   */
  openExternal: (url) => {
    ipcRenderer.send('open-external', url);
  },

  /**
   * Get the application version string.
   */
  getVersion: () => ipcRenderer.invoke('get-version'),

  /**
   * Request the current backend config (port + secret) from main.
   * Use this on mount to recover if backend-ready fired before the listener was set up.
   */
  getBackendConfig: () => ipcRenderer.invoke('get-backend-config'),

  /**
   * Send health score + level to main for tray icon + notification updates.
   * Called on every health_update WebSocket event.
   */
  sendTrayUpdate: (score, level) => {
    ipcRenderer.send('tray-update', { score, level });
  },

  /**
   * Called when main toggles detection mute via Ctrl+M shortcut.
   * Receives { muted: boolean } — renderer should refresh its config display.
   */
  onDetectionMuteChanged: (callback) => {
    ipcRenderer.on('detection-mute-changed', (_event, data) => callback(data));
  },

  /**
   * Open a file's containing folder in Windows Explorer and select the file.
   */
  showInFolder: (filePath) => {
    ipcRenderer.send('show-in-folder', filePath);
  },

  /**
   * Called when a new update is available (download starting automatically).
   */
  onUpdateAvailable: (callback) => {
    ipcRenderer.on('update-available', (_event, data) => callback(data));
  },

  /**
   * Called when an update has been downloaded and is ready to install.
   */
  onUpdateDownloaded: (callback) => {
    ipcRenderer.on('update-downloaded', (_event, data) => callback(data));
  },

  /**
   * Quit and install the downloaded update immediately.
   */
  installUpdate: () => {
    ipcRenderer.send('install-update');
  },
});
