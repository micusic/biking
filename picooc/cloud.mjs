// Download your PICOOC (有品) cloud weight history into local files.
// Request signing and the bodyIndexList endpoint follow AlexxIT/SmartScaleConnect (pkg/picooc).
//
// usage: node cloud.mjs            (China server api2.picooc.com)
//        node cloud.mjs --intl     (international server api2.picooc-int.com)
//
// You type your phone/email and password here; the password is sent only to PICOOC's
// login endpoint and never written to disk. session.json keeps only user_id/role ids so a later
// run can refresh without logging in again (delete it to force a new login).
import { createHash, randomUUID } from 'node:crypto';
import { createInterface } from 'node:readline';
import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { fromEnv } from './session.mjs';

const here = dirname(fileURLToPath(import.meta.url));
const intl = process.argv.includes('--intl');
const API = intl ? 'https://api2.picooc-int.com/v1/api/' : 'https://api2.picooc.com/v1/api/';
const APP_VER = 'i4.1.11.0';
const SESSION = join(here, 'session.json');

const md5u = s => createHash('md5').update(s).digest('hex').toUpperCase();
let deviceId = randomUUID().toUpperCase();

function params(method) {
  const timestamp = String(Math.floor(Date.now() / 1000));
  const sign = md5u(deviceId + md5u(timestamp + md5u(method) + md5u(APP_VER)));
  return new URLSearchParams({
    appver: APP_VER, timestamp, lang: 'zh', method, timezone: '', sign,
    push_token: 'android::' + deviceId, device_id: deviceId,
  });
}

function ask(q) {
  return new Promise(res => {
    const rl = createInterface({ input: process.stdin, output: process.stdout, terminal: true });
    rl.question(q, a => { rl.close(); res(a.trim()); });
  });
}

// Read a secret one keypress at a time in raw mode so nothing typed is ever echoed.
function askHidden(q) {
  return new Promise((res, rej) => {
    const { stdin, stdout } = process;
    if (!stdin.isTTY) return rej(new Error('需要在交互式终端里运行'));
    stdout.write(q);
    let buf = '';
    stdin.setRawMode(true); stdin.resume(); stdin.setEncoding('utf8');
    const onData = chunk => {
      for (const ch of chunk) {
        if (ch === '\r' || ch === '\n') { done(); return res(buf); }
        if (ch === '\u0003') { done(); return rej(new Error('已取消')); }
        if (ch === '\u007f' || ch === '\b') { if (buf) { buf = buf.slice(0, -1); stdout.write('\b \b'); } continue; }
        buf += ch; stdout.write('*');
      }
    };
    const done = () => { stdin.off('data', onData); stdin.setRawMode(false); stdin.pause(); stdout.write('\n'); };
    stdin.on('data', onData);
  });
}

async function login() {
  const account = await ask('PICOOC 手机号或邮箱: ');
  const password = await askHidden('密码 (输入时显示为 *): ');
  const form = params('user_login_new');
  const isPhone = /^\+?\d{6,}$/.test(account);
  const req = {
    appver: form.get('appver'), timestamp: form.get('timestamp'), lang: form.get('lang'),
    method: form.get('method'), timezone: '', sign: form.get('sign'),
    push_token: form.get('push_token'), device_id: deviceId,
    req: {
      app_channel: '', app_version: APP_VER, email: isPhone ? '' : account, password,
      phone: isPhone ? account : '', phone_system: '', phone_type: '',
    },
  };
  form.set('reqData', JSON.stringify(req));
  const r = await fetch(API + 'account/login', {
    method: 'POST', headers: { 'content-type': 'application/x-www-form-urlencoded' }, body: form,
  });
  const j = await r.json();
  if (j.code !== 0) throw new Error(`登录失败: code=${j.code} ${j.msg ?? ''}`);
  const roles = { [j.resp.role_id]: '主账号' };
  for (const x of j.resp.roles ?? []) roles[x.role_id] = x.role_name || x.role_id;
  // Keep the whole login response (it carries the session token the China server wants) and the
  // response headers/cookies, locally only. session.json is a login credential: don't share it.
  const cookies = r.headers.getSetCookie?.() ?? [];
  const s = {
    server: API, device_id: deviceId, user_id: j.resp.user_id, roles,
    login_resp: j.resp, login_cookies: cookies.map(c => c.split(';')[0]),
    login_headers: Object.fromEntries([...r.headers].filter(([k]) => /token|auth|session/i.test(k))),
  };
  writeFileSync(SESSION, JSON.stringify(s, null, 2));
  console.log('登录返回的字段: ' + Object.keys(j.resp).join(', '));
  return s;
}

// The China server only answers requests shaped like its own app's (user-authorized): the iOS
// app 4.16.0 signs every call with method=picooc and appver 4.16.0.0, as captured from the
// user's own phone. downloadAllBodyIndex returns records older than `time`, newest first, up to
// `limit` per call, with leftCount = how many older records remain.
const CN_APP = { appver: '4.16.0.0', version: '4.16.0', method: 'picooc' };
const CN_HEADERS = { 'user-agent': 'Picooc/4.16.0 (iPhone; iOS 27.2; Scale/3.00)', 'accept-language': 'zh-Hans-CN;q=1' };

