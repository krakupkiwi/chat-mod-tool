'use strict';

/**
 * PythonManager — manages the Python backend child process.
 *
 * Responsibilities:
 *   - Find a free port (preferred: 7842, falls back to OS-assigned)
 *   - Resolve the correct Python executable path (dev vs packaged)
 *   - Spawn the Python process with --port and --parent-pid args
 *   - Parse stdout JSON protocol (Channel 1 IPC)
 *   - Emit 'ready' event with {port, ipcSecret} when backend is live
 *   - Restart on crash with exponential backoff (max 5 attempts)
 *   - Send graceful shutdown signal on app quit
 */

const { EventEmitter } = require('events');
const { spawn } = require('child_process');
const net = require('net');
const path = require('path');
const fs = require('fs');
const { app, dialog } = require('electron');

const log = require('./logger');

const PREFERRED_PORT = 7842;
const STARTUP_TIMEOUT_MS = 25_000;
const MAX_RESTARTS = 5;
const BACKOFF_BASE_MS = 1_000;


class PythonManager extends EventEmitter {
  /**
   * @param {{profileDir?: string|null, profileId?: string|null}} opts
   */
  constructor({ profileDir = null, profileId = null } = {}) {
    super();
    this.profileDir = profileDir;
    this.profileId = profileId;
    this.process = null;
    this.port = null;
    this.ipcSecret = null;
    this.ready = false;
    this.restartCount = 0;
    this._startupTimer = null;
    this._shuttingDown = false;
  }

  // -------------------------------------------------------------------------
  // Public API
  // -------------------------------------------------------------------------

  async start() {
    this.port = await findFreePort(PREFERRED_PORT);
    const { exe, scriptArgs, cwd } = this._resolvePythonPaths();

    log.info(`Starting Python backend: ${exe} ${[...scriptArgs].join(' ')} --port ${this.port}`);

    const profileArgs = [];
    if (this.profileDir) profileArgs.push('--profile-dir', this.profileDir);
    if (this.profileId)  profileArgs.push('--profile-id', this.profileId);

    this.process = spawn(
      exe,
      [...scriptArgs, '--port', String(this.port), '--parent-pid', String(process.pid), ...profileArgs],
      {
        stdio: ['pipe', 'pipe', 'pipe'],
        windowsHide: true,
        cwd,
      }
    );

    this.process.stdout.setEncoding('utf8');
    this.process.stderr.setEncoding('utf8');

    this.process.stdout.on('data', (data) => {
      data.split('\n').filter(Boolean).forEach((line) => this._handleStdoutLine(line));
    });

    this.process.stderr.on('data', (data) => {
      // Python stderr lines (tracebacks etc.) — log but don't crash
      data.split('\n').filter(Boolean).forEach((line) => log.error(`[python stderr] ${line}`));
    });

    this.process.on('exit', (code, signal) => {
      if (!this._shuttingDown) {
        log.warn(`Python exited unexpectedly — code=${code} signal=${signal}`);
        this._handleCrash(code, signal);
      }
    });

    this.process.on('error', (err) => {
      log.error(`Failed to spawn Python process: ${err.message}`);
      this.emit('error', err);
    });

    // Wait for ready signal with timeout
    await this._waitForReady();
    this.restartCount = 0; // Reset on successful start
  }

  async stop() {
    this._shuttingDown = true;
    if (!this.process || this.process.killed) return;

    log.info('Sending graceful shutdown to Python backend');
    try {
      this.process.stdin.write(JSON.stringify({ type: 'shutdown' }) + '\n');
    } catch (_) {}

    // Give Python up to 5 seconds to flush and exit
    await new Promise((resolve) => {
      const timer = setTimeout(() => {
        log.warn('Python did not exit cleanly — killing');
        this.process.kill('SIGTERM');
        resolve();
      }, 5_000);

      this.process.once('exit', () => {
        clearTimeout(timer);
        resolve();
      });
    });
  }

