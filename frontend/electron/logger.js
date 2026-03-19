'use strict';

/**
 * Simple logger for the Electron main process.
 * Writes timestamped lines to console and to a log file in AppData.
 */

const fs = require('fs');
const path = require('path');
const os = require('os');

const LOG_DIR = path.join(os.homedir(), 'AppData', 'Roaming', 'TwitchIDS');
const LOG_FILE = path.join(LOG_DIR, 'electron.log');

let _fileStream = null;

function _ensureFile() {
  if (_fileStream) return;
  try {
    fs.mkdirSync(LOG_DIR, { recursive: true });
    _fileStream = fs.createWriteStream(LOG_FILE, { flags: 'a' });
  } catch (_) {}
}

function _write(level, ...args) {
  const ts = new Date().toISOString();
  const line = `${ts} ${level.padEnd(5)} ${args.join(' ')}`;
  console.log(line);
  _ensureFile();
  if (_fileStream) {
    _fileStream.write(line + '\n');
  }
}

module.exports = {
  debug: (...a) => _write('DEBUG', ...a),
  info:  (...a) => _write('INFO',  ...a),
  warn:  (...a) => _write('WARN',  ...a),
  error: (...a) => _write('ERROR', ...a),
};
