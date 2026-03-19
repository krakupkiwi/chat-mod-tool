/**
 * Splash — shown while the Python backend is initialising.
 * Displayed from app start until backend emits the ready signal.
 */

export function Splash({ message = 'Starting backend…' }: { message?: string }) {
  return (
    <div className="flex flex-col items-center justify-center h-screen bg-surface gap-4 select-none">
      {/* Logo / wordmark */}
      <div className="flex items-center gap-3">
        <svg width="36" height="36" viewBox="0 0 36 36" fill="none">
          <rect width="36" height="36" rx="8" fill="#9147ff" fillOpacity="0.15" />
          <rect x="1" y="1" width="34" height="34" rx="7" stroke="#9147ff" strokeWidth="1.5" strokeOpacity="0.4" />
          <path
            d="M12 10h12v2l-4 4v2h-4v-2l-4-4V10zM14 22h8v4h-8v-4z"
            fill="#9147ff"
            fillOpacity="0.9"
          />
        </svg>
        <span className="text-2xl font-bold text-white tracking-wide">TwitchIDS</span>
      </div>

      {/* Spinner */}
      <div className="mt-2">
        <svg
          className="animate-spin text-accent-purple"
          width="28"
          height="28"
          viewBox="0 0 24 24"
          fill="none"
        >
          <circle
            cx="12" cy="12" r="10"
            stroke="currentColor"
            strokeWidth="3"
            strokeOpacity="0.2"
          />
          <path
            d="M12 2a10 10 0 0 1 10 10"
            stroke="currentColor"
            strokeWidth="3"
            strokeLinecap="round"
          />
        </svg>
      </div>

      <p className="text-sm text-gray-500">{message}</p>
    </div>
  );
}
