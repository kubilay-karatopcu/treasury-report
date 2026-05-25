// PRISMA shell JS — Phase 10A
//
// Each "view" is a real Flask route, so we don't need an in-page goto()
// dispatcher. This file's job is small:
//   1. Sync the top-bar mode-toggle pills with the current URL.
//   2. Provide save-modal open/close stubs for Phase 10D's bound_experts
//      modal (already referenced from the prototype's Save button).

(function () {
  // Server already renders the correct `on` class via the `mode` context var
  // (see partials/topbar.html). The JS only kicks in for cases where the
  // server context isn't decisive — currently a no-op, kept as a hook for
  // future SPA-style transitions. Reading `body.classList` is the source of
  // truth: `body.prisma.atolye` → producer on; `body.prisma.consumer` → consumer.
  var bodyClasses = document.body.classList;
  var isAtolye = bodyClasses.contains('atolye');
  var consumer = document.getElementById('modeConsumer');
  var producer = document.getElementById('modeProducer');
  if (consumer && producer) {
    consumer.classList.toggle('on', !isAtolye);
    producer.classList.toggle('on', isAtolye);
  }
})();

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
