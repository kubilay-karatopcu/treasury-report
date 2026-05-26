/**
 * Cross-context clipboard helper.
 *
 * The modern Async Clipboard API (`navigator.clipboard.writeText`) requires
 * a secure context — HTTPS or localhost. Inside a corporate LAN that runs
 * the app over plain HTTP (e.g. http://192.168.1.105:8081), `navigator.clipboard`
 * is `undefined` and copies silently fail.
 *
 * This wrapper:
 *   1. Tries `navigator.clipboard.writeText` in secure contexts.
 *   2. Falls back to a transient hidden <textarea> + `document.execCommand('copy')`,
 *      which works in every browser back to IE9 without secure-context gating.
 *
 * Returns a Promise that resolves on success and rejects on failure (caller
 * can show an error toast).
 */
export async function copyToClipboard(text) {
  if (text == null) return Promise.reject(new Error('clipboard: text is null'));
  const value = String(text);

  // Path 1: modern Async Clipboard API — only works in secure contexts.
  if (typeof navigator !== 'undefined'
      && navigator.clipboard
      && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(value);
      return;
    } catch (err) {
      // Some browsers throw "NotAllowedError" even with the API present
      // (e.g. when the document is not focused). Fall through to the
      // textarea fallback.
      console.warn('clipboard: navigator.clipboard.writeText failed, falling back:', err);
    }
  }

  // Path 2: textarea + execCommand('copy'). Works in HTTP / older browsers.
  return new Promise((resolve, reject) => {
    const ta = document.createElement('textarea');
    ta.value = value;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.top = '0';
    ta.style.left = '-9999px';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    const prevActive = document.activeElement;
    try {
      ta.focus();
      ta.select();
      ta.setSelectionRange(0, value.length);
      const ok = document.execCommand('copy');
      if (!ok) throw new Error('execCommand(copy) returned false');
      resolve();
    } catch (e) {
      reject(e);
    } finally {
      document.body.removeChild(ta);
      // Restore focus to whatever was active before.
      if (prevActive && typeof prevActive.focus === 'function') {
        try { prevActive.focus(); } catch {}
      }
    }
  });
}
