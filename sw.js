// Kill-switch service worker.
// Unregisters itself immediately so stale cached SW versions stop running.
self.addEventListener('install', () => self.skipWaiting());

self.addEventListener('activate', async () => {
  await self.registration.unregister();
  const clients = await self.clients.matchAll({ type: 'window' });
  clients.forEach(client => client.navigate(client.url));
});
