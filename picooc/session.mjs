// Where cloud.mjs / report.mjs get the PICOOC session from.
//
// Locally: session.json (written by cloud.mjs on login; never commit it).
// In the cloud review routine: the PICOOC_SESSION environment variable, holding only what downloading
// needs (server, device id, user id, role ids) as base64 JSON: no phone number, no third-party tokens.
//
// usage: node session.mjs --copy   copy that minimal session to the clipboard for the cloud environment
//                                  (nothing is printed)
import { readFileSync, existsSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
export const SESSION_FILE = join(here, 'session.json');

export function fromEnv() {
  const v = process.env.PICOOC_SESSION;
  if (!v) return null;
  const s = JSON.parse(Buffer.from(v.trim(), 'base64').toString('utf8'));
  if (!s.server || !s.device_id || !s.user_id || !s.roles || !s.login_resp?.role_id) throw new Error('PICOOC_SESSION 缺少字段');
  return s;
}

export function minimal(s) {
  return { server: s.server, device_id: s.device_id, user_id: s.user_id, roles: s.roles,
    login_resp: { role_id: s.login_resp.role_id } };
}

export function loadSession() {
  return fromEnv() ?? (existsSync(SESSION_FILE) ? JSON.parse(readFileSync(SESSION_FILE, 'utf8')) : null);
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1] && process.argv.includes('--copy')) {
  if (!existsSync(SESSION_FILE)) { console.error('没有 session.json，先运行 node cloud.mjs 登录'); process.exit(1); }
  const b64 = Buffer.from(JSON.stringify(minimal(JSON.parse(readFileSync(SESSION_FILE, 'utf8'))))).toString('base64');
  execFileSync('clip', { input: b64 });
  console.log('已复制到剪贴板（未显示内容）。粘贴到云端环境 Personal 的环境变量 PICOOC_SESSION。');
}
