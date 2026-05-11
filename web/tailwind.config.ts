import type { Config } from 'tailwindcss'

const config: Config = {
  content: ['./src/**/*.{js,ts,jsx,tsx,mdx}'],
  theme: {
    extend: {
      colors: {
        surface: {
          base: '#0a0a0a',
          raised: '#0d0d0d',
          overlay: '#111111',
          border: 'rgba(255,32,32,0.2)',
          hover: '#1a0505',
        },
        brand: {
          DEFAULT: '#ff2020',
          light: '#ff4444',
          dim: '#cc0000',
          glow: 'rgba(255,32,32,0.15)',
        },
        text: {
          primary: '#f1f5f9',
          secondary: '#94a3b8',
          muted: '#475569',
          inverse: '#0a0a0a',
        },
        status: {
          success: '#00ff88',
          warning: '#f59e0b',
          error: '#ef4444',
          info: '#00ffff',
          pending: '#ff2020',
        },
        cyber: {
          red: '#ff2020',
          'red-dark': '#cc0000',
          cyan: '#00ffff',
          'cyan-dim': '#00e5ff',
          green: '#00ff88',
          dark: '#0a0a0a',
          'dark-2': '#0d0d0d',
          'dark-3': '#111111',
          'dark-4': '#161616',
        },
      },
      fontFamily: {
        sans: ['var(--font-orbitron)', 'system-ui', 'sans-serif'],
        mono: ['var(--font-sharetech)', '"JetBrains Mono"', 'monospace'],
        orbitron: ['var(--font-orbitron)', 'sans-serif'],
        sharetech: ['var(--font-sharetech)', 'monospace'],
      },
      animation: {
        'pulse-slow': 'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'pulse-red': 'pulseRed 2s ease-in-out infinite',
        'pulse-orb': 'pulseOrb 2.5s ease-in-out infinite',
        'fade-in': 'fadeIn 0.3s ease-out',
        'slide-in': 'slideIn 0.2s ease-out',
        blink: 'blink 1s step-end infinite',
        'wave-1': 'waveBar 1.0s ease-in-out infinite',
        'wave-2': 'waveBar 1.3s ease-in-out 0.1s infinite',
        'wave-3': 'waveBar 0.8s ease-in-out 0.2s infinite',
        'wave-4': 'waveBar 1.1s ease-in-out 0.3s infinite',
        'wave-5': 'waveBar 0.9s ease-in-out 0.15s infinite',
        'glow-pulse': 'glowPulse 2s ease-in-out infinite',
        'flow-dot': 'flowDot 2.5s linear infinite',
        'spin-slow': 'spin 8s linear infinite',
        'progress-fill': 'progressFill 1.5s ease-out forwards',
      },
      keyframes: {
        fadeIn: { from: { opacity: '0' }, to: { opacity: '1' } },
        slideIn: {
          from: { opacity: '0', transform: 'translateY(-4px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        pulseRed: {
          '0%, 100%': {
            boxShadow:
              '0 0 20px rgba(255,32,32,0.4), 0 0 40px rgba(255,32,32,0.2)',
          },
          '50%': {
            boxShadow:
              '0 0 40px rgba(255,32,32,0.8), 0 0 80px rgba(255,32,32,0.4)',
          },
        },
        pulseOrb: {
          '0%, 100%': { transform: 'scale(1)', opacity: '0.9' },
          '50%': { transform: 'scale(1.1)', opacity: '1' },
        },
        blink: {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0' },
        },
        waveBar: {
          '0%, 100%': { transform: 'scaleY(0.3)' },
          '50%': { transform: 'scaleY(1)' },
        },
        glowPulse: {
          '0%, 100%': { opacity: '0.5' },
          '50%': { opacity: '1' },
        },
        flowDot: {
          '0%': { left: '-8%', opacity: '0' },
          '10%': { opacity: '1' },
          '90%': { opacity: '1' },
          '100%': { left: '108%', opacity: '0' },
        },
        progressFill: {
          from: { width: '0%' },
          to: { width: 'var(--progress-width, 0%)' },
        },
      },
      boxShadow: {
        'cyber-red': '0 0 12px rgba(255,32,32,0.3), 0 0 24px rgba(255,32,32,0.1)',
        'cyber-red-lg':
          '0 0 24px rgba(255,32,32,0.5), 0 0 48px rgba(255,32,32,0.2)',
        'cyber-cyan': '0 0 12px rgba(0,255,255,0.3)',
        'cyber-green': '0 0 12px rgba(0,255,136,0.3)',
      },
    },
  },
  plugins: [],
}

export default config
