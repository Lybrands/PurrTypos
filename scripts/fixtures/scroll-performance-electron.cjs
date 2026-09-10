const {app,BrowserWindow}=require('electron');
const fs=require('node:fs');
app.setPath('userData',require('node:path').join(__dirname,'electron-data'));
app.whenReady().then(async()=>{
 const w=new BrowserWindow({width:1200,height:800,show:false,webPreferences:{backgroundThrottling:false}});
 w.webContents.on('console-message',(_e,_l,msg)=>console.log(msg));
 try{
 await w.loadFile(require('node:path').join(__dirname,'index.html'));
 await new Promise(r=>setTimeout(r,600));
 const result=await w.webContents.executeJavaScript(`(async()=>{
 const sleep=ms=>new Promise(r=>setTimeout(r,ms));
 const longTasks=[];const po=new PerformanceObserver(list=>longTasks.push(...list.getEntries().map(e=>e.duration)));po.observe({type:'longtask'});
 const scroller=document.getElementById('conversation');let maxMounted=0;let maxFrame=0;let clicks=0;
 const frames=[]; let last=performance.now(); let running=true;
 function frame(t){frames.push(t-last);last=t;if(running)requestAnimationFrame(frame)}requestAnimationFrame(frame);
 for(let i=0;i<80;i++){
 scroller.scrollTop=(i<40?i/39:(79-i)/39)*(scroller.scrollHeight-scroller.clientHeight);
 document.getElementById('interaction').click();clicks++;
 await sleep(25);
 maxMounted=Math.max(maxMounted,document.querySelectorAll('.work-log__subagent').length);
 }
 document.querySelector('details').open=true;
 await sleep(300);
 const diagnosticScroller=document.querySelector('.ai-dev-inspector__body [data-virtuoso-scroller]');
 if(!diagnosticScroller)throw Error('missing virtual diagnostic scroller');
 let maxDiagnosticPre=0;
 for(let i=0;i<80;i++){
 diagnosticScroller.scrollTop=(i<40?i/39:(79-i)/39)*(diagnosticScroller.scrollHeight-diagnosticScroller.clientHeight);
 document.getElementById('interaction').click();clicks++;
 await sleep(25);
 maxDiagnosticPre=Math.max(maxDiagnosticPre,document.querySelectorAll('pre').length);
 }
 const diagnosisPre=document.querySelectorAll('pre').length;
 running=false;po.disconnect();
 return {clicks,button:document.getElementById('interaction').textContent,totalUnits:109,maxMounted,diagnosisPre,maxDiagnosticPre,frameP95:frames.sort((a,b)=>a-b)[Math.floor(frames.length*.95)],maxFrame:Math.max(...frames),longTasks,scrollTop:scroller.scrollTop,scrollHeight:scroller.scrollHeight};
})()`);
 if(result.button!=='响应 160'||result.maxMounted>=109||result.maxDiagnosticPre>8) throw Error('scroll workload did not remain bounded');
 console.log(JSON.stringify(result));fs.writeFileSync(require('node:path').join(__dirname,'result.json'),JSON.stringify(result,null,2));
 fs.writeFileSync(require('node:path').join(__dirname,'screenshot.png'),(await w.webContents.capturePage()).toPNG());
 }catch(e){console.error(e);app.exit(1)}finally{w.destroy();app.quit()}
});
