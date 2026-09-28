// Needs an answer: agent messages addressed to you (@user or @you up front) or private to you, in every
// project, oldest first, until you post to that agent or to everyone after it, or answer or dismiss them
// here. Answers to a private message stay private. When a
// question lists options (A) B) C), Option 1: …) they become choices: ↑/↓ and Enter, or the letter.
let dismissed=saved.get('dismissed',{}),answerKey='',answersMin=saved.get('answersMin',false);
const answerNodes=new Map();
function pending(){const out=[];
  for(const p of projects){const list=msgs[p.id]||[],gone=new Set(dismissed[p.id]||[]),answered=new Set();let all=false;
    for(let i=list.length-1;i>=0&&!all;i--){const m=list[i];  // newest first: who you have posted to since
      if(m.from==='user'&&m.dm){answered.add(current(m.dm,p.id));continue;}
      if(m.from==='user'){if(!lead(m.text).length)all=true;
        for(const x of m.text.matchAll(/@([\w-]+)/g))answered.add(current(x[1].toLowerCase(),p.id));continue;}
      if(m.kind!=='board'&&!answered.has(current(m.from,p.id))&&!gone.has(m.n)&&(m.dm||lead(m.text).some(n=>n==='user'||n==='you')))out.push({p,m});}}
  return out.sort((a,b)=>a.m.time<b.m.time?-1:a.m.time>b.m.time?1:a.m.n-b.m.n);}
// options: lines like "A) …", "**B.** …", "- C: …", "Option 1: …", in order from A or 1, at least two
const OPT_RE=/^\s*(?:[-*]\s+)?(?:\*\*)?(?:option\s+([A-Za-z]|\d)|\(?([A-H])\))(?:\*\*)?\s*[.):—–-]?\s*(?:\*\*)?\s*(.+)$|^\s*(?:[-*]\s+)?(?:\*\*)?([A-H])(?:\*\*)?\s*[.:—–-]\s*(?:\*\*)?\s*(.+)$/i;
function options(text){const found=[];
  for(const line of text.replace(/```[\s\S]*?```/g,'').split('\n')){const m=OPT_RE.exec(line);if(!m)continue;
    const key=(m[1]||m[2]||m[4]).toUpperCase(),label=(m[3]||m[5]).replace(/\*\*/g,'').trim();
    const want=found.length?String.fromCharCode(found[found.length-1].key.charCodeAt(0)+1):null;
    if(key===(want||(/\d/.test(key)?'1':'A')))found.push({key,label});else if(key==='A'||key==='1')found.splice(0,found.length,{key,label});}
  if(found.length<2){found.length=0;  // inline: "… (A) this; (B) that. (C) other"
    const m=[...text.replace(/```[\s\S]*?```/g,'').matchAll(/\(([A-H])\)\s*(.+?)(?=\s*\([A-H]\)|\n|$)/g)];
    for(const x of m){const key=x[1],want=found.length?String.fromCharCode(found[found.length-1].key.charCodeAt(0)+1):'A';
      if(key===want)found.push({key,label:x[2].replace(/\*\*/g,'').replace(/[;,]?\s*(or|and)?$/i,'').trim()});}}
  return found.length>=2?found:[];}
function drawAnswers(){
  const list=pending(),keys=list.map(x=>x.p.id+':'+x.m.n),key=keys.join();
  if(list.length>answerCount&&answerCount>=0&&key!==answerKey&&answerKey!==''){answersMin=false;saved.set('answersMin',false);}
  answerCount=list.length;
  $('answers').hidden=!list.length||answersMin;$('answercount').textContent=String(list.length);
  $('answerchip').hidden=!list.length||!answersMin;
  $('answerchip').textContent=list.length+(list.length===1?' needs an answer':' need an answer');
  if(key===answerKey)return;answerKey=key;
  for(const k of answerNodes.keys())if(!keys.includes(k))answerNodes.delete(k);
  const focus=document.activeElement,a=focus&&focus.selectionStart,b=focus&&focus.selectionEnd;
  $('answerlist').replaceChildren(...list.map((x,i)=>{
    if(!answerNodes.has(keys[i]))answerNodes.set(keys[i],answerItem(x.p,x.m));return answerNodes.get(keys[i]);}));
  if(focus&&focus!==document.activeElement&&focus.isConnected){focus.focus();if(a!=null)focus.setSelectionRange(a,b);}
  requestAnimationFrame(()=>{for(const d of $('answerlist').children){const body=d.querySelector('.body'),more=d.querySelector('.more');
    more.hidden=body.classList.contains('open')||body.scrollHeight<=body.clientHeight+4;}});}
