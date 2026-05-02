import type { NextConfig } from 'next'

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: 'http://localhost:8000/api/:path*',
      },
    ]
  },
  webpack: (config, { isServer }) => {
    if (!isServer) {
      config.resolve.fallback = { ...config.resolve.fallback, fs: false }
    }
    // Enable async WebAssembly for onnxruntime-web (Silero VAD)
    config.experiments = { ...config.experiments, asyncWebAssembly: true, layers: true }
    return config
  },
}

export default nextConfig
