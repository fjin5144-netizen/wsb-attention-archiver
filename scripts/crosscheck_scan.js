#!/usr/bin/env node
/* Check app.html's spike scan against the Python, at every definition in a reference
 * file written by crosscheck_scan.py.
 *
 *     python3 scripts/crosscheck_scan.py /tmp/scan_ref.json
 *     node scripts/crosscheck_scan.js /tmp/scan_ref.json
 *
 * The functions are not copied out of app.html — they are *run* out of it. The whole
 * <script> is evaluated inside a small sandbox and the pieces that matter are handed
 * back, so this cannot pass while testing a stale copy of code that has since changed,
 * which is the failure mode a copied-out version would have.
 *
 * The page's boot sequence runs too, and is expected to fail: its first act is to fetch
 * data/days.json, and the stub below rejects. That happens after an await, so every
 * top-level declaration is already in place by the time the rejection lands.
 */
const fs = require('fs')
const path = require('path')

const ROOT = path.dirname(__dirname)
const refPath = process.argv[2]
if (!refPath) {
  console.error('usage: node scripts/crosscheck_scan.js <reference.json>')
  process.exit(2)
}

process.on('unhandledRejection', () => {})   // the boot IIFE, see above

const html = fs.readFileSync(path.join(ROOT, 'app.html'), 'utf8')
const m = html.match(/<script>([\s\S]*)<\/script>/)
if (!m) { console.error('no <script> in app.html'); process.exit(2) }

const noop = () => {}
const el = { textContent: '', innerHTML: '', hidden: false, classList: { toggle: noop, add: noop, remove: noop },
             setAttribute: noop, getAttribute: () => null, querySelector: () => null,
             querySelectorAll: () => [], addEventListener: noop, remove: noop, focus: noop }
const sandbox = {
  document: { getElementById: () => el, querySelector: () => null, querySelectorAll: () => [],
              addEventListener: noop, createElement: () => el, body: el, documentElement: el },
  window: { addEventListener: noop, matchMedia: () => ({ matches: false, addEventListener: noop }) },
  location: { search: '', pathname: '/', href: '/' },
  history: { pushState: noop, replaceState: noop },
  localStorage: { getItem: () => null, setItem: noop },
  fetch: () => Promise.reject(new Error('offline by design')),
  requestAnimationFrame: noop,
}

/* The trailing expression hands back the entities under test plus one setter, which
 * closes over the module's own `let` bindings — that is how the archive gets in without
 * the functions taking it as an argument and drifting from the page's version. */
const factory = new Function(...Object.keys(sandbox), `
${m[1]}
;return {
  scanEvents, scanFinding, sha256hex, pyRound, SPK0,
  load(days, snaps, prices){
    DAYS = days; PRICES = prices;
    for (const k in MAPS) delete MAPS[k];
    for (const k in snaps) SNAP[k] = snaps[k];
  },
};`)
const app = factory(...Object.values(sandbox))

if (sha256check(app) !== true) process.exit(1)

const days = JSON.parse(fs.readFileSync(path.join(ROOT, 'data/days.json'), 'utf8'))
const prices = JSON.parse(fs.readFileSync(path.join(ROOT, 'data/prices.json'), 'utf8'))
const snaps = {}
for (const d of days) {
  const p = path.join(ROOT, 'data/apewisdom', d + '.json')
  if (fs.existsSync(p)) snaps[d] = JSON.parse(fs.readFileSync(p, 'utf8'))
}
app.load(days, snaps, prices)

const ref = JSON.parse(fs.readFileSync(refPath, 'utf8'))
const FIELDS = ['n', 'median', 'trimmed_mean', 'mean', 'win_rate']
let checked = 0
const bad = []

for (const key of Object.keys(ref)) {
  const [floor, mult, look, gap] = key.split('-').map(Number)
  const events = app.scanEvents({ floor, mult, look, gap })
  const h = app.scanFinding(events, [20]).horizons[0] || {}
  const R = ref[key]
  const diffs = []

  const mine = events.map(e => e.tk + '|' + e.d).sort()
  if (mine.length !== R.keys.length || mine.some((x, i) => x !== R.keys[i])) {
    const a = new Set(mine), b = new Set(R.keys)
    diffs.push(`event set: ${mine.length} vs ${R.keys.length}` +
      ` · only in js ${[...a].filter(x => !b.has(x)).slice(0, 3)}` +
      ` · only in py ${[...b].filter(x => !a.has(x)).slice(0, 3)}`)
  }
  if (R.gap_median !== h.gap_median) diffs.push(`gap_median: py ${R.gap_median} js ${h.gap_median}`)
  for (const side of ['spike', 'placebo']) {
    for (const f of FIELDS) {
      const p = R[side] && R[side][f], j = h[side] && h[side][f]
      if (p !== j) diffs.push(`${side}.${f}: py ${p} js ${j}`)
    }
  }
  checked++
  if (diffs.length) bad.push({ key, diffs })
}

function sha256check(a) {
  const want = 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'
  const got = a.sha256hex('abc')
  if (got !== want) { console.error(`sha256hex('abc') = ${got}\n  expected ${want}`); return false }
  /* Multi-block and non-ASCII, because the padding maths is where a hand-written digest
   * goes wrong, and every placebo pick runs through it. */
  const crypto = require('crypto')
  for (const s of ['', 'a'.repeat(55), 'a'.repeat(56), 'a'.repeat(64), 'a'.repeat(200),
                   'BRK.B|2026-08-09|20', '证券|2021-05-10|5']) {
    const ours = a.sha256hex(s), theirs = crypto.createHash('sha256').update(s, 'utf8').digest('hex')
    if (ours !== theirs) { console.error(`sha256hex(${JSON.stringify(s)}) diverges:\n  ${ours}\n  ${theirs}`); return false }
  }
  if (a.pyRound(12.5) !== 12 || a.pyRound(13.5) !== 14 || a.pyRound(-12.5) !== -12) {
    console.error('pyRound does not break ties to even like Python round()'); return false
  }
  return true
}

if (bad.length) {
  console.error(`${bad.length} of ${checked} definitions disagree with the Python:\n`)
  for (const b of bad.slice(0, 10)) console.error(`  ${b.key}\n    ${b.diffs.join('\n    ')}`)
  process.exit(1)
}
console.log(`app.html reproduces the Python at all ${checked} definitions ` +
            `(event sets and every describe() field, both sides)`)
