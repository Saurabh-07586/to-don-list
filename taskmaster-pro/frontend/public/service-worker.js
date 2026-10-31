const CACHE_NAME='taskmaster-shell-v1';

self.addEventListener('install',event=>{
  event.waitUntil((async()=>{
    const shellUrl=new URL('./',self.registration.scope).href;
    const response=await fetch(shellUrl);
    if(!response.ok)throw new Error(`App shell request failed: ${response.status}`);
    const html=await response.clone().text();
    const resources=[...html.matchAll(/(?:src|href)="([^"]+)"/g)]
      .map(match=>new URL(match[1],shellUrl))
      .filter(url=>url.origin===self.location.origin)
      .map(url=>url.href);
    const cache=await caches.open(CACHE_NAME);
    await cache.put(shellUrl,response);
    if(resources.length)await cache.addAll(resources);
    await self.skipWaiting();
  })());
});

self.addEventListener('activate',event=>{
  event.waitUntil((async()=>{
    const names=await caches.keys();
    await Promise.all(names.filter(name=>name.startsWith('taskmaster-shell-')&&name!==CACHE_NAME).map(name=>caches.delete(name)));
    await self.clients.claim();
  })());
});

self.addEventListener('fetch',event=>{
  const request=event.request;
  const requestUrl=new URL(request.url);
  if(request.method!=='GET'||requestUrl.origin!==self.location.origin||requestUrl.pathname.startsWith('/api/'))return;
  const shellUrl=new URL('./',self.registration.scope).href;
  if(request.mode==='navigate'){
    event.respondWith(fetch(request).then(response=>{
      if(response.ok)caches.open(CACHE_NAME).then(cache=>cache.put(shellUrl,response.clone()));
      return response;
    }).catch(async()=>{
      const cached=await caches.match(shellUrl);
      if(cached)return cached;
      throw new Error('The app shell is not cached for offline use.');
    }));
    return;
  }
  event.respondWith(caches.match(request).then(cached=>cached||fetch(request)));
});
