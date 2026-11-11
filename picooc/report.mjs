// Merge PICOOC cloud history (cloud_raw.json) with local Bluetooth readings (weights.csv)
// into data.json + a self-contained report.html (no network needed to view it).
// usage: node report.mjs            then open report.html
import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { loadSession } from './session.mjs';

const here = dirname(fileURLToPath(import.meta.url));
const session = loadSession() ?? { roles: {}, login_resp: {} };
const heightCm = 178; // from your profile; used for BMI on Bluetooth-only rows

const rows = [];
const raw = existsSync(join(here, 'cloud_raw.json')) ? JSON.parse(readFileSync(join(here, 'cloud_raw.json'), 'utf8')) : {};
for (const [roleId, recs] of Object.entries(raw)) {
  for (const r of recs) {
    if (r.is_del) continue;
    const comp = r.body_fat > 0;
    rows.push({
      t: r.bodyTime, role: session.roles[roleId] ?? roleId, src: 'cloud',
      weight: r.weight, bmi: r.bmi || +(r.weight / (heightCm / 100) ** 2).toFixed(1),
      fat: comp ? r.body_fat : null, subFat: comp ? r.subcutaneous_fat : null,
      visceral: comp ? r.visceral_fat_level : null, muscle: comp ? r.muscle_race : null,
      water: comp ? r.water_race : null, bone: comp ? r.bone_mass : null,
      bmr: comp ? r.basic_metabolism : null, bodyAge: comp && r.body_age ? r.body_age : null,
      imp: r.electric_resistance || null, abnormal: r.abnormal_flag ? 1 : 0,
    });
  }
}
// Bluetooth readings taken by picooc.exe go to weights.csv and never reach the cloud.
const csv = join(here, 'weights.csv');
if (existsSync(csv)) {
  const lines = readFileSync(csv, 'utf8').replace(/^﻿/, '').trim().split(/\r?\n/).slice(1);
  for (const line of lines) {
    const [time, w, imp] = line.split(',');
    const t = Math.floor(new Date(time.replace(' ', 'T') + '+08:00').getTime() / 1000);
    if (!(+w > 0) || rows.some(x => Math.abs(x.t - t) < 600 && Math.abs(x.weight - w) < 0.3)) continue;
    rows.push({ t, role: session.roles[session.login_resp?.role_id] ?? '本人', src: 'bluetooth', weight: +w,
      bmi: +(w / (heightCm / 100) ** 2).toFixed(1), fat: null, subFat: null, visceral: null, muscle: null,
      water: null, bone: null, bmr: null, bodyAge: null, imp: +imp > 0 ? +imp : null, abnormal: 0 });
  }
}
rows.sort((a, b) => a.t - b.t);

// Readings you know are off (clothes, shoes, someone else...) go in exclude.json as
// { "time": "YYYY-MM-DD HH:MM", "reason": "..." }; they stay in the table but leave the analysis.
const exFile = join(here, 'exclude.json');
if (existsSync(exFile)) {
  for (const ex of JSON.parse(readFileSync(exFile, 'utf8'))) {
    const t = Math.floor(new Date(ex.time.replace(' ', 'T') + '+08:00').getTime() / 1000);
    for (const r of rows) if (Math.abs(r.t - t) < 120) r.excluded = ex.reason || '手动排除';
  }
}
writeFileSync(join(here, 'data.json'), JSON.stringify(rows, null, 1));

// Flat merged CSV for other tools (Excel, a cycling analysis, ...). Times are Beijing time.
const cell = v => v == null ? '' : /[",\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : String(v);
const tod = t => { const h = new Date((t + 8 * 3600) * 1000).getUTCHours(); /* Beijing time */ return h >= 5 && h < 11 ? 'morning' : h >= 16 ? 'evening' : 'other'; };
const cols = ['time', 'unix_time', 'time_of_day', 'weight_kg', 'bmi', 'fat_pct', 'fat_kg', 'lean_kg', 'subcutaneous_fat_pct',
  'visceral_fat', 'muscle_pct', 'water_pct', 'bone_kg', 'bmr_kcal', 'body_age', 'impedance_ohm', 'source', 'abnormal', 'excluded_reason'];
const lines = [cols.join(',')];
for (const r of rows) {
  const fatKg = r.fat != null ? +(r.weight * r.fat / 100).toFixed(2) : null;
  lines.push([new Date(r.t * 1000).toLocaleString('sv-SE', { timeZone: 'Asia/Shanghai' }), r.t, tod(r.t), r.weight, r.bmi, r.fat,
    fatKg, fatKg != null ? +(r.weight - fatKg).toFixed(2) : null, r.subFat, r.visceral, r.muscle, r.water, r.bone, r.bmr,
    r.bodyAge, r.imp, r.src, r.abnormal, r.excluded ?? ''].map(cell).join(','));
}
writeFileSync(join(here, 'body_data.csv'), '﻿' + lines.join('\n') + '\n');

const html = readFileSync(join(here, 'report.template.html'), 'utf8')
  .replace('/*__DATA__*/[]', JSON.stringify(rows))
  .replace('__HEIGHT__', String(heightCm))
  .replace('__GENERATED__', new Date().toLocaleString('sv-SE'));
writeFileSync(join(here, 'report.html'), html);
console.log(`${rows.length} 条记录 (${rows.filter(r => r.src === 'cloud').length} 云端 + ${rows.filter(r => r.src === 'bluetooth').length} 蓝牙) → report.html`);
