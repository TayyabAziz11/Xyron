/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './index.html',
    './src/**/*.{js,ts,jsx,tsx}',
    '../shared/**/*.{js,ts,jsx,tsx}',
  ],
  theme: {
    extend: {
      colors: {
        bg:      '#08080f',
        surface: '#0f0f1a',
        card:    '#13131f',
        'card-ho': '#181828',
        border:  '#1e1e30',
        'border-hi': '#2a2a42',
        brand: {
          DEFAULT: '#7c3aed',
          light:   '#8b5cf6',
          dim:     'rgba(124,58,237,0.14)',
          glow:    'rgba(124,58,237,0.07)',
        },
        ok:   '#10b981',
        warn: '#f59e0b',
        err:  '#ef4444',
      },
      fontFamily: {
        sans: ['-apple-system', 'Inter', 'Segoe UI', 'system-ui', 'sans-serif'],
      },
      animation: {
        'pulse-slow':  'pulse 3s cubic-bezier(0.4,0,0.6,1) infinite',
        'spin-slow':   'spin 3s linear infinite',
        'ping-slow':   'ping 2s cubic-bezier(0,0,0.2,1) infinite',
        'glow':        'glow 2s ease-in-out infinite alternate',
      },
      keyframes: {
        glow: {
          '0%':   { boxShadow: '0 0 5px rgba(124,58,237,0.3)' },
          '100%': { boxShadow: '0 0 20px rgba(124,58,237,0.8), 0 0 40px rgba(124,58,237,0.4)' },
        },
      },
      backdropBlur: { xs: '2px' },
    },
  },
  plugins: [],
}
