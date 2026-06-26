/** Shared utilities for storefront and admin panel */
(function (global) {
  function escapeHtml(str) {
    return String(str ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function escapeAttr(str) {
    return escapeHtml(str).replace(/'/g, '&#39;');
  }

  function apiBaseMissingError() {
    const msg =
      'API URL не налаштовано: створіть api-config.js з api-config.example.js і вкажіть window.__API_BASE__';
    console.error('[poselyanov3dprint]', msg);
    throw new Error(msg);
  }

  function resolveApiBase() {
    const h = location.hostname;
    if (h === 'localhost' || h === '127.0.0.1') return 'http://localhost:8080';
    if (h.includes('ngrok-free.dev') || h.includes('ngrok-free.app')) return location.origin;
    if (global.__API_BASE__) return String(global.__API_BASE__).replace(/\/$/, '');
    if (h.includes('github.io')) apiBaseMissingError();
    return location.origin;
  }

  global.SharedApp = {
    escapeHtml,
    escapeAttr,
    resolveApiBase,
  };
})(window);