  /**
   * Stop the current process (if running) and start a new one with new profile args.
   * @param {string} profileDir
   * @param {string} profileId
   */
  async restart(profileDir, profileId) {
    this.profileDir = profileDir;
    this.profileId = profileId;
    this.restartCount = 0;
    this.ready = false;
    if (this.process && !this.process.killed) {
      await this.stop();
    }
    this._shuttingDown = false;
    await this.start();
  }

  getConfig() {
    return { port: this.port, ipcSecret: this.ipcSecret };
  }

  // -------------------------------------------------------------------------
  // Private
  // -------------------------------------------------------------------------

  _handleStdoutLine(line) {
    let msg;
    try {
      msg = JSON.parse(line);
    } catch (_) {
      log.debug(`[python stdout non-json] ${line}`);
      return;
    }

    switch (msg.type) {
      case 'ready':
        this.ipcSecret = msg.ipc_secret;
        this.ready = true;
        clearTimeout(this._startupTimer);
        log.info(`Python backend ready on port ${msg.port}`);
        this.emit('ready', { port: msg.port, ipcSecret: msg.ipc_secret });
        break;

      case 'health':
        // Periodic heartbeat — could display in tray tooltip
        this.emit('health', msg);
        break;

      case 'error':
        log.error(`[python error] ${msg.message} (${msg.code})`);
        this.emit('backend-error', msg);
        break;

      case 'shutdown':
        log.info(`Python shutdown: ${msg.reason}`);
        break;

      default:
        log.debug(`[python stdout] ${JSON.stringify(msg)}`);
    }
  }

  _waitForReady() {
    return new Promise((resolve, reject) => {
      if (this.ready) return resolve();

      this._startupTimer = setTimeout(() => {
        reject(new Error(`Python backend failed to start within ${STARTUP_TIMEOUT_MS}ms`));
      }, STARTUP_TIMEOUT_MS);

      this.once('ready', () => {
        clearTimeout(this._startupTimer);
        resolve();
      });
    });
  }

  async _handleCrash(code, signal) {
    this.ready = false;
    this.ipcSecret = null;
    this.emit('disconnected');

    if (this.restartCount >= MAX_RESTARTS) {
      log.error('Python backend crashed too many times — giving up');
      dialog.showErrorBox(
        'TwitchIDS — Backend Error',
        'The detection engine crashed repeatedly and could not be restarted.\n\nPlease restart the application. If the problem persists, check the log file.'
      );
      return;
    }

    const delay = BACKOFF_BASE_MS * Math.pow(2, this.restartCount);
    this.restartCount += 1;
    log.info(`Restarting Python in ${delay}ms (attempt ${this.restartCount}/${MAX_RESTARTS})`);

    await sleep(delay);
    await this.start();
  }

  _resolvePythonPaths() {
    const backendDir = path.join(__dirname, '..', '..', 'backend');

    if (app.isPackaged) {
      // Packaged: Python EXE is self-contained — no script arg needed
      return {
        exe: path.join(process.resourcesPath, 'python-backend', 'twitchids-backend.exe'),
        scriptArgs: [],
        cwd: process.resourcesPath,
      };
    }

    // Development: find the venv Python, then pass main.py as script arg
    const script = path.join(backendDir, 'main.py');
    const devExePaths = [
      path.join(backendDir, '.venv', 'Scripts', 'python.exe'),
      path.join(backendDir, 'venv', 'Scripts', 'python.exe'),
      'python',
    ];

    for (const exe of devExePaths) {
      if (exe === 'python' || fs.existsSync(exe)) {
        return { exe, scriptArgs: [script], cwd: backendDir };
      }
    }

    return { exe: 'python', scriptArgs: [script], cwd: backendDir };
  }
}

// -------------------------------------------------------------------------
// Helpers
// -------------------------------------------------------------------------

function findFreePort(preferred) {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.listen(preferred, '127.0.0.1', () => {
      const { port } = server.address();
      server.close(() => resolve(port));
    });
    server.on('error', () => {
      // Preferred port is in use — get an OS-assigned free port
      const fallback = net.createServer();
      fallback.listen(0, '127.0.0.1', () => {
        const { port } = fallback.address();
        fallback.close(() => resolve(port));
      });
    });
  });
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

module.exports = { PythonManager };
