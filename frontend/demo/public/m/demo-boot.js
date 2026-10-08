/* WAREXX live demo — the phone app's start-up (/m).
 *
 * /m in the demo IS the WAREXX phone app (vendor/warexx-app/mobile/index.html, a
 * snapshot of app/backend/app/mobile, copied in at build by tools/mobile-page.mjs,
 * with this script added before its own).
 * This only does what demo/main.jsx does for the desktop:
 *
 *   1. starts the service worker (../sw.js) that stands in for the server, and
 *      holds the app's first request until it is in charge (window.__essaReady);
 *   2. signs the visitor in as the demo's Sample Admin, in Sample Warehouse A;
 *   3. marks the page as the demo (window.WAREXX_DEMO), so the app can offer
 *      sample codes to scan and say that saves are simulated.
 */
(function () {
  var START_WAREHOUSE = { id: 1, name: 'Sample Warehouse A', code: 'WH-A', catalogue: 'Garments' }

  var from = null
  try { from = new URLSearchParams(location.search).get('from') } catch (e) { /* old browser */ }
  window.WAREXX_DEMO = {
    // printed on the demo's own sample labels: a product, a carton, one piece
    samples: ['SMPL-00066', 'SMPL-B-00001', 'SMPL-00001-006'],
    contact: (from || 'https://warexx.aavoraa.com') + '#contact',
  }

  // A deep link that reached a host with no /m/… rewrite comes here as
  // /m/?go=/m/grn/12 (see demo/index.html); put the real address back before
  // the app reads it.
  try {
    var go = new URLSearchParams(location.search).get('go')
    if (go && /^\/m(\/|$)/.test(go)) history.replaceState(null, '', go)
  } catch (e) { /* stay on /m */ }

  try {
    if (!localStorage.getItem('essa_token')) localStorage.setItem('essa_token', 'demo')
    if (!localStorage.getItem('essa_user')) localStorage.setItem('essa_user', 'Sample Admin')
    if (!localStorage.getItem('essa_warehouse')) localStorage.setItem('essa_warehouse', JSON.stringify(START_WAREHOUSE))
  } catch (e) { /* private mode: the app opens on its sign-in screen */ }

  var mark = function () { document.body && document.body.classList.add('wxdemo') }
  if (document.body) mark(); else document.addEventListener('DOMContentLoaded', mark)

  window.__essaReady = (async function () {
    // The iPhone app ships the demo inside itself and runs the same sw.js in the
    // page (WKWebView cannot register a worker for bundled files) — it has set
    // this up before the page started; there is no worker to wait for.
    if (window.__warexxInPageServer) { await window.__warexxInPageServer; return }
    if (!('serviceWorker' in navigator)) {
      throw new Error('This browser cannot run the demo (no service workers).')
    }
    await navigator.serviceWorker.register('/sw.js', { scope: '/' })
    if (!navigator.serviceWorker.controller) {
      await new Promise(function (resolve) {
        navigator.serviceWorker.addEventListener('controllerchange', resolve, { once: true })
        setTimeout(resolve, 4000)
      })
      // the very first visit: reload once so the worker answers this page too
      if (!navigator.serviceWorker.controller) { location.reload(); await new Promise(function () {}) }
    }
  })()
  window.__essaReady.catch(function (e) {
    var a = document.getElementById('app')
    if (a) a.innerHTML = '<div class="center"><b>The demo could not start</b><p class="sub">' +
      String((e && e.message) || e) + '</p></div>'
  })
})()
