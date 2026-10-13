// The panel's icon sprite (d-7985ee-01ad81): one <symbol> per name in src/web/assets/icons.txt,
// taken from Lucide's own package (lucide-static, ISC), with its license next to it. `ui.icon(name)`
// uses it; tests/test_web_style.py checks that every icon a template uses is listed.
import { copyFileSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";

const DIST = "src/web/static/dist";
const LUCIDE = "node_modules/lucide-static";

const names = readFileSync("src/web/assets/icons.txt", "utf8").split(/\s+/).filter(Boolean);
const symbols = names.map((name) => {
  const svg = readFileSync(`${LUCIDE}/icons/${name}.svg`, "utf8");
  const inner = svg.slice(svg.indexOf(">", svg.indexOf("<svg")) + 1, svg.lastIndexOf("</svg>")).trim();
  return `<symbol id="${name}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ` +
    `stroke-linecap="round" stroke-linejoin="round">${inner}</symbol>`;
});

mkdirSync(DIST, { recursive: true });
writeFileSync(`${DIST}/icons.svg`,
  `<svg xmlns="http://www.w3.org/2000/svg"><!-- Lucide icons, ISC: /static/dist/LICENSE-lucide.txt -->${symbols.join("")}</svg>\n`);
copyFileSync(`${LUCIDE}/LICENSE`, `${DIST}/LICENSE-lucide.txt`);
console.log(`icons.svg: ${names.length} icons`);
