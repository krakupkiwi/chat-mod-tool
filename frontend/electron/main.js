'use strict';

/**
 * Electron main process.
 *
 * Responsibilities:
 *   - Create the BrowserWindow with locked-down webPreferences
 *   - Apply Content Security Policy
 *   - Manage the Python backend process via PythonManager
 *   - Relay backend config (port + IPC secret) to the renderer
 *   - Handle system tray, notifications, external URLs, power events
 *   - Graceful shutdown: stop Python before quitting
 */

const {
  app,
  BrowserWindow,
  ipcMain,
  session,
  shell,
  Notification,
  Tray,
  Menu,
  nativeImage,
  globalShortcut,
} = require('electron');
const path = require('path');

const { PythonManager } = require('./python-manager');
const log = require('./logger');

// Auto-updater — only active in packaged builds (not during development)
let autoUpdater = null;
if (!process.env.ELECTRON_IS_DEV) {
  try {
    ({ autoUpdater } = require('electron-updater'));
    autoUpdater.logger = log;
    autoUpdater.autoDownload = true;
    autoUpdater.autoInstallOnAppQuit = true;
  } catch (err) {
    log.warn(`electron-updater unavailable: ${err.message}`);
  }
}

const isDev = !app.isPackaged;
const pythonManager = new PythonManager();

let mainWindow = null;
let tray = null;

// Health state tracked in main for tray + notifications
let lastHealthLevel = 'healthy';
let lastHealthScore = 100;
let detectionMuted = false;

// Levels that warrant a native notification (only on upward transition)
const ALERT_LEVELS = new Set(['likely_attack', 'critical']);
const LEVEL_LABELS = {
  healthy: 'Healthy',
  elevated: 'Elevated',
  suspicious: 'Suspicious',
  likely_attack: 'Likely Attack',
  critical: 'CRITICAL',
};

// -------------------------------------------------------------------------
// Content Security Policy
// -------------------------------------------------------------------------

function applyCSP() {
  // Dev CSP: allows Vite HMR (inline scripts, eval for React Fast Refresh, localhost WS).
  // Prod CSP: strict — no eval, no inline scripts.
  const devPolicy = [
    "default-src 'self'",
    // Vite HMR injects inline <script> tags; React Fast Refresh requires eval.
    "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
    // Vite HMR SharedWorker uses blob: URLs — worker-src must explicitly allow it.
    "worker-src blob:",
    // Vite dev server + backend WebSocket/HTTP on localhost
    "connect-src 'self' ws://localhost:* ws://127.0.0.1:* http://localhost:* http://127.0.0.1:*",
    "img-src 'self' data: https://static-cdn.jtvnw.net",
    "style-src 'self' 'unsafe-inline'",
    "font-src 'self'",
  ].join('; ');

  const prodPolicy = [
    "default-src 'self'",
    "script-src 'self'",
    "connect-src 'self' ws://127.0.0.1:* http://127.0.0.1:*",
    "img-src 'self' data: https://static-cdn.jtvnw.net",
    "style-src 'self' 'unsafe-inline'",
    "font-src 'self'",
  ].join('; ');

  session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
    callback({
      responseHeaders: {
        ...details.responseHeaders,
        'Content-Security-Policy': [isDev ? devPolicy : prodPolicy],
      },
    });
  });
}

// -------------------------------------------------------------------------
// Window creation
// -------------------------------------------------------------------------

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 900,
    minHeight: 600,
    title: 'TwitchIDS',
    backgroundColor: '#0f1117',
    show: false, // Show only after content loads (avoids flash)
    webPreferences: {
      nodeIntegration: false,           // NEVER true
      contextIsolation: true,           // REQUIRED
      sandbox: true,                    // REQUIRED
      webSecurity: true,                // NEVER false
      allowRunningInsecureContent: false,
      preload: path.join(__dirname, 'preload.js'),
    },
  });

  // Load app
  if (isDev) {
    mainWindow.loadURL('http://localhost:5173');
    mainWindow.webContents.openDevTools();
  } else {
    mainWindow.loadFile(path.join(__dirname, '..', 'dist', 'index.html'));
  }

  mainWindow.once('ready-to-show', () => {
    mainWindow.show();
  });

  // Minimize to tray instead of closing
  mainWindow.on('close', (event) => {
    if (!app.isQuitting) {
      event.preventDefault();
      mainWindow.hide();
    }
  });
}

// -------------------------------------------------------------------------
// Tray icon helpers
// -------------------------------------------------------------------------

// Resolve path to bundled tray assets — works both in dev and packaged builds.
// In packaged builds, __dirname is inside the asar; extraResources puts assets
// alongside it, so we check process.resourcesPath first.
function _trayIconPath(level) {
  const file = `tray-${level}.png`;
  if (app.isPackaged) {
    return path.join(process.resourcesPath, 'assets', 'tray', file);
  }
  return path.join(__dirname, '..', 'assets', 'tray', file);
}

