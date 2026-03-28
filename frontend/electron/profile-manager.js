'use strict';

/**
 * ProfileManager — manages app profiles in %APPDATA%\TwitchIDS\profiles\.
 *
 * Runs entirely in the Electron main process (full Node.js access).
 * Does NOT require the Python backend to be running — all operations are
 * pure filesystem / crypto.
 *
 * Directory layout:
 *   %APPDATA%\TwitchIDS\profiles\
 *     index.json            ← [{id, name, created_at, last_used, encrypted}]
 *     <uuid>\
 *       meta.json           ← {id, name, created_at, encrypted, pwd_hash?}
 *       data.db             ← SQLite database
 *       config.json         ← settings snapshot
 *       .*.enc              ← long-token encrypted files (scoped to profile)
 *
 * Password hashing (local encrypted profiles):
 *   crypto.scryptSync(password, salt, 64) — stored as "scrypt:<salt_hex>:<hash_hex>"
 *   This gates access to the profile; the data on disk is NOT encrypted
 *   (Windows DPAPI via Credential Manager protects OAuth tokens separately).
 *
 * Import/export (encrypted .tidsprofile):
 *   The binary envelope format matches the Python backend's AES-256-GCM scheme
 *   so that files encrypted on one side can be decrypted on the other.
 *
 *   Header (54 bytes):
 *     [4B magic "TIDS"][1B ver=1][1B flags][4B payload_len LE][16B salt][12B nonce][16B tag]
 *   Key derivation: PBKDF2-HMAC-SHA256(password, salt, 480_000, 32)
 */

const crypto = require('crypto');
const fs = require('fs');
const os = require('os');
const path = require('path');
const AdmZip = require('adm-zip');

// ─── Constants ───────────────────────────────────────────────────────────────

const MAGIC = Buffer.from('TIDS');
const FORMAT_VERSION = 1;
const FLAGS_ENCRYPTED = 0x01;
const HEADER_SIZE = 54; // 4+1+1+4+16+12+16
const PBKDF2_ITERATIONS = 480_000;
const PBKDF2_KEYLEN = 32;

// ─── ProfileManager ───────────────────────────────────────────────────────────

class ProfileManager {
  constructor() {
    this._base = path.join(os.homedir(), 'AppData', 'Roaming', 'TwitchIDS');
    this._profilesDir = path.join(this._base, 'profiles');
    this._indexPath = path.join(this._profilesDir, 'index.json');
  }

  // ── Public API ─────────────────────────────────────────────────────────────

  /**
   * Return all profiles from index.json.
   * @returns {Array<{id:string, name:string, created_at:number, last_used:number|null, encrypted:boolean}>}
   */
  listProfiles() {
    this._ensureProfilesDir();
    if (!fs.existsSync(this._indexPath)) return [];
    try {
      return JSON.parse(fs.readFileSync(this._indexPath, 'utf8'));
    } catch {
      return [];
    }
  }

  /**
   * Create a new profile directory and register it in index.json.
   * @param {string} name
   * @param {{encrypted?: boolean, password?: string}} opts
   * @returns {{id: string, profileDir: string}}
   */
  createProfile(name, { encrypted = false, password = null } = {}) {
    if (!name || !name.trim()) throw new Error('Profile name cannot be empty');

    this._ensureProfilesDir();
    const id = crypto.randomUUID();
    const profileDir = path.join(this._profilesDir, id);
    fs.mkdirSync(profileDir, { recursive: true });

    let pwd_hash = null;
    if (encrypted) {
      if (!password) throw new Error('A password is required for encrypted profiles');
      pwd_hash = this._hashPassword(password);
    }

    const now = Math.floor(Date.now() / 1000);
    const meta = { id, name: name.trim(), created_at: now, encrypted: Boolean(encrypted), pwd_hash };
    fs.writeFileSync(path.join(profileDir, 'meta.json'), JSON.stringify(meta, null, 2));
    fs.writeFileSync(path.join(profileDir, 'config.json'), JSON.stringify({
      dry_run: true,
      auto_timeout_enabled: false,
      auto_ban_enabled: false,
      timeout_threshold: 75.0,
      ban_threshold: 95.0,
      alert_threshold: 60.0,
      emote_filter_sensitivity: 50,
      default_channel: '',
      message_retention_days: 7,
      health_history_retention_days: 30,
      flagged_users_retention_days: 0,
      moderation_actions_retention_days: 0,
    }, null, 2));

    const indexEntry = { id, name: meta.name, created_at: now, last_used: null, encrypted: meta.encrypted };
    const index = this.listProfiles();
    index.push(indexEntry);
    this._saveIndex(index);

    return { id, profileDir };
  }

