/**
 * SetupScreen — shown when the app is not yet authenticated or
 * no channel is configured. Guides the user through first-run setup.
 *
 * Steps:
 *   1. client_id  — enter Twitch app credentials
 *   2. auth       — complete PKCE browser auth
 *   3. channel    — pick the channel to monitor
 *   4. thresholds — choose sensitivity preset + confirm dry-run mode
 */

import { useEffect, useState } from 'react';
import { useChatStore } from '../store/chatStore';

interface SetupScreenProps {
  port: number;
  ipcSecret: string;
}

async function apiPost(port: number, secret: string, path: string, body: unknown) {
  const res = await fetch(`http://127.0.0.1:${port}/api${path}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-IPC-Secret': secret,
    },
    body: JSON.stringify(body),
  });
  return res.json();
}

async function apiPatch(port: number, secret: string, path: string, body: unknown) {
  const res = await fetch(`http://127.0.0.1:${port}/api${path}`, {
    method: 'PATCH',
    headers: {
      'Content-Type': 'application/json',
      'X-IPC-Secret': secret,
    },
    body: JSON.stringify(body),
  });
  return res.json();
}

// ---------------------------------------------------------------------------
// Sensitivity presets
// ---------------------------------------------------------------------------

interface Preset {
  id: string;
  label: string;
  description: string;
  alertThreshold: number;
  timeoutThreshold: number;
  banThreshold: number;
}

const PRESETS: Preset[] = [
  {
    id: 'conservative',
    label: 'Conservative',
    description: 'Fewer alerts, low false-positive rate. Good for established streamers with active mods.',
    alertThreshold: 70,
    timeoutThreshold: 85,
    banThreshold: 97,
  },
  {
    id: 'balanced',
    label: 'Balanced',
    description: 'Default tuning. Catches most bot raids with <3% false positives on normal chat.',
    alertThreshold: 60,
    timeoutThreshold: 75,
    banThreshold: 95,
  },
  {
    id: 'aggressive',
    label: 'Aggressive',
    description: 'Flags suspicious accounts early. Higher recall — best for channels that are actively targeted.',
    alertThreshold: 45,
    timeoutThreshold: 65,
    banThreshold: 92,
  },
];

// ---------------------------------------------------------------------------
// Threshold step
// ---------------------------------------------------------------------------

