// Real WebGL rendering against bounded synthetic memory, no model calls.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');
(async () => {
  const base = process.env.UI_BASE_URL || 'http://127.0.0.1:8767';
  const frame = fs.readFileSync(path.join(__dirname,'../.qa/frame.jpg'));
  let elapsed = 60, run = 'growth';
  const browser = await chromium.launch({headless:true,channel:'chrome'});
  try {
    const page = await browser.newPage({viewport:{width:1440,height:1100}});
    const errors = [];
    page.on('pageerror',error => errors.push(error.message));
    await page.route('**/*',route => {
      const url = new URL(route.request().url());
      if (url.origin !== base) return route.abort();
      const json = value => route.fulfill({contentType:'application/json',body:JSON.stringify(value)});
      if (url.pathname.startsWith('/frames/')) {
        assert(Number(url.pathname.split('/').pop()) <= elapsed,'Future frames must never load.');
        return route.fulfill({contentType:'image/jpeg',body:frame});
      }
      if (url.pathname === '/api/status') return json({run_id:run,mode:'replay',title:'Growing memory QA',duration_s:1800,has_video:false,playback_s:elapsed,processed_through_s:elapsed,audio_through_s:elapsed,vision_through_s:elapsed,state:'paused',csrf_token:'qa'});
      if (url.pathname === '/api/memory') return json({run_id:run,through_s:elapsed,records:[],frames:elapsed ? Array.from({length:241},(_,i)=>({id:String(i),t:i*5,url:`/frames/${i*5}`})) : []});
      if (url.pathname === '/api/shared') return json({run_id:run,rotation:0,selection:0,answer:null,controls_enabled:true,revision:0});
      if (url.pathname.startsWith('/api/')) return json({});
      return route.continue();
    });
    await page.goto(base);
    const volume = page.locator('#cubeCanvas');
    await page.waitForFunction(()=>document.querySelector('#cubeCanvas')?.dataset.memorySeconds === '60');
    assert.equal(Number(await volume.getAttribute('data-volume-depth')),4);
    elapsed = 600;
    await page.waitForFunction(()=>document.querySelector('#cubeCanvas').dataset.memorySeconds === '600');
    assert.equal(Number(await volume.getAttribute('data-volume-depth')),40);
    assert(Number(await volume.getAttribute('data-overview-frames')) <= 32);
    elapsed = 1200;
    await page.waitForFunction(()=>document.querySelector('#cubeCanvas').dataset.memorySeconds === '1200');
    assert.equal(Number(await volume.getAttribute('data-volume-depth')),80);
    await volume.scrollIntoViewIfNeeded();
    await page.waitForTimeout(300);
    await volume.screenshot({path:path.join(__dirname,'../.qa/growing-volume.png')});
    elapsed = 0; run = 'fresh';
    await page.waitForFunction(()=>document.querySelector('#cubeCanvas').dataset.memorySeconds === '0');
    assert.equal(Number(await volume.getAttribute('data-volume-depth')),0.4);
    assert.equal(Number(await volume.getAttribute('data-overview-frames')),0);
    assert.deepEqual(errors,[]);
    console.log('PASS: volume grows at fixed time scale; future frames excluded; overview bounded; restart clears volume.');
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exit(1)});
