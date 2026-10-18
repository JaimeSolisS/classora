import { createRoot } from 'react-dom/client';
import App from './App';
import { createQueryClient } from './app/queryClient';
import { createAppRouter } from './app/router';
import { applyLanguage } from './i18n';
import { resolveInitialLanguage } from './i18n/language';
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
// Same for the language: stored choice, then browser languages, then Spanish.
// The catalogs are bundled, so the first render is already translated and
// <html lang> already matches.
applyLanguage(resolveInitialLanguage());

const container = document.getElementById('root');
if (!container) {
  throw new Error('Missing #root element');
}

// The app's single query client and router.
const queryClient = createQueryClient();
const router = createAppRouter();

createRoot(container).render(<App router={router} queryClient={queryClient} />);
// H1 throwaway: no-op comment
