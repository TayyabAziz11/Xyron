// Configure ONNX Runtime before React mounts and before any VAD hook initialises.
// Single-thread mode skips the Worker entirely — no .mjs, no SharedArrayBuffer needed.
import * as ort from 'onnxruntime-web'
ort.env.wasm.numThreads = 1

import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import { AuthProvider } from './auth/AuthProvider'
import '@/styles/globals.css'
import '@desktop/styles/desktop.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <AuthProvider>
      <App />
    </AuthProvider>
  </React.StrictMode>,
)
