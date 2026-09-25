import fs from 'fs';
const out = {};
for (const style of ['open-peeps', 'notionists']) {
  const base = `./node_modules/@dicebear/${style}/lib`;
  const idx = await import(`${base}/index.js`);
  const comps = await import(`${base}/components/index.js`);
  const src = fs.readFileSync(`${base}/index.js`, 'utf8');
  const m = src.match(/body: `([\s\S]*?)`,\n\s*extra/);
  let body = m[1]
    .replace(/\$\{\(_\w+ = \(_\w+ = components\.(\w+)\)[^}]*?\}/g, '@@slot:$1@@')
    .replace(/\$\{escape\.xml\(`\$\{colors\.(\w+)\}`\)\}/g, '@@color:$1@@');
  if (body.includes('${')) throw new Error(style + ' body left ${');
  const colorsProxy = new Proxy({}, { get: (_, k) => `@@color:${String(k)}@@` });
  // A part may draw another part inside it (Notionists: the badge sits on the outfit).
  const componentsProxy = new Proxy({}, { get: (_, k) => ({ value: () => `@@slot:${String(k)}@@` }) });
  const slots = {};
  for (const [name, group] of Object.entries(comps)) {
    slots[name] = {};
    for (const [variant, fn] of Object.entries(group)) {
      const svg = fn(componentsProxy, colorsProxy);
      if (svg.includes('undefined') || svg.includes('${')) throw new Error(`${style} ${name} ${variant}`);
      slots[name][variant] = svg;
    }
  }
  const p = idx.schema.properties;
  const colors = {}, probabilities = {};
  for (const [k, v] of Object.entries(p)) {
    if (k.endsWith('Color') && Array.isArray(v.default)) colors[k.slice(0, -5)] = v.default;
    if (k.endsWith('Probability')) probabilities[k.slice(0, -11)] = v.default;
  }
  const vb = src.match(/viewBox: '([^']+)'/)[1];
  out[style] = { meta: idx.meta, viewBox: vb, body, slots, colors, probabilities };
  const used = [...new Set([...body.matchAll(/@@color:(\w+)@@/g), ...JSON.stringify(slots).matchAll(/@@color:(\w+)@@/g)].map(x => x[1]))];
  console.log(style, 'slots', Object.fromEntries(Object.entries(slots).map(([k, v]) => [k, Object.keys(v).length])), 'colors used', used, 'palettes', Object.keys(colors), 'prob', probabilities);
}
// The module loads open_peeps.json / notionists.json (underscore, like its style keys).
for (const [k, v] of Object.entries(out)) fs.writeFileSync(`${k.replace('-', '_')}.json`, JSON.stringify(v));
