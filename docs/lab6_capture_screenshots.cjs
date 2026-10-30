// Render the actual local report. Does not access GitHub or the user's browser.
const { chromium } = require('playwright');
const { pathToFileURL } = require('node:url');
const path = require('node:path');
const fs = require('node:fs');
(async () => {
  const out = path.join(__dirname, 'lab6_screenshots');
  fs.mkdirSync(out, {recursive:true});
  const browser = await chromium.launch({headless:true, channel:'chrome'});
  try {
    const page = await browser.newPage({viewport:{width:1600,height:1050},deviceScaleFactor:1});
    const errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    for(const [view,name,expected] of [['team','local_01_team',26],['individual','local_02_individual',6],['task20','local_03_task20',0]]) {
      await page.goto(pathToFileURL(path.join(__dirname,'lab6_review.html')).href+'?view='+view);
      await page.locator('#'+view).waitFor({state:'visible'});
      const count=await page.locator('article:visible').count();
      if(count!==expected) throw new Error(`Expected ${expected} cards, got ${count}`);
      if(errors.length) throw new Error(errors.join('\n'));
      await page.evaluate(()=>window.scrollTo(0,0));
      await page.screenshot({path:path.join(out,name+'.png'),fullPage:true});
      console.log(`${name}: ${count} visible task cards`);
    }
    await page.goto(pathToFileURL(path.join(__dirname,'lab6_review.html')).href+'?view=team');
    await page.getByRole('link',{name:'Бабаев Ильгар',exact:true}).click();
    if(await page.locator('article:visible').count()!==6) throw new Error('Navigation failed');
  } finally { await browser.close(); }
})().catch(e=>{console.error(e.message);process.exitCode=1;});
