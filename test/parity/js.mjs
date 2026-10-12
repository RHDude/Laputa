import { setLang, evText } from '../../ui/src/i18n.js';
import fs from 'fs';
const evs = JSON.parse(fs.readFileSync('' + process.argv[2] + '/evs.json', 'utf8'));
for (const l of ['ru', 'en', 'uk', 'de', 'es', 'zh']) { setLang(l); fs.writeFileSync(process.argv[2] + '/js-' + l + '.txt', evs.map((e) => { const x = evText(e); return x.title + '\t' + x.text; }).join('\n') + '\n'); }
