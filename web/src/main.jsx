import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
)

// PWA service worker — PRODUCTION only: in dev it would shadow Vite's module
// graph and serve stale modules over HMR. Registration failing is a non-event
// (the app is online-only by design), so it never blocks or breaks startup.
if (import.meta.env.PROD && 'serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch((err) => console.warn('SW registration failed:', err))
  })
}
