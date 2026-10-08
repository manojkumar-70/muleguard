/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        background: '#050505',
        surface: '#0D0D10',
        elevated: '#141419',
        border: '#25252D',
        threat: {
          DEFAULT: '#FF2A3D',
          dark: '#B5122A',
        },
        intel: {
          DEFAULT: '#6366F1',
          dark: '#312E81',
        },
        text: {
          primary: '#F5F5F5',
          muted: '#8B8B96',
        },
      },
      fontFamily: {
        mono: ['IBM Plex Mono', 'Consolas', 'ui-monospace', 'monospace'],
        sans: ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
      },
    },
  },
  plugins: [],
};
