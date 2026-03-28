/**
 * ProfileManagerModal — in-dashboard profile management.
 *
 * Opens as a modal from the dashboard header.
 * The Python backend IS running when this is shown, so export is available.
 *
 * Actions:
 *   - Switch to a different profile (stops backend, starts new one)
 *   - Create a new profile
 *   - Rename any profile
 *   - Delete any non-active profile
 *   - Export the currently active profile
 *   - Import a .tidsprofile file (creates new profile, then optionally switch)
 */

import { useEffect, useRef, useState } from 'react';

interface Props {
  activeProfileId: string | null;
  onClose: () => void;
  /** Called when user initiates a switch — App.tsx handles the store reset. */
  onSwitchInitiated: () => void;
}

function Spinner({ size = 16 }: { size?: number }) {
  return (
    <svg className="animate-spin" width={size} height={size} viewBox="0 0 24 24" fill="none">
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" strokeOpacity="0.2" />
      <path d="M12 2a10 10 0 0 1 10 10" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}

function LockIcon() {
  return (
    <svg width="11" height="11" viewBox="0 0 16 16" fill="currentColor" aria-hidden>
      <path d="M11 7V5a3 3 0 0 0-6 0v2H3v8h10V7h-2zm-4-2a1 1 0 0 1 2 0v2H7V5z" />
    </svg>
  );
}

function formatDate(ts: number | null): string {
  if (!ts) return 'Never';
  return new Date(ts * 1000).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}

export function ProfileManagerModal({ activeProfileId, onClose, onSwitchInitiated }: Props) {
  const [profiles, setProfiles] = useState<ProfileMeta[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  // Create form
  const [showCreate, setShowCreate] = useState(false);
  const [createName, setCreateName] = useState('');
  const [createEncrypted, setCreateEncrypted] = useState(false);
  const [createPassword, setCreatePassword] = useState('');
  const [createPasswordConfirm, setCreatePasswordConfirm] = useState('');
  const [creating, setCreating] = useState(false);

  // Rename
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState('');

  // Export
  const [exporting, setExporting] = useState(false);

  // Refs for reliable Electron focus (autoFocus is unreliable in Electron)
  const createNameRef = useRef<HTMLInputElement>(null);
  const renameRef = useRef<HTMLInputElement>(null);

  // Focus create-name input when create form opens
  useEffect(() => {
    if (showCreate) {
      requestAnimationFrame(() => createNameRef.current?.focus());
    }
  }, [showCreate]);

  // Focus rename input when renaming starts
  useEffect(() => {
    if (renamingId) {
      requestAnimationFrame(() => renameRef.current?.focus());
    }
  }, [renamingId]);

  const loadProfiles = async () => {
    setLoading(true);
    try {
      const list = await window.electronAPI!.profiles.list();
      setProfiles(list.sort((a, b) => (b.last_used ?? 0) - (a.last_used ?? 0)));
    } catch (e: unknown) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadProfiles(); }, []);

  // ── Switch profile ──────────────────────────────────────────────────────────

  const switchProfile = async (profile: ProfileMeta) => {
    if (busyId || profile.id === activeProfileId) return;

    let password: string | undefined;
    if (profile.encrypted) {
      password = prompt(`Enter password for profile "${profile.name}":`) ?? undefined;
      if (password === undefined) return; // user cancelled
    }

    if (!confirm(`Switch to profile "${profile.name}"?\n\nThe current session will be ended.`)) return;

    setBusyId(profile.id);
    setError(null);

    try {
      const result = await window.electronAPI!.profiles.select(profile.id, password);
      if (!result.success) {
        setError(result.error === 'incorrect_password' ? 'Incorrect password.' : (result.error ?? 'Switch failed'));
        setBusyId(null);
        return;
      }
      // Trigger store reset in App.tsx (profile-switched event fires from main.js)
      onSwitchInitiated();
      onClose();
    } catch (e: unknown) {
      setError(String(e));
      setBusyId(null);
    }
  };

  // ── Create profile ──────────────────────────────────────────────────────────

  const submitCreate = async (andSwitch = false) => {
    if (creating) return;
    if (!createName.trim()) { setError('Profile name cannot be empty.'); return; }
    if (createEncrypted && !createPassword) { setError('Password is required.'); return; }
    if (createEncrypted && createPassword !== createPasswordConfirm) { setError('Passwords do not match.'); return; }

    setCreating(true);
    setError(null);
    try {
      const { id } = await window.electronAPI!.profiles.create(
        createName.trim(),
        { encrypted: createEncrypted, password: createEncrypted ? createPassword : undefined }
      );
      setCreateName(''); setCreatePassword(''); setCreatePasswordConfirm(''); setCreateEncrypted(false);
      setShowCreate(false);

      if (andSwitch) {
        // Switch immediately to the new profile
        setBusyId(id);
        const result = await window.electronAPI!.profiles.select(id);
        if (!result.success) {
          setError(result.error ?? 'Switch failed');
          setBusyId(null);
          await loadProfiles();
        } else {
          onSwitchInitiated();
          onClose();
        }
      } else {
        await loadProfiles();
      }
    } catch (e: unknown) {
      setError(String(e));
    } finally {
      setCreating(false);
    }
  };

  // ── Rename ──────────────────────────────────────────────────────────────────

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

  // ── Delete ──────────────────────────────────────────────────────────────────

  const deleteProfile = async (profile: ProfileMeta) => {
    if (!confirm(`Permanently delete profile "${profile.name}"?\n\nAll data in this profile will be lost.`)) return;
    try {
      await window.electronAPI!.profiles.delete(profile.id);
      await loadProfiles();
    } catch (e: unknown) {
      setError(String(e));
    }
  };

  // ── Export ──────────────────────────────────────────────────────────────────

  const exportProfile = async () => {
    if (exporting) return;
    const activeProfile = profiles.find(p => p.id === activeProfileId);
    const safeName = (activeProfile?.name ?? 'profile').replace(/[^a-z0-9]/gi, '_');

    const { canceled, filePath } = await window.electronAPI!.showSaveDialog({
      title: 'Export Profile',
      defaultPath: `${safeName}.tidsprofile`,
      filters: [{ name: 'TwitchIDS Profile', extensions: ['tidsprofile'] }],
    });
    if (canceled || !filePath) return;

    const usePassword = confirm('Encrypt the exported file with a password?\n\nClick OK to set a password, or Cancel for no encryption.');
    let exportPassword: string | undefined;
    if (usePassword) {
      const pwd = prompt('Enter export password:');
      if (!pwd) return;
      exportPassword = pwd;
    }

    setExporting(true);
    setError(null);
    try {
      const result = await window.electronAPI!.profiles.export(filePath, exportPassword);
      if (!result.success) {
        setError(result.error ?? 'Export failed');
      }
    } catch (e: unknown) {
      setError(String(e));
    } finally {
      setExporting(false);
    }
  };

  // ── Import ──────────────────────────────────────────────────────────────────

  const importProfile = async () => {
    const { canceled, filePaths } = await window.electronAPI!.showOpenDialog({
      title: 'Import TwitchIDS Profile',
      filters: [{ name: 'TwitchIDS Profile', extensions: ['tidsprofile'] }],
      properties: ['openFile'],
    });
    if (canceled || !filePaths[0]) return;

    const importPassword = prompt('Enter the import password (leave blank if unencrypted):') ?? '';
    try {
      await window.electronAPI!.profiles.import(filePaths[0], importPassword || undefined);
      await loadProfiles();
    } catch (e: unknown) {
      setError(String(e));
    }
  };

  // ─── Render ─────────────────────────────────────────────────────────────────

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={onClose}>
      <div
        className="bg-surface border border-gray-700 rounded-xl w-[540px] max-h-[80vh] flex flex-col shadow-2xl"
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-700">
          <h2 className="font-semibold text-base">Profile Manager</h2>
          <button className="text-gray-500 hover:text-white text-lg leading-none" onClick={onClose}>&times;</button>
        </div>

        {/* Error */}
        {error && (
          <div className="mx-5 mt-3 px-3 py-2 bg-red-900/40 border border-red-700/50 rounded text-sm text-red-300 flex justify-between">
            <span>{error}</span>
            <button className="ml-2 text-red-400 hover:text-white" onClick={() => setError(null)}>&times;</button>
          </div>
        )}

        {/* Profile list */}
        <div className="flex-1 overflow-y-auto px-5 py-4">
          {loading ? (
            <div className="flex justify-center py-8"><Spinner size={22} /></div>
          ) : (
            <div className="flex flex-col gap-2">
              {profiles.map(profile => {
                const isActive = profile.id === activeProfileId;
                const isBusy = busyId === profile.id;
                const isRenaming = renamingId === profile.id;
                return (
                  <div
                    key={profile.id}
                    className={[
                      'flex items-center gap-3 px-3 py-2.5 rounded-lg border transition-colors',
                      isActive
                        ? 'bg-purple-900/30 border-purple-600/50'
                        : 'bg-gray-900 border-gray-700 hover:border-gray-600',
                    ].join(' ')}
                  >
                    {/* Name + badges */}
                    <div className="flex-1 min-w-0">
                      {isRenaming ? (
                        <input
                          ref={renameRef}
                          type="text"
                          className="w-full bg-gray-700 border border-purple-500 rounded px-2 py-0.5 text-sm focus:outline-none"
                          value={renameValue}
                          onChange={e => setRenameValue(e.target.value)}
                          onKeyDown={e => {
                            if (e.key === 'Enter') submitRename(profile.id);
                            if (e.key === 'Escape') setRenamingId(null);
                          }}
                        />
                      ) : (
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-medium truncate">{profile.name}</span>
                          {profile.encrypted && (
                            <span className="text-gray-500"><LockIcon /></span>
                          )}
                          {isActive && (
                            <span className="text-xs bg-purple-700/50 text-purple-300 px-1.5 py-0.5 rounded">Active</span>
                          )}
                        </div>
                      )}
                      {!isRenaming && (
                        <p className="text-xs text-gray-500 mt-0.5">Last used: {formatDate(profile.last_used)}</p>
                      )}
                    </div>

                    {/* Actions */}
                    {!isRenaming && (
                      <div className="flex items-center gap-1 flex-shrink-0">
                        {!isActive && (
                          <button
                            className="text-xs px-2 py-1 bg-purple-600 hover:bg-purple-500 rounded flex items-center gap-1 disabled:opacity-50"
                            onClick={() => switchProfile(profile)}
                            disabled={!!busyId}
                          >
                            {isBusy ? <Spinner size={12} /> : null}
                            Switch
                          </button>
                        )}
                        <button
                          className="text-xs px-2 py-1 text-gray-400 hover:text-white hover:bg-gray-700 rounded"
                          onClick={() => { setRenamingId(profile.id); setRenameValue(profile.name); }}
                        >
                          Rename
                        </button>
                        {!isActive && (
                          <button
                            className="text-xs px-2 py-1 text-red-500 hover:text-red-300 hover:bg-red-900/30 rounded"
                            onClick={() => deleteProfile(profile)}
                          >
                            Delete
                          </button>
                        )}
                      </div>
                    )}
                    {isRenaming && (
                      <div className="flex gap-1">
                        <button className="text-xs px-2 py-1 bg-purple-600 hover:bg-purple-500 rounded" onClick={() => submitRename(profile.id)}>Save</button>
                        <button className="text-xs px-2 py-1 text-gray-400 hover:text-white" onClick={() => setRenamingId(null)}>Cancel</button>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}

          {/* Create form (inline) */}
          {showCreate && (
            <div className="mt-4 border border-gray-700 rounded-lg p-4 flex flex-col gap-3">
              <h3 className="text-sm font-medium">New Profile</h3>
              <input
                ref={createNameRef}
                type="text"
                className="w-full bg-gray-800 border border-gray-600 rounded px-3 py-2 text-sm focus:outline-none focus:border-purple-500"
                placeholder="Profile name"
                value={createName}
                onChange={e => setCreateName(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && submitCreate()}
              />
              <label className="flex items-center gap-2 text-sm cursor-pointer">
                <input type="checkbox" className="accent-purple-500" checked={createEncrypted} onChange={e => setCreateEncrypted(e.target.checked)} />
                Password-protect
              </label>
              {createEncrypted && (
                <>
                  <input type="password" className="w-full bg-gray-800 border border-gray-600 rounded px-3 py-2 text-sm focus:outline-none focus:border-purple-500" placeholder="Password" value={createPassword} onChange={e => setCreatePassword(e.target.value)} />
                  <input type="password" className="w-full bg-gray-800 border border-gray-600 rounded px-3 py-2 text-sm focus:outline-none focus:border-purple-500" placeholder="Confirm" value={createPasswordConfirm} onChange={e => setCreatePasswordConfirm(e.target.value)} onKeyDown={e => e.key === 'Enter' && submitCreate()} />
                </>
              )}
              <div className="flex gap-2 justify-end">
                <button className="text-xs px-3 py-1.5 text-gray-400 hover:text-white" onClick={() => setShowCreate(false)}>Cancel</button>
                <button className="text-xs px-3 py-1.5 bg-gray-700 hover:bg-gray-600 rounded flex items-center gap-1" onClick={() => submitCreate(false)} disabled={creating}>
                  Create
                </button>
                <button className="text-xs px-3 py-1.5 bg-purple-600 hover:bg-purple-500 rounded flex items-center gap-1" onClick={() => submitCreate(true)} disabled={creating}>
                  {creating && <Spinner size={12} />} Create &amp; Switch
                </button>
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center gap-2 px-5 py-3 border-t border-gray-700">
          <button
            className="text-xs px-3 py-1.5 bg-gray-700 hover:bg-gray-600 rounded"
            onClick={() => { setShowCreate(true); setError(null); }}
          >
            + New Profile
          </button>
          <button
            className="text-xs px-3 py-1.5 bg-gray-700 hover:bg-gray-600 rounded"
            onClick={importProfile}
          >
            Import
          </button>
          <div className="flex-1" />
          <button
            className="text-xs px-3 py-1.5 bg-gray-700 hover:bg-gray-600 rounded flex items-center gap-1 disabled:opacity-50"
            onClick={exportProfile}
            disabled={exporting || !activeProfileId}
            title="Export active profile"
          >
            {exporting && <Spinner size={12} />}
            Export Active Profile
          </button>
        </div>
      </div>
    </div>
  );
}