function ThresholdStep({
  port,
  ipcSecret,
  onDone,
}: {
  port: number;
  ipcSecret: string;
  onDone: () => void;
}) {
  const [selectedPreset, setSelectedPreset] = useState('balanced');
  const [liveModeEnabled, setLiveModeEnabled] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  async function handleFinish() {
    const preset = PRESETS.find((p) => p.id === selectedPreset)!;
    setLoading(true);
    setError('');
    try {
      await apiPatch(port, ipcSecret, '/config', {
        alert_threshold: preset.alertThreshold,
        timeout_threshold: preset.timeoutThreshold,
        ban_threshold: preset.banThreshold,
        dry_run: !liveModeEnabled,
      });
      onDone();
    } catch {
      setError('Failed to save settings. Please try again.');
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      <h2 className="text-lg font-semibold text-white mb-1">Detection sensitivity</h2>
      <p className="text-gray-400 text-sm mb-4">
        Choose how aggressively TwitchIDS flags suspicious accounts. You can adjust this anytime in Settings.
      </p>

      <div className="flex flex-col gap-2 mb-5">
        {PRESETS.map((preset) => (
          <button
            key={preset.id}
            onClick={() => setSelectedPreset(preset.id)}
            className={`text-left rounded-lg border px-4 py-3 transition-colors ${
              selectedPreset === preset.id
                ? 'border-accent-purple bg-surface-2'
                : 'border-surface-3 bg-surface hover:border-gray-500'
            }`}
          >
            <div className="flex items-center gap-2 mb-0.5">
              <div
                className={`w-3 h-3 rounded-full border-2 shrink-0 ${
                  selectedPreset === preset.id
                    ? 'border-accent-purple bg-accent-purple'
                    : 'border-gray-500'
                }`}
              />
              <span className="text-sm font-semibold text-white">{preset.label}</span>
              <span className="ml-auto text-xs text-gray-500 font-mono">
                alert ≥{preset.alertThreshold} · ban ≥{preset.banThreshold}
              </span>
            </div>
            <p className="text-xs text-gray-400 ml-5">{preset.description}</p>
          </button>
        ))}
      </div>

      {/* Live mode toggle */}
      <div className="rounded-lg border border-surface-3 bg-surface px-4 py-3 mb-4">
        <label className="flex items-start gap-3 cursor-pointer">
          <input
            type="checkbox"
            checked={liveModeEnabled}
            onChange={(e) => setLiveModeEnabled(e.target.checked)}
            className="mt-0.5 accent-purple-500"
          />
          <div>
            <span className="text-sm font-semibold text-white">Enable live moderation</span>
            <p className="text-xs text-gray-400 mt-0.5">
              When checked, TwitchIDS will execute automated timeouts and bans.
              Leave unchecked to stay in dry-run mode (actions are logged but not applied) —
              you can enable live moderation later from the Settings drawer.
            </p>
          </div>
        </label>
      </div>

      {error && <p className="text-red-400 text-sm mb-3">{error}</p>}

      <button
        onClick={handleFinish}
        disabled={loading}
        className="w-full bg-accent-purple hover:bg-purple-600 disabled:opacity-50 text-white font-semibold py-2.5 rounded-lg transition-colors"
      >
        {loading ? 'Saving…' : 'Launch TwitchIDS'}
      </button>
    </>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function SetupScreen({ port, ipcSecret }: SetupScreenProps) {
  const [step, setStep] = useState<'client_id' | 'auth' | 'channel' | 'thresholds'>('client_id');
  const [clientId, setClientId] = useState('');
  const [clientSecret, setClientSecret] = useState('');
  const [channel, setChannel] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const setConfiguredChannel = useChatStore((s) => s.setConfiguredChannel);

  // If client_id is already configured on backend, skip straight to auth step
  useEffect(() => {
    fetch(`http://127.0.0.1:${port}/api/auth/status`, {
      headers: { 'X-IPC-Secret': ipcSecret },
    })
      .then((r) => r.json())
      .then((data) => {
        if (data.client_id_configured) setStep('auth');
      })
      .catch(() => {});
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function handleClientIdSubmit() {
    if (!clientId.trim() || !clientSecret.trim()) return;
    setLoading(true);
    setError('');
    try {
      await apiPost(port, ipcSecret, '/auth/start', {
        client_id: clientId.trim(),
        client_secret: clientSecret.trim(),
      });
      setStep('auth');
    } catch {
      setError('Failed to start authorization. Is the backend running?');
    } finally {
      setLoading(false);
    }
  }

  async function handleChannelSubmit() {
    if (!channel.trim()) return;
    setLoading(true);
    setError('');
    try {
      const ch = channel.trim().replace('#', '');
      await apiPatch(port, ipcSecret, '/config', { default_channel: ch });
      setConfiguredChannel(ch);
      setStep('thresholds');
    } catch {
      setError('Failed to save channel. Please try again.');
    } finally {
      setLoading(false);
    }
  }

  function handleThresholdsDone() {
    window.location.reload();
  }

  // Step indicator
  const STEP_LABELS = ['App credentials', 'Authorize', 'Channel', 'Settings'];
  const stepIndex = { client_id: 0, auth: 1, channel: 2, thresholds: 3 }[step];

  return (
    <div className="flex flex-col items-center justify-center h-full gap-6 p-8 bg-surface">
      <div className="text-center">
        <h1 className="text-3xl font-bold text-white mb-1">TwitchIDS</h1>
        <p className="text-gray-500 text-sm">Twitch Chat Intrusion Detection System</p>
      </div>

      {/* Step indicator */}
      <div className="flex items-center gap-1">
        {STEP_LABELS.map((label, i) => (
          <div key={label} className="flex items-center gap-1">
            <div
              className={`flex items-center justify-center w-5 h-5 rounded-full text-[10px] font-bold transition-colors ${
                i < stepIndex
                  ? 'bg-accent-purple text-white'
                  : i === stepIndex
                    ? 'bg-accent-purple text-white ring-2 ring-accent-purple ring-offset-2 ring-offset-surface'
                    : 'bg-surface-3 text-gray-500'
              }`}
            >
              {i < stepIndex ? '✓' : i + 1}
            </div>
            <span className={`text-xs ${i === stepIndex ? 'text-gray-200' : 'text-gray-600'}`}>
              {label}
            </span>
            {i < STEP_LABELS.length - 1 && (
              <div className={`w-6 h-px mx-1 ${i < stepIndex ? 'bg-accent-purple' : 'bg-surface-3'}`} />
            )}
          </div>
        ))}
      </div>

      <div className="bg-surface-1 border border-surface-3 rounded-xl p-7 w-full max-w-md">
        {step === 'client_id' && (
          <>
            <h2 className="text-lg font-semibold text-white mb-1">Connect your Twitch app</h2>
            <p className="text-gray-400 text-sm mb-4">
              Enter your Twitch application Client ID. Register at{' '}
              <button
                className="text-accent-purple underline"
                onClick={() => window.electronAPI?.openExternal('https://dev.twitch.tv/console/apps')}
              >
                dev.twitch.tv
              </button>
              . Set the redirect URI to{' '}
              <code className="text-gray-300 bg-surface-2 px-1 rounded">http://localhost</code>.
            </p>
            <input
              type="text"
              value={clientId}
              onChange={(e) => setClientId(e.target.value)}
              placeholder="Client ID"
              className="w-full bg-surface-2 border border-surface-3 rounded-lg px-4 py-2.5 text-white text-sm mb-3 focus:outline-none focus:border-accent-purple"
              onKeyDown={(e) => e.key === 'Enter' && handleClientIdSubmit()}
            />
            <input
              type="password"
              value={clientSecret}
              onChange={(e) => setClientSecret(e.target.value)}
              placeholder="Client Secret"
              className="w-full bg-surface-2 border border-surface-3 rounded-lg px-4 py-2.5 text-white text-sm mb-4 focus:outline-none focus:border-accent-purple"
              onKeyDown={(e) => e.key === 'Enter' && handleClientIdSubmit()}
            />
            {error && <p className="text-red-400 text-sm mb-4">{error}</p>}
            <button
              onClick={handleClientIdSubmit}
              disabled={loading || !clientId.trim() || !clientSecret.trim()}
              className="w-full bg-accent-purple hover:bg-purple-600 disabled:opacity-50 text-white font-semibold py-2.5 rounded-lg transition-colors"
            >
              {loading ? 'Starting…' : 'Authorize with Twitch'}
            </button>
          </>
        )}

        {step === 'auth' && (
          <>
            <h2 className="text-lg font-semibold text-white mb-1">Complete authorization</h2>
            <p className="text-gray-400 text-sm mb-6">
              A browser window has opened. Sign in with Twitch and grant the requested permissions.
              Return here when done.
            </p>
            <div className="flex gap-3">
              <button
                onClick={() => setStep('channel')}
                className="flex-1 bg-accent-purple hover:bg-purple-600 text-white font-semibold py-2.5 rounded-lg transition-colors"
              >
                I've authorized — Continue
              </button>
              <button
                onClick={() => setStep('client_id')}
                className="px-4 bg-surface-2 hover:bg-surface-3 text-gray-300 rounded-lg transition-colors"
              >
                Back
              </button>
            </div>
          </>
        )}

        {step === 'channel' && (
          <>
            <h2 className="text-lg font-semibold text-white mb-1">Select channel to monitor</h2>
            <p className="text-gray-400 text-sm mb-4">
              Enter the Twitch channel name you want to monitor.
            </p>
            <input
              type="text"
              value={channel}
              onChange={(e) => setChannel(e.target.value)}
              placeholder="channelname"
              className="w-full bg-surface-2 border border-surface-3 rounded-lg px-4 py-2.5 text-white text-sm mb-4 focus:outline-none focus:border-accent-purple"
              onKeyDown={(e) => e.key === 'Enter' && handleChannelSubmit()}
            />
            {error && <p className="text-red-400 text-sm mb-4">{error}</p>}
            <button
              onClick={handleChannelSubmit}
              disabled={loading || !channel.trim()}
              className="w-full bg-accent-purple hover:bg-purple-600 disabled:opacity-50 text-white font-semibold py-2.5 rounded-lg transition-colors"
            >
              {loading ? 'Saving…' : 'Continue'}
            </button>
          </>
        )}

        {step === 'thresholds' && (
          <ThresholdStep port={port} ipcSecret={ipcSecret} onDone={handleThresholdsDone} />
        )}
      </div>
    </div>
  );
}
