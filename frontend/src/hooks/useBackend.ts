/**
 * useBackend — listens for Electron IPC events from the main process
 * that tell the renderer the Python backend port and IPC secret.
 *
 * Also handles backend reconnect events (after Python crash + restart).
 */

import { useEffect } from 'react';
import { useChatStore } from '../store/chatStore';
// Window.electronAPI is declared in src/electron.d.ts

export function useBackend() {
  const { setBackendConfig, setBackendConnected } = useChatStore();

  useEffect(() => {
    const api = window.electronAPI;

    if (api) {
      // Register listener for future ready events
      api.onBackendReady(({ port, ipcSecret }) => {
        setBackendConfig({ port, ipcSecret });
      });

      api.onBackendDisconnected(() => {
        setBackendConnected(false);
      });

      api.onBackendReconnected(({ port, ipcSecret }) => {
        setBackendConfig({ port, ipcSecret });
      });

      // Pull current config in case backend-ready already fired before this hook mounted
      api.getBackendConfig().then((config) => {
        if (config) setBackendConfig(config);
      });
    } else {
      // Running in browser (dev mode without Electron)
      // Read config from env or use defaults
      const devPort = parseInt(import.meta.env.VITE_BACKEND_PORT ?? '7842', 10);
      const devSecret = import.meta.env.VITE_IPC_SECRET ?? '';
      if (devSecret) {
        setBackendConfig({ port: devPort, ipcSecret: devSecret });
      }
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
}
