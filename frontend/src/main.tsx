import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import './index.css'
import App from './App'
import { LiveProvider } from './store'
import { LoginGate } from './components/LoginGate'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <LoginGate>
        <LiveProvider>
          <App />
        </LiveProvider>
      </LoginGate>
    </BrowserRouter>
  </StrictMode>,
)
