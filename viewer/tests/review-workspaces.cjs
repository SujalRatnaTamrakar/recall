// Synthetic UI contracts. Real persistence and clip export are covered by test_reviews.py.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require('playwright');
const base=process.env.UI_BASE_URL||'http://127.0.0.1:8870';
(async()=>{
  const browser=await chromium.launch({headless:true,channel:'chrome'});
  try{
    const context=await browser.newContext({viewport:{width:1440,height:1080}});
    const boards=new Map(),moments=new Map(),requests=[],errors=[];
    let run='review-qa',sourceCalls=0,questionCalls=0;
    const boardFor=role=>{const key=`${run}:${role}`;if(!boards.has(key))boards.set(key,{run_id:run,revision:0,role,title:'Synthetic trail recording',through_s:8,brief:{name:'',terms:'',checklist:[]},items:[]});return boards.get(key);};
    const json=(route,value)=>route.fulfill({contentType:'application/json',body:JSON.stringify(value)});
    const record={evidence_id:'ev-bottle',kind:'transcript',t_start:2,t_end:4,modality:'audio',text:'Thanks to Trail Flask for sponsoring this walk. This bottle keeps my tea warm.'};
    await context.route('**/*',async route=>{
      const req=route.request(),url=new URL(req.url());
      if(url.origin!==new URL(base).origin)return route.abort();
      if(url.pathname==='/video'){
        const bytes=fs.readFileSync(path.join(__dirname,'../.qa/source.mp4'));
        const match=req.headers().range?.match(/^bytes=(\d+)-(\d*)$/);
        if(!match)return route.fulfill({contentType:'video/mp4',body:bytes});
        const start=Number(match[1]),end=match[2]?Math.min(Number(match[2]),bytes.length-1):bytes.length-1;
        return route.fulfill({status:206,contentType:'video/mp4',body:bytes.subarray(start,end+1),headers:{'accept-ranges':'bytes','content-range':`bytes ${start}-${end}/${bytes.length}`}});
      }
      if(!url.pathname.startsWith('/api/'))return route.continue();
      if(url.pathname==='/api/status')return json(route,{run_id:run,mode:'synthetic',title:'Synthetic trail recording',duration_s:8,has_video:true,playback_s:8,processed_through_s:8,audio_through_s:8,vision_through_s:8,state:'ended',csrf_token:'review-token',coverage_gaps:[]});
      if(req.method()==='POST')assert.equal(req.headers()['x-tube-token'],'review-token');
      if(url.pathname==='/api/source'||url.pathname==='/api/control'){sourceCalls++;return json(route,{});}
      if(url.pathname==='/api/memory')return json(route,{run_id:run,through_s:8,records:[record],frames:[]});
      if(url.pathname==='/api/ask'){
        const data=req.postDataJSON();requests.push(data);questionCalls++;
        assert.equal(data.share,false);assert.equal(data.now,8);
        return json(route,{run_id:run,through_s:8,status:'answered',summary:'They thanked Trail Flask for sponsoring the walk and said the bottle keeps their tea warm.',citations:[{evidence_id:record.evidence_id,t:2,t_end:4,source_type:'spoken',label:'Sponsor statement',excerpt:record.text}],context:{through_s:8,story_through_s:8,selected_records:1,semantic_search:true,profile_version:0,records:[{evidence_id:record.evidence_id,start_s:2,end_s:4,source_type:'spoken',role:'match',excerpt:record.text}]}});
      }
      if(url.pathname==='/api/reviews'){
        const data=req.method()==='POST'?req.postDataJSON():null;
        const board=boardFor(data?.role||url.searchParams.get('role'));
        if(data){
          assert.equal(data.run_id,run);
          if(data.revision!==board.revision)return route.fulfill({status:400,contentType:'application/json',body:JSON.stringify({error:'This board changed in another tab. Refresh the board before saving.'})});
          if(data.action==='brief')board.brief=data.brief;
          if(data.action==='create')board.items.push({id:`saved-${board.items.length}`,title:data.title,notes:'',requirement:data.requirement,status:board.role==='editor'?'shortlisted':'needs_review',sources:[{...record,excerpt:record.text,source_type:'spoken'}],through_s:8});
          if(data.action==='update')Object.assign(board.items.find(i=>i.id===data.id),{notes:data.notes,status:data.status});
          if(data.action==='remove')board.items=board.items.filter(i=>i.id!==data.id);
          board.revision++;
        }
        return json(route,board);
      }
      if(url.pathname==='/api/reviews/moment'){
        const data=req.postDataJSON();assert.equal(data.run_id,run);assert.equal(data.evidence_id,record.evidence_id);
        const value={id:`moment-${moments.size}`,run_id:run,start_s:0,end_s:8,evidence_s:2,url:`/?view=moment&id=moment-${moments.size}`,title:'Synthetic trail recording',excerpt:record.text,video_url:'/video',source_type:'spoken'};moments.set(value.id,value);return json(route,value);
      }
      if(url.pathname.startsWith('/api/moments/'))return json(route,moments.get(url.pathname.split('/').pop()));
      throw new Error(`Unexpected API ${url.pathname}`);
    });
    const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
    for(const role of ['moderator','campaign','editor']){
      await page.goto(`${base}/?view=${role}`);
      await page.waitForFunction(()=>document.querySelector('#reviewSubmit')?.disabled===false);
      assert.equal(await page.locator('.perspective-nav [aria-current]').textContent(),role[0].toUpperCase()+role.slice(1));
      assert.equal(await page.locator('#reviewVideo').getAttribute('src'),null,'Do not decode another video until requested.');
      if(role==='campaign'){
        await page.locator('#briefName').fill('Trail Flask');await page.locator('#briefTerms').fill('Trail Flask');
        await page.locator('#briefChecklist').fill('Mention the sponsorship\nShow the bottle');
        await page.locator('#briefForm button').click();
        await page.waitForFunction(()=>document.querySelectorAll('[data-check]').length===2);
        await page.locator('[data-check="0"]').click();
      }else await page.locator('#reviewQuestion').fill('What did they say about this walk?');
      await page.locator('#reviewSubmit').click();
      await page.waitForSelector('.review-match');
      assert((await page.locator('.review-answer').textContent()).includes('Trail Flask'));
      await page.locator('[data-add]').click();
      await page.waitForSelector('.review-saved');
      const state=role==='campaign'?'confirmed':role==='editor'?'selected':'reviewed';
      await page.locator('.item-status').selectOption(state);
      await page.locator('.item-notes').fill('Checked the wording. <img src=x onerror=alert(1)>');
      await page.locator('[data-save]').click();
      await page.waitForFunction(()=>document.querySelector('#reviewNotice').textContent.includes('Review saved'));
      assert.equal(await page.locator('.review-saved img').count(),0);
      await page.reload();await page.waitForSelector('.review-saved');
      assert.equal(await page.locator('.item-status').inputValue(),state);
      assert((await page.locator('.item-notes').inputValue()).includes('<img'));
      if(role==='moderator'){
        // A conflicting save can be recovered without losing the local note.
        boardFor(role).revision++;
        await page.locator('.item-notes').fill('Keep this unsaved review after refreshing.');
        await page.locator('[data-save]').click();
        await page.waitForFunction(()=>document.querySelector('#reviewNotice').textContent.includes('another tab'));
        await page.locator('#refreshBoard').click();
        await page.waitForFunction(()=>document.querySelector('#reviewNotice').textContent.includes('unsaved edits are preserved'));
        assert.equal(await page.locator('.item-notes').inputValue(),'Keep this unsaved review after refreshing.');
        await page.locator('[data-save]').click();
        await page.waitForFunction(()=>document.querySelector('#reviewNotice').textContent.includes('Review saved'));
      }
      if(role==='campaign')assert((await page.locator('[data-check="0"]').textContent()).includes('Reviewed by you'));
      const downloadPromise=page.waitForEvent('download');await page.locator('#exportBoard').click();
      const download=await downloadPromise;const exported=JSON.parse(fs.readFileSync(await download.path(),'utf8'));
      assert.equal(exported.role,role);assert.equal(exported.items[0].status,state);
      await page.locator('.review-saved [data-watch]').click();
      await page.waitForFunction(()=>document.querySelector('#reviewVideo').readyState>=2);
      assert((await page.locator('#nearbyTranscript').textContent()).includes('sponsoring'));
      const popupPromise=page.waitForEvent('popup');await page.locator('#reviewClip').click();const popup=await popupPromise;
      await popup.waitForSelector('.moment-editor:not([hidden])');await popup.close();
      await page.screenshot({path:path.join(__dirname,`../.qa/${role}-workspace.png`),fullPage:true});
      await page.locator('#closePreview').click();
      assert.equal(await page.locator('#reviewVideo').getAttribute('src'),null);
      await page.setViewportSize({width:390,height:844});
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'No mobile horizontal overflow.');
      await page.setViewportSize({width:1440,height:1080});
    }
    const before=questionCalls;await page.locator('.review-saved [data-watch]').click();await page.waitForSelector('#reviewInspector:not([hidden])');
    run='next-replay';
    await page.waitForFunction(()=>document.querySelector('#boardCount').textContent==='0');
    assert.equal(await page.locator('#reviewVideo').getAttribute('src'),null);
    assert.equal(questionCalls,before,'Replay changes do not trigger unsolicited inference.');
    assert.equal(sourceCalls,0,'Review pages never control source playback.');
    assert(requests.every(q=>q.share===false));assert.deepEqual(errors,[]);
    console.log('PASS: all three role workflows, private search, save/reload, human review, export, clip editor, mobile layout, restart isolation.');
    await context.close();
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
