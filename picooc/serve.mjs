// Tiny local server for report.html (localhost only).
import { createServer } from 'node:http';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
const here = dirname(fileURLToPath(import.meta.url));
createServer((req, res) => {
  try { res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' }); res.end(readFileSync(join(here, 'report.html'))); }
  catch { res.writeHead(404); res.end(); }
}).listen(8765, '127.0.0.1', () => console.log('http://127.0.0.1:8765'));
