// Open the page in headless Chromium (over http and from file://) and compare its answers,
// computed by Relate's WebAssembly build in the page, with the CLI's for every person (and the graph's labels with the report's).
// usage (from the repository folder, with Playwright and Chromium installed):
//   python tests/web/expected.py demo/offboarding.rl > expected.json
//   node tests/web/check.cjs docs/index.html expected.json 8765
const { chromium } = require('playwright');
const fs = require('fs'), path = require('path'), http = require('http');
(async () => {
  const [page_, expected_, port] = process.argv.slice(2);
  const want = JSON.parse(fs.readFileSync(expected_, 'utf8'));
  const dir = path.dirname(path.resolve(page_)), name = path.basename(page_);
  const srv = http.createServer((q, s) => { const f = path.join(dir, decodeURIComponent(q.url.split('?')[0]).replace(/^\/$/, '/' + name));
    fs.readFile(f, (e, b) => { if (e) { s.writeHead(404); s.end(); } else { s.writeHead(200, { 'content-type': 'text/html; charset=utf-8' }); s.end(b); } }); }).listen(+port);
  const b = await chromium.launch();
  let bad = 0;
  for (const url of [`http://127.0.0.1:${port}/`, 'file://' + path.resolve(page_)]) {
    const p = await b.newPage(); const errs = [];
    p.on('pageerror', (e) => errs.push(e.message)); p.on('console', (m) => m.type() === 'error' && errs.push(m.text()));
    const t0 = Date.now(); await p.goto(url); await p.waitForFunction(() => window.offgraph, null, { timeout: 60000 });
    const ready = await p.evaluate(() => window.offgraph.ready);
    console.log(url.slice(0, 30), 'ready', ready, 'in', Date.now() - t0, 'ms', 'status:', await p.textContent('#status'), errs.length ? 'errors: ' + errs.join(' | ') : '');
    if (!ready) { bad++; continue; }
    const got = await p.evaluate(() => {
      const o = {}; for (const { email } of window.offgraph.people) {
        const w = window.offgraph.whatif([email]);
        const svg = window.offgraph.svg(email, window.offgraph.reach(email), window.offgraph.ask('orphans', email),
            window.offgraph.ask('sole_admin', email).map((r) => r.group_name), window.offgraph.ask('memberships', email).map((r) => r.group_name));
        const tx = [...svg.matchAll(/>([^<>]+)<\/text>/g)].map((m) => m[1].replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#39;/g, "'")).sort();
        o[email] = { svg_text: tx, reach: window.offgraph.reach(email).map((r) => [r.resource, r.access, r.via, r.path]),
          checklist: window.offgraph.checklist(email),
          whatif: { already_left: w.already_left, unowned: w.unowned.map((r) => r.resource), no_admin_groups: w.no_admin_groups,
                    no_editor: w.no_editor.map((r) => r.resource), no_admin: w.no_admin.map((r) => r.resource), seats_usd: w.seats_usd } }; }
      return o; });
    for (const e of Object.keys(want)) for (const k of Object.keys(want[e]))
      if (JSON.stringify(got[e]?.[k]) !== JSON.stringify(want[e][k])) { bad++; if (bad < 6) console.log('DIFF', e, k, JSON.stringify(got[e]?.[k]).slice(0, 200), '!=', JSON.stringify(want[e][k]).slice(0, 200)); }
    console.log('people compared:', Object.keys(want).length, 'differences so far:', bad);
    await p.close();
  }
  await b.close(); srv.close(); process.exit(bad ? 1 : 0);
})();
