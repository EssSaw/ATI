import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { execFile } from 'node:child_process';
import { createServer } from 'vite';
import { fileURLToPath } from 'node:url';
const chrome = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const fixture = `<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script>addEventListener('error',e=>fetch('/ui-result',{method:'POST',body:JSON.stringify({error:e.message})}));addEventListener('unhandledrejection',e=>fetch('/ui-result',{method:'POST',body:JSON.stringify({error:String(e.reason)})}));</script><script type="module">
import React from '/node_modules/.vite/deps/react.js';
import ReactDOM from '/node_modules/.vite/deps/react-dom_client.js';const {createRoot}=ReactDOM;
import ProjectChat from '/src/ProjectChat.jsx';
import {scheduleModelLoad} from '/src/models.js';
import {preferences} from '/src/state.js';
import {loadChats} from '/src/chatStorage.js';
import '/src/styles.css'; import '/src/project.css';
const wait = async check => { for(let i=0;i<150;i++){if(check())return;await new Promise(r=>setTimeout(r,30));}throw new Error('UI wait timed out'); };
const find = text => [...document.querySelectorAll('button')].find(n=>n.textContent.includes(text));
const check = (condition,message)=>{if(!condition)throw new Error(message)};
let sends=0,lastPayload,modelLoads=0,preparations=0;
const fixtureSettings={mode:'9router',router_base_url:'http://localhost:20128/v1',router_api_key:'fixture-secret',router_model:'provider/original'};
function Harness(){const [settings,setSettings]=React.useState(fixtureSettings);const [open,setOpen]=React.useState(false);const [catalog,setCatalog]=React.useState({models:[],status:'idle',error:''});React.useEffect(()=>{if(!open)return;setCatalog({models:[],status:'loading',error:''});return scheduleModelLoad(request,settings,setCatalog);},[open,settings.router_base_url,settings.router_api_key]);return React.createElement(ProjectChat,{settings,request,openSettings:()=>{},health:'Fixture',route:'Fixture',onCompiler:()=>{},modelCatalog:catalog,onModelOpen:setOpen,onModelSelect:id=>setSettings(s=>{const next={...s,router_model:id};localStorage.setItem('fixture-preferences',JSON.stringify(preferences(next)));return next;})});}
const request=async(path,payload)=>{if(path==='/agent/models'){modelLoads++;check(payload.router_api_key==='fixture-secret','model credentials missing');return {models:['provider/exact-ID','provider/exact-ID','other/model']};}if(path.endsWith('/workspaces/open'))return {workspace_id:'fixture-workspace',revision:'one',name:'Fixture project',path:'/fixture',files:[],skipped:[]};if(path.endsWith('/prepare')){preparations++;await new Promise(r=>setTimeout(r,80));return {id:'prepared',name:payload.name,mime_type:'text/plain',kind:'text',size:12,text:'local notes'};}if(path.endsWith('/context')){check(payload.context_window===32768,'default budget absent');check(payload.settings.api_key==='','preview leaked key');return {context:{window_tokens:32768,reserved_tokens:4096,estimated_tokens:1800,usage_percent:5.5,breakdown:{system:500,tools:500,project:0,messages:400,attachments:400,tool_results:0},dropped_messages:0,estimate:true,compacted:false}};}if(path.endsWith('/project-chat')){sends++;lastPayload=payload;check(payload.settings.router_model==='provider/exact-ID','exact selected model absent');if(sends<=2)check(payload.messages.at(-1).attachments[0].name==='notes.txt','attachment missing in payload');if(sends===1)throw new Error('Provider fixture unavailable');return {reply:'Fixture answer'};}throw new Error('Unexpected request '+path);};
const root=createRoot(document.getElementById('root'));root.render(React.createElement(Harness));
try{
 await wait(()=>find('+ New chat')&&!find('+ New chat').disabled);
 check(document.querySelector('.project-tutorial').textContent.includes('Select a project folder'),'tutorial absent');
 document.querySelector('.model-toggle').click();await wait(()=>document.querySelector('.model-popover'));check(document.querySelector('.model-toggle').textContent.includes('provider/original'),'exact active model missing');await wait(()=>find('provider/exact-ID'));check(modelLoads===1,'duplicate catalog loads');check(document.querySelectorAll('.model-options button').length===2,'duplicate catalog IDs');find('provider/exact-ID').click();await wait(()=>document.querySelector('.model-toggle').textContent.includes('provider/exact-ID'));
 check(!localStorage.getItem('fixture-preferences').includes('fixture-secret'),'key persisted');check(!document.querySelector('[data-api-key]'),'key exposed');check(!document.querySelector('button button'),'nested buttons');
 check(!document.querySelector('.project-main .local-workspace')?.checkVisibility(),'path controls leaked into canvas');
 const options=document.querySelector('.conversation-more'); options.click(); await wait(()=>find('Project settings')); find('Project settings').click(); await wait(()=>document.querySelector('dialog').open);
 check(document.querySelector('dialog input[placeholder="/Users/you/project"]'),'path missing from settings');
 document.querySelector('.settings-close').click();
 document.querySelector('.attach-toggle').click();await wait(()=>find('Choose browser snapshot'));check(find('Attach files'),'attach menu missing');document.querySelector('.attach-toggle').click();
 const directoryDrop=new Event('drop',{bubbles:true,cancelable:true});Object.defineProperty(directoryDrop,'dataTransfer',{value:{types:['Files'],items:[{webkitGetAsEntry:()=>({isDirectory:true})}],files:[]}});document.querySelector('.project-main').dispatchEvent(directoryDrop);await wait(()=>document.querySelector('.attachment-error')?.textContent.includes('Directories'));check(preparations===0,'directory prepared recursively');
 const picker=document.querySelector('input[aria-label="Attach files"]'), dt=new DataTransfer();dt.items.add(new File(['local notes'],'notes.txt',{type:'text/plain'}));dt.items.add(new File(['bad'],'bad.exe'));document.querySelector('.project-main').dispatchEvent(new DragEvent('dragenter',{bubbles:true,dataTransfer:dt}));await wait(()=>document.querySelector('.file-drop-overlay'));document.querySelector('.project-composer').dispatchEvent(new DragEvent('dragenter',{bubbles:true,dataTransfer:dt}));document.querySelector('.project-composer').dispatchEvent(new DragEvent('dragleave',{bubbles:true,dataTransfer:dt}));check(document.querySelector('.file-drop-overlay'),'nested dragleave flickered');document.querySelector('.project-main').dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer:dt}));
 await wait(()=>document.querySelector('.attachment-list'));
 await wait(()=>!document.querySelector('.attach-toggle').disabled);
 check(document.querySelector('.attachment-error').textContent.includes('bad.exe'),'unsupported error absent');
 check(document.querySelector('.attachment-list').textContent.includes('notes.txt'),'chip missing');
 const textarea=document.querySelector('#project-prompt');check(!textarea.disabled,'composer locked after prepare');
 const submit=document.querySelector('.project-composer button[type="submit"]');check(submit.disabled,'attachments bypass project requirement');check(document.querySelector('.project-tutorial'),'attachments hid tutorial');
 document.querySelector('.project-status').click();await wait(()=>document.querySelector('dialog').open);const path=document.querySelector('input[placeholder="/Users/you/project"]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(path,'/fixture');path.dispatchEvent(new Event('input',{bubbles:true}));await wait(()=>!find('Connect local project').disabled);find('Connect local project').click();await wait(()=>!document.querySelector('.project-tutorial'));document.querySelector('.settings-close').click();await wait(()=>!submit.disabled);
 const composer=document.querySelector('.project-composer-wrap');check(composer.parentElement.classList.contains('project-main'),'composer outside main');const rect=composer.getBoundingClientRect(),main=document.querySelector('.project-main').getBoundingClientRect();check(rect.left>=main.left && rect.right<=main.right+1,'composer crosses main boundary');check(rect.top>=document.querySelector('.project-scroll').getBoundingClientRect().bottom-1,'composer not below conversation');check(!document.querySelector('.composer-actions').textContent.includes('/ 30'),'message cap hint remains');check(submit.getAttribute('aria-label')==='Send message','send inaccessible');check(submit.textContent.trim()==='↑','send not icon only: '+JSON.stringify(submit.textContent));const footer=document.querySelector('.sidebar-bottom').getBoundingClientRect();check(footer.bottom<=innerHeight,'sidebar footer clipped');check(rect.bottom<=innerHeight+1,'composer outside viewport');check(document.documentElement.scrollWidth<=innerWidth+1,'horizontal page overflow');
 document.querySelector('.context-toggle').click();await wait(()=>document.querySelector('#context-popover'));await wait(()=>document.querySelector('#context-popover').textContent.includes('1,800'));check(document.querySelector('[role=progressbar]'),'context usage missing');check(find('Compact conversation').disabled,'empty compact enabled');document.querySelector('#context-popover').dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));await wait(()=>!document.querySelector('#context-popover'));check(document.activeElement.classList.contains('context-toggle'),'escape focus not restored');
 document.querySelector('.project-composer').requestSubmit();await wait(()=>sends>0||document.querySelector('.error'));
 check(sends===1,'send not issued');check(document.querySelector('.attachment-list').textContent.includes('notes.txt'),'draft lost on failure');
 document.querySelector('.project-composer').requestSubmit();await wait(()=>document.querySelector('.project-message.assistant'));
 check(!document.querySelector('.project-composer .attachment-list'),'draft not cleared on success');
 check(document.querySelector('.project-message.user .attachment-list').textContent.includes('notes.txt'),'sent attachment not rendered');
 document.querySelector('.attach-toggle').click();await wait(()=>find('Choose browser snapshot'));check(find('Choose browser snapshot').disabled,'snapshot not frozen');check(document.querySelector('#snapshot-reason').textContent.includes('frozen'),'disabled reason missing');document.querySelector('.attach-toggle').click();
 const first=document.querySelector('.conversation-select');find('+ New chat').click();await wait(()=>document.querySelectorAll('.conversation-row').length===2);await new Promise(r=>setTimeout(r,50));check(document.querySelector('.project-tutorial'),'new chat tutorial absent');document.querySelector('.conversation-more').click();await wait(()=>find('Project settings'));find('Project settings').click();await wait(()=>document.querySelector('dialog').open);check(first.getAttribute('aria-current')==='page','settings did not select corresponding chat');document.querySelector('.settings-close').click();
 for(let i=0;i<5;i++){const prompt=document.querySelector('#project-prompt');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(prompt,'Follow-up '+i);prompt.dispatchEvent(new Event('input',{bubbles:true}));await wait(()=>!document.querySelector('.composer-send').disabled);document.querySelector('.project-composer').requestSubmit();await wait(()=>document.querySelectorAll('.project-message.assistant').length===i+2);}
 const before=document.querySelectorAll('.project-message').length;document.querySelector('.context-toggle').click();await wait(()=>find('Compact conversation'));check(!find('Compact conversation').disabled,'compact disabled on long conversation');find('Compact conversation').click();await wait(()=>document.querySelector('.context-banner'));check(document.querySelectorAll('.project-message').length===before,'compaction deleted visible history');await new Promise(r=>setTimeout(r,800));const saved=await loadChats();const active=saved.chats.find(c=>c.id===saved.active);check(active.contextStart===4,'boundary not saved');check(active.messages.length===12,'compaction deleted saved history');check(active.lastContext===null,'compaction retained stale metadata');
 root.unmount();const restoredRoot=createRoot(document.getElementById('root'));restoredRoot.render(React.createElement(Harness));await wait(()=>document.querySelector('.project-status')?.textContent.includes('reconnect required'));check(document.querySelector('.project-tutorial').textContent.includes('Reconnect'),'reload reconnect reminder absent');check(document.querySelector('.composer-send').disabled,'reconnect did not block send');
 await fetch('/ui-result',{method:'POST',body:JSON.stringify({ok:true,width:innerWidth})});
}catch(error){await fetch('/ui-result',{method:'POST',body:JSON.stringify({error:error.message})});}
</script></body></html>`;
for (const width of [1280, 390]) test(`Chrome attachment UI fixture at ${width}px`, { skip: !fs.existsSync(chrome), timeout: 20000 }, async () => {
  let resolveResult;
  const result = new Promise(resolve => { resolveResult = resolve; });
  const plugin = {name:'attachment-fixture',configureServer(server){ server.middlewares.use((req, res, next) => {
    if (req.url === '/ui-fixture') { res.setHeader('Content-Type', 'text/html; charset=utf-8'); res.end(fixture); }
    else if (req.url === '/ui-result') { let body='';req.on('data',chunk=>{body+=chunk;});req.on('end',()=>{resolveResult(JSON.parse(body));res.end('ok');}); }
    else next();
  }); }};
  const server = await createServer({ root: fileURLToPath(new URL('..', import.meta.url)), configFile: false, plugins:[plugin], server: { host: '127.0.0.1', port: 0 }, logLevel: 'silent' });
  await server.listen();
  const port=server.httpServer.address().port;
  const process=execFile(chrome,['--headless=new','--disable-gpu','--enable-logging=stderr',`--window-size=${width},900`,`http://127.0.0.1:${port}/ui-fixture`]);
  let timer;let errors='';process.stderr.on('data',chunk=>{errors+=chunk;});
  try { const outcome=await Promise.race([result,new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error('Chrome attachment fixture timed out: '+errors.slice(-2000))),15000);})]);assert.equal(outcome.error,undefined,outcome.error);assert.equal(outcome.ok,true); }
  finally { clearTimeout(timer);process.kill();await server.close(); }
});
