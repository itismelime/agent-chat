// Models panel: installed models, Hugging Face search, jobs.
// Uses the page's $, el, api and KIND; loaded after the page's own script.
const mp=$('modelspanel'),GB=b=>b==null?'?':(b/1024**3).toFixed(1)+' GB';
let mtab='installed',mtimer=null,mdrawn='';
function openModels(){mp.hidden=false;showTab(mtab);clearInterval(mtimer);mtimer=setInterval(mrefresh,2000);}
function closeModels(){mp.hidden=true;clearInterval(mtimer);t.focus();}
function showTab(tab){mtab=tab;mdrawn='';
  for(const b of mp.querySelectorAll('.mtab'))b.classList.toggle('on',b.dataset.tab===tab);
  for(const s of mp.querySelectorAll('.mview'))s.hidden=s.dataset.tab!==tab;mrefresh();}
function merr(e){$('merr').textContent=e?e.message||String(e):'';}
async function mrefresh(){
  try{
    const s=await api('api/models/status');
    $('mstatus').textContent=(s.own?"agent-chat's Ollama":'External Ollama')+' at '+s.url+' · '+
      (s.reachable?'v'+s.version:'not reachable: '+(s.own?'systemctl --user start agent-chat-ollama':
        'check the address (./install.sh --ollama-url)'))+' · '+
      (s.gpu?`${s.gpu.name}, ${GB(s.gpu.used)} of ${GB(s.gpu.total)} used`:'GPU unknown');
    if(mtab==='installed'&&s.reachable)drawInstalled((await api('api/models')).models);
    if(mtab==='jobs')drawJobs((await api('api/models/jobs')).jobs);
  }catch(e){merr(e);}}
function button(text,fn){const b=el('button','ghost',text);b.type='button';
  b.onclick=async()=>{try{merr();await fn();}catch(e){merr(e);}mrefresh();};return b;}
function drawInstalled(rows){
  const key=JSON.stringify(rows);if(key===mdrawn||$('mlist').contains(document.activeElement))return;mdrawn=key;
  $('mlist').replaceChildren(...rows.map(r=>{
    const d=el('div','mrow'),ctx=el('input');ctx.type='number';ctx.min=512;ctx.max=131072;ctx.step=64;
    ctx.value=r.override||'';ctx.placeholder=r.num_ctx?String(r.num_ctx):'?';ctx.title='Context tokens (empty: recommended)';
    ctx.onchange=async()=>{try{merr();await api('api/models/tune',{model:r.name,num_ctx:ctx.value?+ctx.value:null});}catch(e){merr(e);}mdrawn='';};
    d.append(el('b','',r.name),el('span','',GB(r.size)),el('span',r.loaded?'badge':'',r.loaded?'Loaded':''),
      el('span','',r.verdict),ctx);
    if(r.can_think){const sel=el('select');sel.title='Thinking';
      for(const [v,label] of [['','Default (off to talk, on for agents)'],['off','Off'],
        ...(r.levels||['on']).map(v=>[v,v[0].toUpperCase()+v.slice(1)])])sel.append(new Option(label,v));
      sel.value=r.think||'';
      sel.onchange=async()=>{try{merr();await api('api/models/tune',{model:r.name,think:sel.value||null});}catch(e){merr(e);}mdrawn='';};
      d.append(sel);}
    d.append(button('Benchmark',async()=>{await api('api/models/benchmark',{model:r.name});showTab('jobs');}),
      button('Delete',async()=>{if(confirm(`Delete ${r.name}? Its files are removed.`))await api('api/models/delete',{model:r.name});}));
    return d;}));}
async function search(){
  const q=$('mq').value.trim();$('mresults').replaceChildren(el('p','','Searching Hugging Face…'));
  try{merr();const {results}=await api('api/models/search?q='+encodeURIComponent(q));
    $('mresults').replaceChildren(...(results.length?results:[null]).map(r=>{
      if(!r)return el('p','','Nothing found.');
      const d=el('div','mrow'),name=el('input');name.value=r.name||'';name.title='Name in Ollama';
      d.append(el('b','',`${r.label} ${r.score}`),el('span','',r.id),el('span','',r.verdict),
        el('span','',r.file?`${r.file} · ${GB(r.size)}`:'no usable GGUF file'),el('span','',r.downloads.toLocaleString()+' downloads'),name);
      if(r.file)d.append(button('Get',async()=>{await api('api/models/import',{url:r.url,filename:r.file,model:name.value.trim()});showTab('jobs');}));
      return d;}));
  }catch(e){merr(e);$('mresults').replaceChildren();}}
function drawJobs(jobs){
  $('mjobs').replaceChildren(...(jobs.length?jobs:[null]).map(j=>{
    if(!j)return el('p','','No jobs yet.');
    const d=el('div','mrow'),bar=el('progress');bar.max=j.total||1;bar.value=j.state==='done'?bar.max:j.completed||0;
    d.append(el('b','',`${j.kind} ${j.model}`),el('span','',j.state),bar,el('span','',j.message||''));
    const r=j.result;
    if(r&&j.kind==='benchmark'){
      d.append(el('span','',`${r.seconds}s · ${r.tokens_per_second??'?'} tok/s · VRAM ${r.vram_used==null?'?':GB(r.vram_used)}`));
      if(r.thinking)for(const k of ['off','on']){const x=r.thinking[k];
        d.append(el('span','',`thinking ${k}: ${x.seconds}s, ${x.tokens} tokens (~${x.thinking_tokens} thinking)`));}}
    if(j.state==='running'&&['import','pull'].includes(j.kind))
      d.append(button('Cancel',()=>api(`api/models/jobs/${j.id}/cancel`,{})));
    return d;}));}
$('modelsbtn').onclick=openModels;$('mclose').onclick=closeModels;
for(const b of mp.querySelectorAll('.mtab'))b.onclick=()=>showTab(b.dataset.tab);
$('msearch').onsubmit=e=>{e.preventDefault();search();};
$('mpull').onsubmit=async e=>{e.preventDefault();const v=$('mpullname').value.trim();if(!v)return;
  try{merr();await api('api/models/pull',{model:v});$('mpullname').value='';showTab('jobs');}catch(err){merr(err);}};
$('munload').onclick=async()=>{try{merr();const r=await api('api/models/unload',{});
  $('merr').textContent=r.unloaded.length?'Unloaded '+r.unloaded.join(', '):'Nothing was loaded.';}catch(e){merr(e);}mrefresh();};
addEventListener('keydown',e=>{if(e.key==='Escape'&&!mp.hidden&&!['INPUT','SELECT'].includes(document.activeElement.tagName))closeModels();});