/** Load a static PNG tray icon for the given health level. */
function createLevelIcon(level) {
  try {
    const iconPath = _trayIconPath(level);
    const img = nativeImage.createFromPath(iconPath);
    if (!img.isEmpty()) return img;
  } catch (_) {
    // fall through to fallback
  }
  // Fallback: generate a solid-color square if asset is missing
  const FALLBACK = { healthy: [74,222,128,255], elevated: [250,204,21,255],
    suspicious: [251,146,60,255], likely_attack: [248,113,113,255], critical: [239,68,68,255] };
  const [r, g, b, a] = FALLBACK[level] ?? FALLBACK.healthy;
  const SIZE = 16;
  const buf = Buffer.alloc(SIZE * SIZE * 4);
  for (let i = 0; i < SIZE * SIZE; i++) {
    buf[i * 4] = r; buf[i * 4 + 1] = g; buf[i * 4 + 2] = b; buf[i * 4 + 3] = a;
  }
  return nativeImage.createFromBuffer(buf, { width: SIZE, height: SIZE });
}

function buildTrayMenu() {
  return Menu.buildFromTemplate([
    {
      label: 'Show TwitchIDS',
      click: () => { mainWindow?.show(); mainWindow?.focus(); },
    },
    { type: 'separator' },
    {
      label: detectionMuted ? 'Unmute Detection  Ctrl+M' : 'Mute Detection  Ctrl+M',
      click: () => toggleDetectionMute(),
    },
    { type: 'separator' },
    {
      label: 'Quit',
      click: () => {
        app.isQuitting = true;
        app.quit();
      },
    },
  ]);
}

function updateTray(score, level) {
  if (!tray) return;
  lastHealthScore = score;
  lastHealthLevel = level;

  const icon = createLevelIcon(level);
  tray.setImage(icon);

  const levelLabel = LEVEL_LABELS[level] ?? level;
  const muteNote = detectionMuted ? ' [MUTED]' : '';
  tray.setToolTip(`TwitchIDS — Health ${Math.round(score)} (${levelLabel})${muteNote}`);
  tray.setContextMenu(buildTrayMenu());
}

// -------------------------------------------------------------------------
// Notifications
// -------------------------------------------------------------------------

function notifyLevelChange(level) {
  if (!Notification.isSupported()) return;
  if (!ALERT_LEVELS.has(level)) return;

  const levelLabel = LEVEL_LABELS[level] ?? level;
  new Notification({
    title: `TwitchIDS — ${levelLabel}`,
    body: `Chat health: ${Math.round(lastHealthScore)} — possible bot activity detected`,
    urgency: level === 'critical' ? 'critical' : 'normal',
  }).show();
}

// -------------------------------------------------------------------------
// System tray
// -------------------------------------------------------------------------

function createTray() {
  try {
    const icon = createLevelIcon('healthy');
    tray = new Tray(icon);
    tray.setToolTip('TwitchIDS — Chat Monitor');
    tray.setContextMenu(buildTrayMenu());
    tray.on('double-click', () => { mainWindow?.show(); mainWindow?.focus(); });
  } catch (err) {
    // Tray is non-critical — skip if it fails
    log.warn(`System tray unavailable: ${err.message}`);
  }
}

// -------------------------------------------------------------------------
// Mute detection (Ctrl+M)
// -------------------------------------------------------------------------

async function toggleDetectionMute() {
  const { port, ipcSecret } = pythonManager.getConfig();
  if (!port || !ipcSecret) return;

  try {
    // Read current dry_run state, then toggle it
    const res = await fetch(`http://127.0.0.1:${port}/api/config`, {
      headers: { 'X-IPC-Secret': ipcSecret },
    });
    if (!res.ok) return;
    const cfg = await res.json();
    const newMuted = !cfg.dry_run;

    await fetch(`http://127.0.0.1:${port}/api/config`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json', 'X-IPC-Secret': ipcSecret },
      body: JSON.stringify({ dry_run: newMuted }),
    });

    detectionMuted = newMuted;
    log.info(`Detection ${newMuted ? 'muted (dry-run ON)' : 'unmuted (dry-run OFF)'} via Ctrl+M`);

    // Notify renderer so the footer updates immediately
    mainWindow?.webContents.send('detection-mute-changed', { muted: newMuted });

    // Rebuild tray menu to show updated label
    if (tray) tray.setContextMenu(buildTrayMenu());
  } catch (err) {
    log.warn(`toggleDetectionMute failed: ${err.message}`);
  }
}

// -------------------------------------------------------------------------
// Global shortcuts
// -------------------------------------------------------------------------

function registerShortcuts() {
  // Ctrl+M — toggle detection mute (dry-run)
  globalShortcut.register('CommandOrControl+M', () => {
    toggleDetectionMute();
  });

  // Ctrl+D — focus / show dashboard window
  globalShortcut.register('CommandOrControl+D', () => {
    if (mainWindow) {
      mainWindow.show();
      mainWindow.focus();
    }
  });
}

