/**
 * ProfilePicker — the first screen shown on every launch.
 *
 * Displayed before the Python backend starts, so it operates entirely via
 * Electron IPC (window.electronAPI.profiles.*).  No REST calls are made here.
 *
 * Flow:
 *   1. Load profile list on mount.
 *   2. User clicks a card → if encrypted, show password prompt.
 *   3. Call profiles.select() → main.js starts Python with --profile-dir.
 *   4. Show loading splash while waiting for backend-ready.
 *   5. On backend-ready → call onSelected(profileId).
 */

import { useEffect, useRef, useState } from 'react';
import { Splash } from './Splash';

// ─── Types ────────────────────────────────────────────────────────────────────

type View =
  | 'loading'       // fetching profile list
  | 'list'          // showing profile grid
  | 'create'        // new-profile form
  | 'password'      // password prompt for encrypted profile
  | 'switching';    // backend starting (splash)

interface PendingSelect {
  profileId: string;
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function formatDate(ts: number | null): string {
  if (!ts) return 'Never opened';
  return new Date(ts * 1000).toLocaleDateString(undefined, {
    month: 'short', day: 'numeric', year: 'numeric',
  });
}

// ─── Sub-components ───────────────────────────────────────────────────────────

function LockIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor" aria-hidden>
      <path d="M11 7V5a3 3 0 0 0-6 0v2H3v8h10V7h-2zm-4-2a1 1 0 0 1 2 0v2H7V5z" />
    </svg>
  );
}

