/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        // Dark dashboard palette
        surface: {
          DEFAULT: '#0f1117',
          1: '#161b22',
          2: '#1c2230',
          3: '#242d3d',
        },
        accent: {
          purple: '#9147ff',  // Twitch purple
          green:  '#00c853',
          yellow: '#ffd600',
          orange: '#ff6d00',
          red:    '#f44336',
        },
      },
      fontFamily: {
        mono: ['JetBrains Mono', 'Fira Code', 'Consolas', 'monospace'],
      },
    },
  },
  plugins: [],
};
