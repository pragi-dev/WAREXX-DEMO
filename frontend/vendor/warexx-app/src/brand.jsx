// The WAREXX mark and wordmark — the official logo artwork (assets/logo-*.png,
// cut from the brand file with the black ground made transparent). Each comes in
// two inks: as drawn, with a white roof and white "WARE" for the black chrome,
// and `onLight`, where the white parts are the logo's ink black so they still
// show on a white card. The orange is never changed.
import markImg from './assets/logo-mark.png'
import markInk from './assets/logo-mark-ink.png'
import wordImg from './assets/logo-word.png'
import wordInk from './assets/logo-word-ink.png'
import wordTagImg from './assets/logo-word-tag.png'
import wordTagInk from './assets/logo-word-tag-ink.png'

export function BrandMark({ className = '', size, title, onLight = false }) {
  return (
    <img className={'brandmark ' + className} src={onLight ? markInk : markImg}
      width={size} height={size ? Math.round(size * 591 / 620) : undefined}
      alt={title || ''} aria-hidden={title ? undefined : 'true'} draggable="false" />
  )
}

// Sized by its HEIGHT in CSS (.wordmark { font-size }) as before: the lettering
// is one em tall, the version with "BY AAVORAA" under it about 1.85em.
export function Wordmark({ className = '', tagline = false, onLight = false }) {
  const src = tagline ? (onLight ? wordTagInk : wordTagImg) : (onLight ? wordInk : wordImg)
  return (
    <span className={'wordmark' + (tagline ? ' with-tag' : '') + (className ? ' ' + className : '')}>
      <img className="wm-img" src={src} alt="WAREXX" draggable="false" />
    </span>
  )
}
