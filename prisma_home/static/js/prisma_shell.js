// PRISMA shell JS — Phase 10A
//
// Each "view" is a real Flask route, so we don't need an in-page goto()
// dispatcher. This file's job is small:
//   1. Sync the top-bar mode-toggle pills with the current URL.
//   2. Provide save-modal open/close stubs for Phase 10D's bound_experts
//      modal (already referenced from the prototype's Save button).

// Phase 11.polish: mode switch is now a single pill rendered server-side
// with the correct destination — no client-side state toggle needed.

// Phase 10D save-modal stubs — kept here so existing markup that references
// onclick="openSaveModal()" doesn't throw if the modal isn't on the page yet.
window.openSaveModal = function () {
  var el = document.getElementById('saveModal');
  if (el) el.classList.add('active');
};
window.closeSaveModal = function () {
  var el = document.getElementById('saveModal');
  if (el) el.classList.remove('active');
};

// Phase 12.light — theme toggle. The inline <head> script in
// _base_prisma.html already applied the saved theme before paint, so
// our job here is just the click handler + persistence.
(function () {
  var KEY = 'prisma-theme';
  function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    try { localStorage.setItem(KEY, theme); } catch (e) { /* private mode */ }
  }
  function currentTheme() {
    return document.documentElement.getAttribute('data-theme') || 'dark';
  }
  function wire() {
    var btn = document.getElementById('themeToggle');
    if (!btn) return;
    btn.addEventListener('click', function () {
      applyTheme(currentTheme() === 'dark' ? 'light' : 'dark');
    });
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', wire);
  } else {
    wire();
  }
})();
