/**
 * SetupScreen — shown when the app is not yet authenticated or
 * no channel is configured. Guides the user through first-run setup.
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

export function SetupScreen({ port, ipcSecret }: SetupScreenProps) {
  const [step, setStep] = useState<'client_id' | 'auth' | 'channel'>('client_id');
  const [clientId, setClientId] = useState('');
  const [clientSecret, setClientSecret] = useState('');
  const [channel, setChannel] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

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
    } catch (e) {
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
      await apiPatch(port, ipcSecret, '/config', { default_channel: channel.trim().replace('#', '') });
      // Reload the page to reconnect with the new channel
      window.location.reload();
    } catch (e) {
      setError('Failed to save channel. Please try again.');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex flex-col items-center justify-center h-full gap-8 p-8">
      <div className="text-center">
        <h1 className="text-3xl font-bold text-white mb-2">TwitchIDS</h1>
        <p className="text-gray-400">Twitch Chat Intrusion Detection System</p>
      </div>

      <div className="bg-surface-1 border border-surface-3 rounded-xl p-8 w-full max-w-md">
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
              <code className="text-gray-300 bg-surface-2 px-1 rounded">http://localhost:3000/callback</code>.
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
              {loading ? 'Starting...' : 'Authorize with Twitch'}
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
              {loading ? 'Saving...' : 'Start Monitoring'}
            </button>
          </>
        )}
      </div>

      <p className="text-gray-600 text-xs">
        Dry-run mode is enabled by default — no automated actions will be taken.
      </p>
    </div>
  );
}