function answerItem(p,m){
  const from=current(m.from,p.id),d=who(el('article','ans'),from,p.id),h=el('div','ah'),body=el('div','body rich'),box=el('div','box'),ta=el('textarea');
  const x=btn('×',()=>dismiss(p.id,m.n),'icon');x.title='Dismiss without answering';x.setAttribute('aria-label','Dismiss');
  const nm=el('b','',from);if(from!==m.from)nm.title='posted as '+m.from;
  h.append(avatar(from,p.id),nm,...(m.dm?[el('span','dmtag','Private')]:[]),
    el('small','',(projects.length>1?p.name+' · ':'')+m.time.slice(11,16)),x);
  body.append(renderText(m.text,{pid:p.id}));
  const more=btn('Show more',()=>{body.classList.toggle('open');more.textContent=body.classList.contains('open')?'Show less':'Show more';},'more');
  ta.rows=1;ta.value='@'+from+' ';ta.setAttribute('aria-label','Answer '+from);mentions(ta);
  const fit=()=>{ta.style.height='auto';ta.style.height=ta.scrollHeight+'px';};
  const send=async text=>{text=text.trim();if(!text||ta.disabled)return;ta.disabled=true;
    try{await api(`api/projects/${p.id}/messages`,{from:'user',text,reply:m.n,...(m.dm?{dm:current(m.dm,p.id)}:{})});dismiss(p.id,m.n);refresh();}
    catch(e){ta.disabled=false;say('','Not sent: '+e.message);}};
  const opts=options(m.text),list=opts.length?choices(opts,o=>send(`@${from} ${o.key}: ${o.label.slice(0,100)}`),
    ()=>{ta.focus();ta.setSelectionRange(ta.value.length,ta.value.length);}):null;
  ta.oninput=()=>{field=ta;sel=0;showAc();fit();};
  ta.onkeydown=e=>{if(acKeys(e))return;
    if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();send(ta.value);}
    else if(e.key==='ArrowUp'&&list&&ta.value.trim()==='@'+from){e.preventDefault();list.focus();}};
  box.append(ta,btn('Send',()=>send(ta.value)));
  d.append(h,body,more,...(list?[list]:[]),box);return d;}
function choices(opts,pick,chat){
  const list=el('div','choices'),items=[...opts,{key:'✎',label:'Chat about it'}];let at=0;
  list.tabIndex=0;list.setAttribute('role','listbox');list.setAttribute('aria-label','Choose an answer: arrow keys, Enter, or the letter');
  const go=()=>at===opts.length?chat():pick(items[at]);
  const rows=items.map((o,i)=>{const r=el('div','choice');r.setAttribute('role','option');
    r.append(el('kbd','',o.key),el('span','',o.label));r.onmousedown=e=>e.preventDefault();
    r.onclick=()=>{at=i;mark();go();};return r;});
  const mark=()=>rows.forEach((r,i)=>{r.classList.toggle('sel',i===at);r.setAttribute('aria-selected',String(i===at));});
  list.onkeydown=e=>{if(e.ctrlKey||e.altKey||e.metaKey)return;
    if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();at=(at+(e.key==='ArrowDown'?1:items.length-1))%items.length;
      mark();rows[at].scrollIntoView({block:'nearest'});}
    else if(e.key==='Enter'||e.key===' '){e.preventDefault();go();}
    else{const i=opts.findIndex(o=>o.key===e.key.toUpperCase());if(i>=0){e.preventDefault();at=i;mark();go();}}};
  mark();list.append(...rows);return list;}
function dismiss(pid,n){(dismissed[pid]=dismissed[pid]||[]).push(n);saved.set('dismissed',dismissed);render();}
function showAnswers(min){answersMin=min;saved.set('answersMin',min);render();}
function focusAnswer(){  // Alt+N: the first question's choices, else its reply box
  const first=$('answerlist').firstElementChild;if(!first||!answerCount)return false;
  if(answersMin)showAnswers(false);(first.querySelector('.choices')||first.querySelector('textarea')).focus();return true;}
$('answertoggle').onclick=()=>showAnswers(true);
$('answerchip').onclick=()=>{showAnswers(false);focusAnswer();};
