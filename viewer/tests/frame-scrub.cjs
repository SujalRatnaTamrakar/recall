// Real browser/decoder regression: scrubbing uses source video and stays at
// or before both the replay clock and processing frontier. API data is fake.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { chromium } = require('playwright');

(async () => {
  const root = path.resolve(__dirname, '..');
  const base = process.env.UI_BASE_URL || 'http://127.0.0.1:8765';
  const video = path.join(root, '.qa', 'source.mp4');
  fs.mkdirSync(path.dirname(video), { recursive: true });
  if (!fs.existsSync(video)) execFileSync('ffmpeg', ['-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=30', '-t', '8', '-an', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', '-y', video, '-loglevel', 'error']);
  const bytes = fs.readFileSync(video);
  let playback = 8;
  let processed = 8;
  const browser = await chromium.launch({ headless: true, channel: process.env.BROWSER_CHANNEL || 'chrome' });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1100 } });
    await context.route('**/*', async route => {
      const req = route.request();
      const url = new URL(req.url());
      if (url.origin !== new URL(base).origin) return route.abort();
      const json = body => route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
      if (url.pathname === '/video') {
        const range = req.headers().range?.match(/^bytes=(\d+)-(\d*)$/);
        if (!range) return route.fulfill({ contentType: 'video/mp4', body: bytes, headers: { 'accept-ranges': 'bytes' } });
        const start = Number(range[1]);
        const end = range[2] ? Math.min(Number(range[2]), bytes.length - 1) : bytes.length - 1;
        return route.fulfill({ status: 206, contentType: 'video/mp4', body: bytes.subarray(start, end + 1), headers: { 'accept-ranges': 'bytes', 'content-range': `bytes ${start}-${end}/${bytes.length}` } });
      }
      if (url.pathname === '/api/status') return json({ run_id: 'scrub', mode: 'replay', title: 'Synthetic test', duration_s: 8, has_video: true, playback_s: playback, processed_through_s: processed, audio_through_s: processed, vision_through_s: processed, state: 'paused', csrf_token: 'test' });
      if (url.pathname === '/api/memory') return json({ run_id: 'scrub', through_s: Math.min(playback, processed), frames: [], records: [], server: { has_video: true, video_url: '/video' } });
      if (url.pathname === '/api/shared') return json({ run_id: 'scrub', rotation: 0, selection: 0, answer: null, controls_enabled: true, revision: 0 });
      if (url.pathname.startsWith('/api/')) return json({ ok: true });
      return route.continue();
    });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(base);
    const readout = page.locator('#cubeCanvas [role=status]');
    await page.waitForFunction(() => document.querySelector('#cubeCanvas [role=status]')?.dataset.frameSource === 'video');
    await page.locator('#timeline').fill('2.123');
    await page.waitForFunction(() => Math.abs(Number(document.querySelector('#cubeCanvas [role=status]')?.dataset.frameTime) - 2.123) < .1);
    assert.equal(await readout.getAttribute('data-frame-source'), 'video');
    playback = 1.5;
    processed = 5;
    await page.waitForFunction(() => Number(document.querySelector('#cubeCanvas [role=status]')?.dataset.frameTime) <= 1.501);
    await page.locator('#timeline').fill('7');
    await page.waitForTimeout(500);
    assert(Number(await readout.getAttribute('data-frame-time')) <= 1.501, 'A scrub cannot inspect beyond the playback clock.');
    playback = 8;
    processed = 0.75;
    await page.waitForFunction(() => Number(document.querySelector('#cubeCanvas [role=status]')?.dataset.frameTime) <= .751);
    assert(Number(await readout.getAttribute('data-frame-time')) <= .751, 'A scrub cannot inspect beyond processing.');
    assert.deepEqual(errors, []);
    console.log('Frame scrub uses decoded video and respects playback and processing cutoffs.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
