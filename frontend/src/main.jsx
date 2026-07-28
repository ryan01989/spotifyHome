import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'
import { SpinProvider } from './SpinContext.jsx'
import { NowPlayingProvider } from './NowPlayingContext.jsx'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <SpinProvider>
      <NowPlayingProvider>
        <App />
      </NowPlayingProvider>
    </SpinProvider>
  </StrictMode>,
)