function cnParams(s, roleId) {
  const timestamp = String(Math.floor(Date.now() / 1000));
  const sign = md5u(s.device_id + md5u(timestamp + md5u(CN_APP.method) + md5u(CN_APP.appver)));
  const mainRoleId = String(s.login_resp?.role_id ?? roleId);
  return new URLSearchParams({
    appver: CN_APP.appver, device_id: s.device_id, device_mac: '', isMainRole: roleId === mainRoleId ? '1' : '0',
    lang: 'zh-CN', mainRoleId, method: CN_APP.method, os: 'iOS', push_token: 'iOS::' + s.device_id,
    roleId, sign, timestamp, timezone: 'Asia/Shanghai', userId: String(s.user_id), version: CN_APP.version,
  });
}

async function fetchRole(s, roleId) {
  const byId = new Map();
  let time = Math.floor(Date.now() / 1000) + 86400;
  for (let page = 1; page < 100; page++) {
    const p = cnParams(s, roleId);
    p.set('babyTime', '0'); p.set('limit', '1000'); p.set('time', String(time));
    const r = await fetch(API + 'bodyIndex/downloadAllBodyIndex?' + p, { headers: CN_HEADERS, redirect: 'manual' });
    if (r.status !== 200) throw new Error(`下载失败: HTTP ${r.status}`);
    const j = await r.json();
    if (j.code !== 0 && j.code !== 200) throw new Error(`下载失败: code=${j.code} ${j.msg ?? ''}`);
    const recs = j.resp?.bodyIndexList ?? [];
    for (const x of recs) byId.set(x.body_index_id, x);
    process.stdout.write(`  第 ${page} 页: ${recs.length} 条，还剩 ${j.resp?.leftCount ?? '?'} 条 (累计 ${byId.size})\n`);
    if (!recs.length || !j.resp?.leftCount) break;
    const oldest = Math.min(...recs.map(x => x.bodyTime));
    if (oldest >= time) break;
    time = oldest;
    await new Promise(res => setTimeout(res, 500)); // be gentle with their server
  }
  return [...byId.values()];
}

const COLS = [
  ['time', r => new Date(r.bodyTime * 1000).toLocaleString('sv-SE')],
  ['weight_kg', r => r.weight], ['bmi', r => r.bmi], ['fat_pct', r => r.body_fat],
  ['subcutaneous_fat_pct', r => r.subcutaneous_fat], ['visceral_fat', r => r.visceral_fat_level],
  ['muscle_pct', r => r.muscle_race], ['skeletal_muscle', r => r.skeletal_muscle],
  ['water_pct', r => r.water_race], ['bone_kg', r => r.bone_mass], ['bmr_kcal', r => r.basic_metabolism],
  ['body_age', r => r.body_age], ['impedance', r => r.electric_resistance],
  ['abnormal', r => r.abnormal_flag], ['deleted', r => r.is_del], ['manual', r => r.is_manually_add],
  ['scale_mac', r => r.mac], ['server_id', r => r.server_id],
];
const csvCell = v => (v === undefined || v === null) ? '' : /[",\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : String(v);

const loginOnly = process.argv.includes('--login-only');
const envSession = fromEnv(); // cloud routine: PICOOC_SESSION (see session.mjs)
let s = envSession ?? (existsSync(SESSION) ? JSON.parse(readFileSync(SESSION, 'utf8')) : null);
if (s && (s.server !== API || !s.login_resp)) s = null;
if (!s && envSession !== null) throw new Error('PICOOC_SESSION 与服务器不符');
if (!s && process.env.PICOOC_SESSION === undefined && !process.stdin.isTTY) throw new Error('没有登录会话：先在终端里运行 node cloud.mjs 登录');
if (loginOnly) { await login(); console.log('已保存登录会话到 session.json'); process.exit(0); }
if (!s) s = await login(); else deviceId = s.device_id;
console.log(`已登录 user_id=${s.user_id}，成员: ${Object.values(s.roles).join(', ')}`);

const raw = {};
for (const [roleId, name] of Object.entries(s.roles)) {
  console.log(`下载 ${name} (${roleId}) ...`);
  const recs = await fetchRole(s, roleId);
  raw[roleId] = recs;
  recs.sort((a, b) => a.bodyTime - b.bodyTime);
  const rows = [['role', ...COLS.map(c => c[0])].join(',')];
  for (const r of recs) rows.push([csvCell(name), ...COLS.map(c => csvCell(c[1](r)))].join(','));
  const file = join(here, `cloud_${roleId}.csv`);
  writeFileSync(file, '﻿' + rows.join('\n') + '\n');
  const first = recs[0], last = recs.at(-1);
  console.log(`  ${recs.length} 条 → ${file}` + (first ? `  (${new Date(first.bodyTime * 1000).toLocaleDateString('sv-SE')} ~ ${new Date(last.bodyTime * 1000).toLocaleDateString('sv-SE')})` : ''));
}
writeFileSync(join(here, 'cloud_raw.json'), JSON.stringify(raw, null, 1));
console.log('完成。原始数据: cloud_raw.json');
