// Real provider check. Start an empty replay server with local AI and synthetic-demo.mp4.
// This is a local synthetic smoke test, not the human real-footage acceptance suite.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const base = process.env.UI_BASE_URL || 'http://127.0.0.1:8767';
async function get(route) { const r=await fetch(base+route); assert(r.ok); return r.json(); }
async function post(route, body) {
  const status=await get('/api/status');
  const response=await fetch(base+route,{method:'POST',headers:{'Content-Type':'application/json','X-Tube-Token':status.csrf_token},body:JSON.stringify(body)});
  const data=await response.json(); assert(response.ok, JSON.stringify(data)); return data;
}
(async()=>{
  const browser=await chromium.launch({headless:true,channel:'chrome'});
  const report={kind:'synthetic-local-provider-test',external_python_connections:process.env.TUBE_VERIFY_OFFLINE==='1'?'blocked-by-test-server':'not-restricted-by-this-test',samples:[]};
  try {
    const initial=await get('/api/status');
    assert.equal(initial.processed_through_s,0);
    const before=await post('/api/ask',{question:'What did they say they would do next?'});
    assert.equal(before.status,'insufficient_evidence');
    const studio=await browser.newPage({viewport:{width:1440,height:1050}});
    const source=await browser.newPage({viewport:{width:1280,height:900}});
    const errors=[]; studio.on('pageerror',e=>errors.push(e.message)); source.on('pageerror',e=>errors.push(e.message));
    await studio.goto(base); await source.goto(base+'/broadcast');
    await source.locator('#startBroadcast').click();
    for(let i=0;i<55;i++){
      await new Promise(resolve=>setTimeout(resolve,1000));
      const s=await get('/api/status');
      report.samples.push({playback_s:s.playback_s,through_s:s.processed_through_s,lag_s:s.lag_s,state:s.state});
      assert.equal(s.gap,null,JSON.stringify(s));
      assert(s.processed_through_s<=s.playback_s);
      if(s.state==='ended'&&s.lag_s<0.1) break;
    }
    const end=await get('/api/status');
    assert(end.playback_s>30,'Replay must actually play at 1x.');
    assert(end.processed_through_s>30,'The final source window must finish.');
    const memory=await get('/api/memory');
    assert(memory.frames.length>=6,'Real local vision must produce sampled frames.');
    report.records=memory.records.length;
    report.frames=memory.frames.length;
    report.max_lag_s=Math.max(...report.samples.map(s=>s.lag_s));
    report.questions=[];
    for(const question of ['When was a green circle visible?','What did they say they would do next?','Why did they choose a green circle?','What is the capital of France?']){
      const started=performance.now(); const answer=await post('/api/ask',{question});
      report.questions.push({question,seconds:(performance.now()-started)/1000,answer});
    }
    report.passed=false;
    fs.writeFileSync(path.resolve(__dirname,'../../docs/local-smoke-results.json'),JSON.stringify(report,null,2));
    assert.equal(report.questions[0].answer.status,'answered','Visual-only evidence retrieval must work.');
    assert(report.questions[0].answer.citations.some(c=>c.source_type==='observed'));
    assert.equal(report.questions[1].answer.status,'answered');
    assert.equal(report.questions[2].answer.status,'insufficient_evidence');
    assert.equal(report.questions[3].answer.status,'insufficient_evidence');
    await studio.locator('#question').fill('When was a green circle visible?');
    await studio.locator('#askSubmit').click();
    await studio.waitForFunction(()=>!document.querySelector('#askSubmit').disabled);
    await studio.waitForTimeout(1500);
    await studio.screenshot({path:path.resolve(__dirname,'../../docs/preview.png'),fullPage:true});
    assert.equal(await studio.locator('#cubeEmpty').isVisible(),false);
    report.browser_errors=errors; assert.deepEqual(errors,[]);
    const oldRun=end.run_id;
    await post('/api/control',{action:'restart'});
    const reset=await get('/api/status');
    assert.notEqual(reset.run_id,oldRun);
    assert.equal(reset.processed_through_s,0);
    assert.equal((await get('/api/memory')).records.length,0);
    const ended=report.samples.filter(s=>s.state==='ended');
    report.final_processing_lag_s=ended.at(-1)?.lag_s;
    report.passed=true;
    fs.writeFileSync(path.resolve(__dirname,'../../docs/local-smoke-results.json'),JSON.stringify(report,null,2));
    console.log(JSON.stringify({passed:true,frames:report.frames,records:report.records,max_lag_s:report.max_lag_s,questions:report.questions.map(q=>({question:q.question,status:q.answer.status,seconds:q.seconds})),browser_errors:errors},null,2));
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
