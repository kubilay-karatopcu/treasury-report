import { createRoot } from 'react-dom/client';
import App from './App.jsx';

const container      = document.getElementById('presentation-root');
const manifestScript = document.getElementById('initial-manifest');

if (!container) {
  console.error('[presentations] #presentation-root bulunamadı.');
} else if (!manifestScript) {
  console.error('[presentations] #initial-manifest bulunamadı.');
} else {
  const initialManifest = JSON.parse(manifestScript.textContent);
  // mode = "editor" (default) | "snapshot" — set by snapshot.html
  const mode = container.dataset.mode || 'editor';
  createRoot(container).render(
    <App initialManifest={initialManifest} mode={mode} />
  );
}