  /**
   * Update the profile name in index.json and meta.json.
   * @param {string} id
   * @param {string} newName
   */
  renameProfile(id, newName) {
    if (!newName || !newName.trim()) throw new Error('Profile name cannot be empty');
    const profileDir = this.getProfileDir(id);
    const metaPath = path.join(profileDir, 'meta.json');
    if (fs.existsSync(metaPath)) {
      const meta = JSON.parse(fs.readFileSync(metaPath, 'utf8'));
      meta.name = newName.trim();
      fs.writeFileSync(metaPath, JSON.stringify(meta, null, 2));
    }
    const index = this.listProfiles().map(p => p.id === id ? { ...p, name: newName.trim() } : p);
    this._saveIndex(index);
  }

  /**
   * Delete a profile directory and remove it from index.json.
   * Throws if the profile is the currently active one (checked by caller).
   * @param {string} id
   */
  deleteProfile(id) {
    const index = this.listProfiles();
    const entry = index.find(p => p.id === id);
    if (!entry) throw new Error(`Profile '${id}' not found`);

    const profileDir = this.getProfileDir(id);
    if (fs.existsSync(profileDir)) {
      fs.rmSync(profileDir, { recursive: true, force: true });
    }

    this._saveIndex(index.filter(p => p.id !== id));
  }

  /**
   * Return the absolute path to a profile's directory.
   * @param {string} id
   * @returns {string}
   */
  getProfileDir(id) {
    return path.join(this._profilesDir, id);
  }

  /**
   * Verify access to a profile.
   * Returns true if the profile is not encrypted, or if the password matches.
   * @param {string} id
   * @param {string|null} password
   * @returns {boolean}
   */
  verifyAccess(id, password) {
    const profiles = this.listProfiles();
    const entry = profiles.find(p => p.id === id);
    if (!entry) return false;
    if (!entry.encrypted) return true;

    // Read pwd_hash from meta.json (not stored in index for privacy)
    const metaPath = path.join(this.getProfileDir(id), 'meta.json');
    if (!fs.existsSync(metaPath)) return false;
    const meta = JSON.parse(fs.readFileSync(metaPath, 'utf8'));
    if (!meta.pwd_hash) return false;

    return this._verifyPassword(password || '', meta.pwd_hash);
  }

  /**
   * Update the last_used timestamp in index.json.
   * @param {string} id
   */
  markLastUsed(id) {
    const index = this.listProfiles().map(p =>
      p.id === id ? { ...p, last_used: Math.floor(Date.now() / 1000) } : p
    );
    this._saveIndex(index);
  }

  /**
   * On first run: if index.json is missing but the legacy data.db exists,
   * create a "Default" profile and copy the database into it.
   * WCM token migration happens inside Python on the first backend start.
   * @returns {{id:string, profileDir:string}|null}
   */
  migrateFromLegacy() {
    this._ensureProfilesDir();
    if (fs.existsSync(this._indexPath)) return null; // already initialised

    const legacyDb = path.join(this._base, 'data.db');
    const { id, profileDir } = this.createProfile('Default');

    if (fs.existsSync(legacyDb)) {
      try {
        fs.copyFileSync(legacyDb, path.join(profileDir, 'data.db'));
      } catch (err) {
        // Non-fatal — the profile is still usable without the migrated DB
        console.warn('[ProfileManager] migrateFromLegacy: could not copy data.db:', err.message);
      }
    }

    return { id, profileDir };
  }

