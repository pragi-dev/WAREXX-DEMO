import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'
import { DemoChrome, DemoLogin, demoSession } from './demo.jsx'
import { session } from './api.js'
import './styles.css'
import './design-system.css'
import './ui/warexx.css'
import './ui/screens.css'

// This app is never a frame's content. It PUTS the shop in a frame; nothing puts
// it in one.
//
// So finding ourselves inside one means a request for a POS screen came back as
// the warehouse instead — a routing rule that missed, a login redirect that lost
// its prefix. Rendering anyway is what turned that into three stacked copies of
// the header: the app draws its own POS frame, which asks for a POS screen, which
// comes back as the app, which draws another frame. The browser gives up after a
// few and leaves a page that looks like a display bug rather than a wrong answer
// from the server.
//
// Refusing to mount stops it at the first one and says what actually happened,
// which is the difference between a fault somebody can act on and a fault that
// looks like CSS.
const framed = (() => {
  try {
    return window.top !== window.self
  } catch {
    // cross-origin parent: reading window.top throws, and a parent we are not
    // allowed to look at is certainly not us
    return true
  }
})()

if (framed) {
  document.getElementById('root').innerHTML = `
    <div style="font:14px/1.65 system-ui,sans-serif;color:#1A1814;padding:28px;max-width:640px">
      <h2 style="margin:0 0 10px;font-size:17px">This is the warehouse app, in a frame</h2>
      <p style="margin:0 0 12px">
        Something asked for a <b>POS</b> page and got this instead, so the shop is
        not what loaded here. It is a routing fault on the server, not a problem
        with this screen.
      </p>
      <p style="margin:0 0 12px">
        The warehouse app does not run inside a frame — it is the thing that opens
        one — so it has stopped rather than loading itself over and over.
      </p>
      <p style="margin:0">
        <a href="/" target="_top" style="color:#C2560F;font-weight:600">Open the warehouse ↗</a>
      </p>
    </div>`
} else {
  // The demo sandbox (src/demo.jsx): /d/<slug> is a booked demo's own page, and
  // a demo session gets its countdown and end-of-demo view beside the app. On
  // every other deployment neither branch is ever taken.
  const demoSlug = (window.location.pathname.match(/^\/d\/([a-z0-9]+)\/?$/) || [])[1]
  const demo = demoSession.get()
  // A demo whose token is gone (signed out, or the window closed) belongs on its
  // own page: the ordinary login screen does not open anything on the demo host.
  if (!demoSlug && demo?.slug && !session.get()) window.location.replace(`/d/${demo.slug}`)
  // The public landing page is no longer part of this app: it is its own
  // deployment (landing-demo/, e.g. https://warexx.aavoraa.com), and this app
  // opens straight on its sign-in screen.
  ReactDOM.createRoot(document.getElementById('root')).render(
    <React.StrictMode>
      {demoSlug ? <DemoLogin slug={demoSlug} /> : (
        <>
          <App />
          {demo && <DemoChrome />}
        </>
      )}
    </React.StrictMode>,
  )
}