// -------------------------------------------------------------------------
// Python backend startup
// -------------------------------------------------------------------------

async function startBackend() {
  pythonManager.on('ready', ({ port, ipcSecret }) => {
    log.info(`Backend ready — port=${port}`);
    mainWindow?.webContents.send('backend-ready', { port, ipcSecret });
  });

  pythonManager.on('disconnected', () => {
    log.warn('Backend disconnected');
    mainWindow?.webContents.send('backend-disconnected');
  });

  // When Python crashes and restarts, send new config to renderer
  pythonManager.on('ready', ({ port, ipcSecret }) => {
    mainWindow?.webContents.send('backend-reconnected', { port, ipcSecret });
  });

  pythonManager.on('backend-error', (msg) => {
    log.error('Backend error:', msg);
  });

  try {
    await pythonManager.start();
  } catch (err) {
    log.error('Failed to start Python backend:', err.message);
    // Show error but don't crash — renderer will show connection error state
  }
}

// -------------------------------------------------------------------------
// IPC handlers (renderer → main)
// -------------------------------------------------------------------------

function registerIPCHandlers() {
  ipcMain.handle('get-version', () => app.getVersion());

  // Renderer calls this on mount to get backend config if it missed the push event
  ipcMain.handle('get-backend-config', () => {
    const config = pythonManager.getConfig();
    return config.ipcSecret ? config : null;
  });

  ipcMain.on('show-notification', (_event, { title, body }) => {
    if (Notification.isSupported()) {
      new Notification({ title, body }).show();
    }
  });

  ipcMain.on('open-external', (_event, url) => {
    // Only allow https:// URLs to be opened externally
    if (typeof url === 'string' && url.startsWith('https://')) {
      shell.openExternal(url);
    }
  });

  ipcMain.on('show-in-folder', (_event, filePath) => {
    if (typeof filePath === 'string') {
      shell.showItemInFolder(filePath);
    }
  });

  // Auto-updater controls
  ipcMain.handle('check-for-updates', () => {
    if (autoUpdater) autoUpdater.checkForUpdates();
  });

  ipcMain.on('install-update', () => {
    if (autoUpdater) autoUpdater.quitAndInstall(false, true);
  });

  // Health update from renderer → update tray + fire level-transition notifications
  ipcMain.on('tray-update', (_event, { score, level }) => {
    const prevLevel = lastHealthLevel;
    updateTray(score, level);

    // Notify only on upward transition into an alert level (not on every tick)
    if (level !== prevLevel && ALERT_LEVELS.has(level)) {
      notifyLevelChange(level);
    }
  });
}

// -------------------------------------------------------------------------
// App lifecycle
// -------------------------------------------------------------------------

app.whenReady().then(async () => {
  try {
    applyCSP();
    createWindow();
    createTray();
    registerIPCHandlers();
    registerShortcuts();
    await startBackend();

    // Check for updates ~5s after startup so the window is fully loaded
    if (autoUpdater && app.isPackaged) {
      setTimeout(() => {
        autoUpdater.checkForUpdatesAndNotify().catch((err) => {
          log.warn(`Update check failed: ${err.message}`);
        });
      }, 5_000);

      autoUpdater.on('update-available', (info) => {
        log.info(`Update available: ${info.version}`);
        mainWindow?.webContents.send('update-available', { version: info.version });
      });

      autoUpdater.on('update-downloaded', (info) => {
        log.info(`Update downloaded: ${info.version}`);
        mainWindow?.webContents.send('update-downloaded', { version: info.version });
      });

      autoUpdater.on('error', (err) => {
        log.warn(`Auto-updater error: ${err.message}`);
      });
    }
  } catch (err) {
    log.error(`Fatal startup error: ${err.message}\n${err.stack}`);
  }
});

app.on('window-all-closed', () => {
  // On macOS, keep app running until explicit quit
  if (process.platform !== 'darwin') {
    // Don't quit — just minimize to tray
  }
});

app.on('activate', () => {
  mainWindow?.show();
});

app.on('will-quit', () => {
  globalShortcut.unregisterAll();
});

app.on('before-quit', async () => {
  app.isQuitting = true;
  log.info('App quitting — stopping Python backend');
  await pythonManager.stop();
});

// -------------------------------------------------------------------------
// Dev mode: spawn Python separately
// -------------------------------------------------------------------------

// In dev mode, set TWITCHIDS_DEV=true and run:
//   Terminal 1: cd backend && python main.py --port 7842 --dev
//   Terminal 2: cd frontend && npm run dev:electron
//
// PythonManager will still try to spawn Python, so for pure frontend dev
// you can comment out `await startBackend()` above and manually set
// the backend config in the renderer.