  /**
   * Import a .tidsprofile file. Decrypts if needed, extracts ZIP into a new
   * profile directory, and registers it in index.json.
   * @param {string} srcPath
   * @param {{importPassword?: string, newName?: string}} opts
   * @returns {{id: string, profileDir: string}}
   */
  importProfile(srcPath, { importPassword = null, newName = null } = {}) {
    const rawBytes = fs.readFileSync(srcPath);

    let zipBytes;
    if (rawBytes.slice(0, 4).equals(MAGIC)) {
      // Encrypted envelope
      if (!importPassword) throw new Error('This profile is password-protected. Please provide the import password.');
      zipBytes = this._decrypt(rawBytes, importPassword);
    } else {
      // Plain ZIP
      zipBytes = rawBytes;
    }

    const zip = new AdmZip(zipBytes);

    // Read manifest and meta from archive
    let manifest = {};
    let meta = {};
    try { manifest = JSON.parse(zip.readAsText('manifest.json')); } catch { /* ok */ }
    try { meta = JSON.parse(zip.readAsText('meta.json')); } catch { /* ok */ }

    const profileName = newName || meta.name || manifest.profile_name || 'Imported Profile';

    this._ensureProfilesDir();
    const id = crypto.randomUUID();
    const profileDir = path.join(this._profilesDir, id);
    fs.mkdirSync(profileDir, { recursive: true });

    // Extract ZIP contents into the new profile directory
    zip.extractAllTo(profileDir, /* overwrite */ true);

    // Rewrite meta.json with the new local ID
    const now = Math.floor(Date.now() / 1000);
    const newMeta = {
      id,
      name: profileName,
      created_at: meta.created_at || now,
      encrypted: false, // imported profiles start unencrypted locally
    };
    fs.writeFileSync(path.join(profileDir, 'meta.json'), JSON.stringify(newMeta, null, 2));

    // Register in index
    const indexEntry = { id, name: profileName, created_at: newMeta.created_at, last_used: null, encrypted: false };
    const index = this.listProfiles();
    index.push(indexEntry);
    this._saveIndex(index);

    return { id, profileDir };
  }

  // ── Private helpers ────────────────────────────────────────────────────────

  _ensureProfilesDir() {
    fs.mkdirSync(this._profilesDir, { recursive: true });
  }

  _saveIndex(index) {
    fs.writeFileSync(this._indexPath, JSON.stringify(index, null, 2));
  }

  /**
   * Hash a password using scrypt.
   * @param {string} password
   * @returns {string}  "scrypt:<salt_hex>:<hash_hex>"
   */
  _hashPassword(password) {
    const salt = crypto.randomBytes(16).toString('hex');
    const hash = crypto.scryptSync(password, salt, 64).toString('hex');
    return `scrypt:${salt}:${hash}`;
  }

  /**
   * Verify a password against a stored hash.
   * @param {string} password
   * @param {string} storedHash  "scrypt:<salt_hex>:<hash_hex>"
   * @returns {boolean}
   */
  _verifyPassword(password, storedHash) {
    try {
      const [, saltHex, hashHex] = storedHash.split(':');
      const expected = Buffer.from(hashHex, 'hex');
      const actual = crypto.scryptSync(password, saltHex, 64);
      return crypto.timingSafeEqual(expected, actual);
    } catch {
      return false;
    }
  }

  /**
   * Decrypt an encrypted .tidsprofile binary envelope.
   * @param {Buffer} data
   * @param {string} password
   * @returns {Buffer} raw ZIP bytes
   */
  _decrypt(data, password) {
    if (!data.slice(0, 4).equals(MAGIC)) throw new Error('Invalid .tidsprofile file (bad magic bytes)');

    const version = data.readUInt8(4);
    if (version !== FORMAT_VERSION) throw new Error(`Unsupported .tidsprofile version: ${version}`);

    const flags = data.readUInt8(5);
    if (!(flags & FLAGS_ENCRYPTED)) {
      // Not actually encrypted — just a plain ZIP with a TIDS header (future use)
      return data.slice(HEADER_SIZE);
    }

    const payloadLen = data.readUInt32LE(6);
    const salt = data.slice(10, 26);
    const nonce = data.slice(26, 38);
    const tag = data.slice(38, 54);
    const ciphertext = data.slice(HEADER_SIZE, HEADER_SIZE + payloadLen);

    if (ciphertext.length !== payloadLen) {
      throw new Error('Truncated .tidsprofile: payload shorter than expected');
    }

    // Derive key — same parameters as Python backend
    const key = crypto.pbkdf2Sync(password, salt, PBKDF2_ITERATIONS, PBKDF2_KEYLEN, 'sha256');

    // AAD must match what Python used: the full 54-byte header with the real tag
    const aad = data.slice(0, HEADER_SIZE);

    try {
      const decipher = crypto.createDecipheriv('aes-256-gcm', key, nonce);
      decipher.setAuthTag(tag);
      decipher.setAAD(aad);
      return Buffer.concat([decipher.update(ciphertext), decipher.final()]);
    } catch {
      throw new Error('Decryption failed — incorrect password or corrupted file');
    }
  }
}

module.exports = { ProfileManager };