function Spinner({ size = 20 }: { size?: number }) {
  return (
    <svg className="animate-spin" width={size} height={size} viewBox="0 0 24 24" fill="none">
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" strokeOpacity="0.2" />
      <path d="M12 2a10 10 0 0 1 10 10" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}

// ─── ProfilePicker ────────────────────────────────────────────────────────────

interface ProfilePickerProps {
  onSelected: (profileId: string) => void;
}

export function ProfilePicker({ onSelected }: ProfilePickerProps) {
  const [view, setView] = useState<View>('loading');
  const [profiles, setProfiles] = useState<ProfileMeta[]>([]);
  const [error, setError] = useState<string | null>(null);

  // New-profile form state
  const [newName, setNewName] = useState('');
  const [newEncrypted, setNewEncrypted] = useState(false);
  const [newPassword, setNewPassword] = useState('');
  const [newPasswordConfirm, setNewPasswordConfirm] = useState('');
  const [creating, setCreating] = useState(false);

  // Password-prompt state (for encrypted profiles)
  const [pending, setPending] = useState<PendingSelect | null>(null);
  const [passwordInput, setPasswordInput] = useState('');
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [verifying, setVerifying] = useState(false);

  // Rename state
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState('');

  // Busy flag to prevent double-click on cards
  const [busyId, setBusyId] = useState<string | null>(null);

  // Refs for reliable Electron focus (autoFocus is unreliable in Electron)
  const passwordRef = useRef<HTMLInputElement>(null);
  const newNameRef = useRef<HTMLInputElement>(null);

  // Focus the name input whenever the create view becomes active
  useEffect(() => {
    if (view === 'create') {
      requestAnimationFrame(() => newNameRef.current?.focus());
    }
  }, [view]);

  // Focus the password input whenever the password overlay becomes active
  useEffect(() => {
    if (view === 'password') {
      requestAnimationFrame(() => passwordRef.current?.focus());
    }
  }, [view]);

  // ── Load profiles ──────────────────────────────────────────────────────────

  const loadProfiles = async () => {
    setView('loading');
    setError(null);
    try {
      const list = await window.electronAPI!.profiles.list();
      setProfiles(list.sort((a, b) => (b.last_used ?? 0) - (a.last_used ?? 0)));
      setView('list');
    } catch (e: unknown) {
      setError(String(e));
      setView('list');
    }
  };

  useEffect(() => { loadProfiles(); }, []);

  // Listen for backend-ready — fired after profile-select completes Python startup
  useEffect(() => {
    window.electronAPI?.onBackendReady(({ }) => {
      if (pending) {
        onSelected(pending.profileId);
        setPending(null);
      }
    });
  }, [pending, onSelected]);

  // ── Open a profile ─────────────────────────────────────────────────────────

  const openProfile = async (profile: ProfileMeta) => {
    if (busyId) return;
    setBusyId(profile.id);

    if (profile.encrypted) {
      setPending({ profileId: profile.id });
      setPasswordInput('');
      setPasswordError(null);
      setView('password');
      setBusyId(null);
      return;
    }

    await _activateProfile(profile.id);
  };

  const _activateProfile = async (profileId: string) => {
    setPending({ profileId }); // must be set so onBackendReady knows which profile to confirm
    setView('switching');
    setError(null);
    const result = await window.electronAPI!.profiles.select(profileId);
    if (!result.success) {
      setPending(null);
      setError(result.error === 'incorrect_password' ? 'Incorrect password.' : (result.error ?? 'Failed to load profile.'));
      setBusyId(null);
      await loadProfiles();
    }
    // On success, we wait for onBackendReady to fire (wired above)
  };

  // ── Password prompt submit ─────────────────────────────────────────────────

  const submitPassword = async () => {
    if (!pending || verifying) return;
    setVerifying(true);
    setPasswordError(null);

    const result = await window.electronAPI!.profiles.select(
      pending.profileId, passwordInput
    );

    if (!result.success) {
      setPasswordError(
        result.error === 'incorrect_password'
          ? 'Incorrect password — try again.'
          : (result.error ?? 'Failed to load profile.')
      );
      setVerifying(false);
      requestAnimationFrame(() => passwordRef.current?.focus());
      return;
    }

    // Success — wait for backend-ready (wired in useEffect above)
    setView('switching');
    setVerifying(false);
  };

  // ── Create profile ─────────────────────────────────────────────────────────

  const submitCreate = async () => {
    if (creating) return;
    if (!newName.trim()) { setError('Profile name cannot be empty.'); return; }
    if (newEncrypted && !newPassword) { setError('A password is required for encrypted profiles.'); return; }
    if (newEncrypted && newPassword !== newPasswordConfirm) { setError('Passwords do not match.'); return; }
    setCreating(true);
    setError(null);
    try {
      const { id } = await window.electronAPI!.profiles.create(
        newName.trim(),
        { encrypted: newEncrypted, password: newEncrypted ? newPassword : undefined }
      );
      setNewName(''); setNewPassword(''); setNewPasswordConfirm(''); setNewEncrypted(false);
      await loadProfiles();
      // Immediately select the new profile
      setBusyId(id);
      await _activateProfile(id);
    } catch (e: unknown) {
      setError(String(e));
    } finally {
      setCreating(false);
    }
  };

  // ── Delete profile ─────────────────────────────────────────────────────────

  const deleteProfile = async (profile: ProfileMeta, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!confirm(`Delete profile "${profile.name}"? This cannot be undone.`)) return;
    try {
      await window.electronAPI!.profiles.delete(profile.id);
      await loadProfiles();
    } catch (err: unknown) {
      setError(String(err));
    }
  };

  // ── Rename profile ─────────────────────────────────────────────────────────

  const startRename = (profile: ProfileMeta, e: React.MouseEvent) => {
    e.stopPropagation();
    setRenamingId(profile.id);
    setRenameValue(profile.name);
  };

  const submitRename = async (id: string) => {
    if (!renameValue.trim()) { setRenamingId(null); return; }
    try {
      await window.electronAPI!.profiles.rename(id, renameValue.trim());
      setRenamingId(null);
      await loadProfiles();
    } catch (e: unknown) {
      setError(String(e));
    }
  };

  // ── Import profile ─────────────────────────────────────────────────────────

  const importProfile = async () => {
    const result = await window.electronAPI!.showOpenDialog({
      title: 'Import TwitchIDS Profile',
      filters: [{ name: 'TwitchIDS Profile', extensions: ['tidsprofile'] }],
      properties: ['openFile'],
    });
    if (result.canceled || !result.filePaths[0]) return;
    const srcPath = result.filePaths[0];

    const importPassword = prompt('Enter the import password (leave blank if unencrypted):') ?? '';
    try {
      const { id } = await window.electronAPI!.profiles.import(
        srcPath, importPassword || undefined
      );
      await loadProfiles();
      setBusyId(id);
      await _activateProfile(id);
    } catch (e: unknown) {
      setError(String(e));
    }
  };

  // ─── Render ────────────────────────────────────────────────────────────────

  if (view === 'loading') return <Splash message="Loading profiles…" />;
  if (view === 'switching') return <Splash message="Loading profile…" />;

  return (
    <div className="flex flex-col h-screen bg-surface text-white">
      {/* Header */}
      <div className="flex items-center gap-3 px-8 pt-10 pb-6">
        <svg width="30" height="30" viewBox="0 0 36 36" fill="none">
          <rect width="36" height="36" rx="8" fill="#9147ff" fillOpacity="0.15" />
          <path d="M12 10h12v2l-4 4v2h-4v-2l-4-4V10zM14 22h8v4h-8v-4z" fill="#9147ff" fillOpacity="0.9" />
        </svg>
        <div>
          <h1 className="text-xl font-bold tracking-wide">TwitchIDS</h1>
          <p className="text-xs text-gray-500">Select a profile to continue</p>
        </div>
      </div>

      {/* Error banner */}
      {error && (
        <div className="mx-8 mb-4 px-4 py-2 bg-red-900/40 border border-red-700/50 rounded text-sm text-red-300">
          {error}
          <button className="ml-3 text-red-400 hover:text-red-200 underline text-xs" onClick={() => setError(null)}>Dismiss</button>
        </div>
      )}

      {/* Password prompt overlay — rendered as a fixed overlay, never inside a button */}
      {view === 'password' && pending && (
        <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50">
          <div className="bg-surface border border-gray-700 rounded-xl p-6 w-80 flex flex-col gap-4">
            <h2 className="font-semibold text-base">Enter Profile Password</h2>
            <input
              ref={passwordRef}
              type="password"
              className="w-full bg-gray-800 border border-gray-600 rounded px-3 py-2 text-sm focus:outline-none focus:border-purple-500"
              placeholder="Password"
              value={passwordInput}
              onChange={e => setPasswordInput(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && submitPassword()}
              disabled={verifying}
            />
            {passwordError && <p className="text-xs text-red-400">{passwordError}</p>}
            <div className="flex gap-2 justify-end">
              <button
                className="px-3 py-1.5 text-sm text-gray-400 hover:text-white"
                onClick={() => { setView('list'); setPending(null); setBusyId(null); }}
                disabled={verifying}
              >
                Cancel
              </button>
              <button
                className="px-4 py-1.5 text-sm bg-purple-600 hover:bg-purple-500 rounded font-medium flex items-center gap-2"
                onClick={submitPassword}
                disabled={verifying}
              >
                {verifying && <Spinner size={14} />}
                Unlock
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Create-profile panel */}
      {view === 'create' && (
        <div className="flex-1 flex items-center justify-center">
          <div className="bg-gray-900 border border-gray-700 rounded-xl p-7 w-96 flex flex-col gap-4">
            <h2 className="font-semibold text-base">New Profile</h2>
            <div>
              <label className="text-xs text-gray-400 mb-1 block">Profile Name</label>
              <input
                ref={newNameRef}
                type="text"
                className="w-full bg-gray-800 border border-gray-600 rounded px-3 py-2 text-sm focus:outline-none focus:border-purple-500"
                placeholder="e.g. Main Channel"
                value={newName}
                onChange={e => setNewName(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && submitCreate()}
              />
            </div>
            <label className="flex items-center gap-2 text-sm cursor-pointer">
              <input
                type="checkbox"
                className="accent-purple-500"
                checked={newEncrypted}
                onChange={e => setNewEncrypted(e.target.checked)}
              />
              <span>Password-protect this profile</span>
            </label>
            {newEncrypted && (
              <>
                <input
                  type="password"
                  className="w-full bg-gray-800 border border-gray-600 rounded px-3 py-2 text-sm focus:outline-none focus:border-purple-500"
                  placeholder="Password"
                  value={newPassword}
                  onChange={e => setNewPassword(e.target.value)}
                />
                <input
                  type="password"
                  className="w-full bg-gray-800 border border-gray-600 rounded px-3 py-2 text-sm focus:outline-none focus:border-purple-500"
                  placeholder="Confirm password"
                  value={newPasswordConfirm}
                  onChange={e => setNewPasswordConfirm(e.target.value)}
                  onKeyDown={e => e.key === 'Enter' && submitCreate()}
                />
              </>
            )}
            {error && <p className="text-xs text-red-400">{error}</p>}
            <div className="flex gap-2 justify-end pt-1">
              <button
                className="px-3 py-1.5 text-sm text-gray-400 hover:text-white"
                onClick={() => { setView('list'); setError(null); }}
              >
                Cancel
              </button>
              <button
                className="px-4 py-1.5 text-sm bg-purple-600 hover:bg-purple-500 rounded font-medium flex items-center gap-2"
                onClick={submitCreate}
                disabled={creating}
              >
                {creating && <Spinner size={14} />}
                Create &amp; Open
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Profile list */}
      {view === 'list' && (
        <div className="flex-1 overflow-y-auto px-8 pb-8">
          {profiles.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-64 text-gray-500 gap-3">
              <p className="text-sm">No profiles yet.</p>
              <button
                className="px-4 py-2 text-sm bg-purple-600 hover:bg-purple-500 rounded font-medium"
                onClick={() => { setError(null); setView('create'); }}
              >
                + Create First Profile
              </button>
            </div>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 mt-2">
              {profiles.map(profile => (
                <ProfileCard
                  key={profile.id}
                  profile={profile}
                  busy={busyId === profile.id}
                  renamingId={renamingId}
                  renameValue={renameValue}
                  onOpen={() => openProfile(profile)}
                  onDelete={e => deleteProfile(profile, e)}
                  onStartRename={e => startRename(profile, e)}
                  onRenameChange={setRenameValue}
                  onRenameSubmit={() => submitRename(profile.id)}
                  onRenameCancel={() => setRenamingId(null)}
                />
              ))}
            </div>
          )}
        </div>
      )}

      {/* Footer actions */}
      {view === 'list' && (
        <div className="flex items-center gap-3 px-8 py-4 border-t border-gray-800">
          <button
            className="px-4 py-2 text-sm bg-purple-600 hover:bg-purple-500 rounded font-medium"
            onClick={() => { setError(null); setView('create'); }}
          >
            + New Profile
          </button>
          <button
            className="px-4 py-2 text-sm bg-gray-700 hover:bg-gray-600 rounded"
            onClick={importProfile}
          >
            Import .tidsprofile
          </button>
        </div>
      )}
    </div>
  );
}

// ─── ProfileCard ──────────────────────────────────────────────────────────────

interface ProfileCardProps {
  profile: ProfileMeta;
  busy: boolean;
  renamingId: string | null;
  renameValue: string;
  onOpen: () => void;
  onDelete: (e: React.MouseEvent) => void;
  onStartRename: (e: React.MouseEvent) => void;
  onRenameChange: (v: string) => void;
  onRenameSubmit: () => void;
  onRenameCancel: () => void;
}

function ProfileCard({
  profile, busy, renamingId, renameValue,
  onOpen, onDelete, onStartRename, onRenameChange, onRenameSubmit, onRenameCancel,
}: ProfileCardProps) {
  const isRenaming = renamingId === profile.id;
  const renameRef = useRef<HTMLInputElement>(null);

  // Focus rename input reliably when renaming starts
  useEffect(() => {
    if (isRenaming) {
      requestAnimationFrame(() => renameRef.current?.focus());
    }
  }, [isRenaming]);

  // When renaming, render as a plain div — NEVER nest an input inside a
  // role="button" element; Chromium will not give it keyboard focus.
  if (isRenaming) {
    return (
      <div className="relative flex flex-col items-start gap-2 p-4 rounded-xl border bg-gray-900 border-purple-500">
        <input
          ref={renameRef}
          type="text"
          className="w-full bg-gray-700 border border-purple-500 rounded px-2 py-1 text-sm focus:outline-none"
          value={renameValue}
          onChange={e => onRenameChange(e.target.value)}
          onKeyDown={e => {
            if (e.key === 'Enter') onRenameSubmit();
            if (e.key === 'Escape') onRenameCancel();
          }}
        />
        <div className="flex items-center gap-1">
          <button
            className="text-xs px-2 py-0.5 bg-purple-600 hover:bg-purple-500 rounded"
            onClick={onRenameSubmit}
          >
            Save
          </button>
          <button
            className="text-xs px-2 py-0.5 text-gray-400 hover:text-white"
            onClick={onRenameCancel}
          >
            Cancel
          </button>
        </div>
      </div>
    );
  }

  // Normal card — clickable div, no inputs inside
  return (
    <div
      role="button"
      tabIndex={busy ? -1 : 0}
      className={[
        'relative flex flex-col items-start gap-2 p-4 rounded-xl border text-left transition-all cursor-pointer',
        'bg-gray-900 border-gray-700 hover:border-purple-500/60 hover:bg-gray-800/80',
        busy ? 'opacity-60' : '',
      ].join(' ')}
      onClick={busy ? undefined : onOpen}
      onKeyDown={e => { if (!busy && (e.key === 'Enter' || e.key === ' ')) onOpen(); }}
    >
      {/* Name + lock */}
      <div className="flex items-center gap-2 w-full">
        <span className="font-semibold text-sm flex-1 truncate">{profile.name}</span>
        {profile.encrypted && (
          <span className="text-gray-500 flex-shrink-0" title="Password protected">
            <LockIcon />
          </span>
        )}
        {busy && <Spinner size={14} />}
      </div>

      {/* Last used */}
      <span className="text-xs text-gray-500">Last opened: {formatDate(profile.last_used)}</span>

      {/* Actions — stopPropagation so clicks don't bubble to card's onOpen */}
      <div
        className="flex items-center gap-1 mt-auto pt-2"
        onClick={e => e.stopPropagation()}
      >
        <button
          className="text-xs text-gray-500 hover:text-white px-2 py-0.5 rounded hover:bg-gray-700"
          onClick={onStartRename}
        >
          Rename
        </button>
        <button
          className="text-xs text-red-500 hover:text-red-300 px-2 py-0.5 rounded hover:bg-red-900/30"
          onClick={onDelete}
        >
          Delete
        </button>
      </div>
    </div>
  );
}
