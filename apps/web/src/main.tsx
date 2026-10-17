import { createRoot } from 'react-dom/client';
import App from './App';
import './styles/index.css';
import {
  applyTheme,
  readStoredChoice,
  resolveTheme,
  systemPrefersDark,
} from './theme/theme';

// Apply the stored theme before the first render so the page never flashes
// the wrong colors (done here, not in an inline script, to keep CSP simple).
applyTheme(resolveTheme(readStoredChoice(), systemPrefersDark()));

const container = document.getElementById('root');
if (!container) {
  throw new Error('Missing #root element');
}

createRoot(container).render(<App />);
