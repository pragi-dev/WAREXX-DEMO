// A warehouse aisle in one-point perspective — racking in the brand's orange,
// cartons on every shelf, lights down the ceiling. Drawn rather than shipped as
// a photograph: it scales to any card, costs a few KB, and works offline on a
// warehouse tablet the same as on the demo.
//
// Everything is placed by depth (z): a point u pixels below the horizon at z=1
// sits u/z below it at depth z, which is all a vanishing point is.
import React from 'react'

let seq = 0
const VX = 200, VY = 92                          // the vanishing point
const ZS = [1, 1.3, 1.7, 2.2, 2.9, 3.8, 5, 6.6, 8.8, 12]
const LEVELS = [138, 82, 26, -30, -86]          // floor … top beam, at z=1
const X = (s, z) => VX + s * 205 / z
const Y = (u, z) => VY + u / z
// deterministic "random", so the cartons do not reshuffle on every render
const rnd = (n) => { const x = Math.sin(n * 127.1 + 311.7) * 43758.5453; return x - Math.floor(x) }
const CARTON = ['#C98A4E', '#B87A40', '#D79B5C', '#A8692F', '#E0AA6C']

function quad(pts) { return pts.map((p) => p.join(',')).join(' ') }

export function WarehouseArt({ className = '', dim = false }) {
  const id = 'wa' + (++seq)
  const sides = [-1, 1]
  const boxes = []
  const racks = []
  sides.forEach((s) => {
    for (let i = 0; i < ZS.length - 1; i++) {
      const z0 = ZS[i], z1 = ZS[i + 1]
      for (let l = 0; l < LEVELS.length - 1; l++) {
        const lo = LEVELS[l], hi = LEVELS[l + 1]
        // two or three cartons per bay, each a slice of the bay in 1/z space
        const n = 2 + Math.floor(rnd(i * 7 + l * 3 + (s > 0 ? 50 : 0)) * 2)
        const w0 = 1 / z0, w1 = 1 / z1
        for (let b = 0; b < n; b++) {
          const k = i * 31 + l * 7 + b + (s > 0 ? 400 : 0)
          if (rnd(k) < 0.12) continue                    // an empty slot now and then
          const a = b / n + 0.012, c = (b + 1) / n - 0.012
          const za = 1 / (w0 + (w1 - w0) * a), zc = 1 / (w0 + (w1 - w0) * c)
          const top = lo + (hi - lo) * (0.62 + rnd(k + 9) * 0.3)
          const fill = CARTON[Math.floor(rnd(k + 3) * CARTON.length)]
          boxes.push(
            <polygon key={`b${s}${i}${l}${b}`} fill={fill}
              opacity={Math.max(0.35, 1 - i * 0.075)}
              points={quad([[X(s, za), Y(top, za)], [X(s, zc), Y(top, zc)],
                [X(s, zc), Y(lo - 2, zc)], [X(s, za), Y(lo - 2, za)]])} />,
            // the tape across each carton
            <line key={`t${s}${i}${l}${b}`} stroke="rgba(255,240,220,.35)" strokeWidth={1.4 / za}
              x1={X(s, za)} y1={Y((top + lo) / 2, za)} x2={X(s, zc)} y2={Y((top + lo) / 2, zc)} />)
        }
      }
    }
    // uprights at every bay, beams along every level
    ZS.forEach((z, i) => racks.push(
      <line key={`u${s}${i}`} x1={X(s, z)} y1={Y(LEVELS[4] - 8, z)} x2={X(s, z)} y2={Y(LEVELS[0], z)}
        stroke={`url(#${id}r)`} strokeWidth={Math.max(1, 7 / z)} />))
    LEVELS.forEach((u, l) => racks.push(
      <line key={`l${s}${l}`} x1={X(s, ZS[0])} y1={Y(u, ZS[0])} x2={X(s, ZS[ZS.length - 1])}
        y2={Y(u, ZS[ZS.length - 1])} stroke="#E8731A" strokeWidth={l === 0 ? 2 : 3.2}
        opacity={l === 0 ? 0.4 : 0.95} />))
  })
  const far = ZS[ZS.length - 1]
  const lights = ZS.slice(0, -1).map((z, i) => (
    <rect key={'li' + i} x={VX - 22 / z} y={Y(-128, z) - 2 / z} width={44 / z} height={Math.max(1, 5 / z)}
      rx={2 / z} fill="#FFF6E6" opacity={0.95 - i * 0.06} />))
  return (
    <svg className={'wh-art ' + className} viewBox="0 0 400 240" preserveAspectRatio="xMidYMid slice"
      aria-hidden="true" focusable="false">
      <defs>
        <linearGradient id={id + 'bg'} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#2B241E" /><stop offset=".55" stopColor="#3A3027" />
          <stop offset="1" stopColor="#1C1814" />
        </linearGradient>
        <linearGradient id={id + 'fl'} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#8B8178" /><stop offset="1" stopColor="#4A433D" />
        </linearGradient>
        <radialGradient id={id + 'gl'} cx=".5" cy=".38" r=".5">
          <stop offset="0" stopColor="#FFF4DF" stopOpacity=".95" />
          <stop offset=".35" stopColor="#FFD9A6" stopOpacity=".45" />
          <stop offset="1" stopColor="#FFD9A6" stopOpacity="0" />
        </radialGradient>
        <linearGradient id={id + 'r'} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#F7931E" /><stop offset="1" stopColor="#C9520F" />
        </linearGradient>
      </defs>
      <rect width="400" height="240" fill={`url(#${id}bg)`} />
      {/* the far end of the aisle, lit */}
      <rect x={X(-1, far)} y={Y(LEVELS[4] - 30, far)} width={X(1, far) - X(-1, far)}
        height={Y(LEVELS[0], far) - Y(LEVELS[4] - 30, far)} fill="#F3E7D6" />
      <ellipse cx={VX} cy={VY} rx="120" ry="80" fill={`url(#${id}gl)`} />
      <polygon fill={`url(#${id}fl)`}
        points={quad([[X(-1, ZS[0]), Y(LEVELS[0], ZS[0])], [X(-1, far), Y(LEVELS[0], far)],
          [X(1, far), Y(LEVELS[0], far)], [X(1, ZS[0]), Y(LEVELS[0], ZS[0])], [400, 240], [0, 240]])} />
      {/* the floor's sheen and its painted lane */}
      <polygon fill="#FFF3E0" opacity=".14"
        points={quad([[VX - 50, 240], [VX - 4, Y(LEVELS[0], far)], [VX + 4, Y(LEVELS[0], far)], [VX + 50, 240]])} />
      <line x1={X(-0.55, ZS[0])} y1={240} x2={X(-0.55, far)} y2={Y(LEVELS[0], far)} stroke="#F2B132" strokeWidth="2.2" opacity=".75" />
      <line x1={X(0.55, ZS[0])} y1={240} x2={X(0.55, far)} y2={Y(LEVELS[0], far)} stroke="#F2B132" strokeWidth="2.2" opacity=".75" />
      {boxes}
      {racks}
      {lights}
      {dim && <rect width="400" height="240" fill="#0E0C0A" opacity=".35" />}
    </svg>
  )
}
