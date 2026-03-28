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

  // ── Profile management ───────────────────────────────────────────────────

  /**
   * Profile CRUD and selection.
   * All operations run in the Electron main process (no backend required
   * except for export, which delegates to the Python API).
   */
  profiles: {
    list: () =>
      ipcRenderer.invoke('profiles-list'),
    create: (name, opts) =>
      ipcRenderer.invoke('profiles-create', { name, ...opts }),
    rename: (id, newName) =>
      ipcRenderer.invoke('profiles-rename', { id, newName }),
    delete: (id) =>
      ipcRenderer.invoke('profiles-delete', { id }),
    /** Select (and start the backend for) a profile. Main resolves profileDir. */
    select: (profileId, password) =>
      ipcRenderer.invoke('profile-select', { profileId, password }),
    /** Export the active profile to a file (requires backend running). */
    export: (destPath, exportPassword) =>
      ipcRenderer.invoke('profiles-export', { destPath, exportPassword }),
    /** Import a .tidsprofile file (no backend needed). */
    import: (srcPath, importPassword, newName) =>
      ipcRenderer.invoke('profiles-import', { srcPath, importPassword, newName }),
  },

  /**
   * Fired by main after a profile switch completes (backend restarted).
   * The renderer uses this to reset its store before the new backend-ready arrives.
   */
  onProfileSwitched: (callback) => {
    ipcRenderer.on('profile-switched', (_event, data) => callback(data));
  },

  // ── Native file dialogs ──────────────────────────────────────────────────

  showSaveDialog: (opts) => ipcRenderer.invoke('show-save-dialog', opts),
  showOpenDialog: (opts) => ipcRenderer.invoke('show-open-dialog', opts),
});
