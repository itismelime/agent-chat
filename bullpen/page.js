// bullpen page: projects, transcript, composer, roster, terminal, dialogs.
// models.js and board.js load after this and use its globals.
const $=id=>document.getElementById(id),log=$('log'),t=$('t'),ac=$('ac'),bell=$('bell');
const saved={get(k,d){try{const v=JSON.parse(localStorage.getItem(k));return v===null?d:v}catch(e){return d}},
  set(k,v){try{localStorage.setItem(k,JSON.stringify(v))}catch(e){}}};
let projects=[],cur=saved.get('cur',null),msgs={},agents=[],seen=saved.get('seen',{}),notified={},drawn='',busy=false,down=false;
const folds=new Set();  // board-change folds the user opened, by their first message
let usageBy={},usageAt=0,usageFor=null,lastStatus={},answerCount=0,tools={},spawned=[],spawnedBy={},needed=new Set(),boardData=null,query='',drafts=saved.get('drafts',{});
const KIND={claude:'Claude',codex:'Codex',llm:'local model',opencode:'OpenCode',user:'you',board:'board'};

function el(tag,cls,text){const e=document.createElement(tag);if(cls)e.className=cls;
  if(text!==undefined)e.textContent=text;return e;}
function btn(text,fn,cls){const b=el('button',cls||'',text);b.type='button';b.onclick=fn;return b;}
// a name's own color and avatar, the same in chat, Agents and Needs an answer: per project, agents get
// distinct hues in join order (other names hash to one), and a renamed agent's old name shows as its new one
const HUES=[250,145,300,75,195,320,110,225,165,275,90,205];  // no reds: red means an agent needs you
let huesBy={},renamesBy={},agentsBy={};
function hash(name){let h=0;for(const c of name)h=(h*31+c.charCodeAt(0))>>>0;return h;}
function current(name,pid=cur){const r=renamesBy[pid]||{},seen=new Set();
  while(name in r&&!seen.has(name)){seen.add(name);name=r[name];}return name;}
function hue(name,pid=cur){name=current(name,pid);const h=huesBy[pid]||{};return name in h?h[name]:HUES[hash(name)%HUES.length];}
function assignHues(){huesBy={};for(const [pid,list] of Object.entries(agentsBy)){const h=huesBy[pid]={};
  [...list].sort((x,y)=>x.joined<y.joined?-1:1).forEach((a,i)=>{h[a.name]=HUES[i%HUES.length];});}}
function who(node,name,pid=cur){node.classList.add('who');node.style.setProperty('--h',hue(name||'?',pid));return node;}
function avatar(name,pid=cur){name=current(name||'?',pid);const d=who(el('div','av',name==='user'?'Y':name[0]),name,pid);
  if(typeof decorateAvatar==='function')decorateAvatar(d,name,pid);return d;}
function ago(iso){const s=(Date.now()-Date.parse(iso))/1000;
  return s<60?'just now':s<3600?Math.floor(s/60)+'m ago':s<86400?Math.floor(s/3600)+'h ago':Math.floor(s/86400)+'d ago';}
function day(iso){const d=new Date(iso),today=new Date(),y=new Date(today-864e5);
  return d.toDateString()===today.toDateString()?'Today':d.toDateString()===y.toDateString()?'Yesterday':
    d.toLocaleDateString(undefined,{weekday:'long',month:'long',day:'numeric'});}
async function api(path,body){
  const r=await fetch(path,body?{method:'POST',headers:{'Content-Type':'application/json','X-Bullpen':'1'},
    body:JSON.stringify(body)}:{headers:{'X-Bullpen':'1'}});
  const x=r.status===204?null:await r.json();
  if(!r.ok)throw new Error(x&&x.error||r.statusText);return x;}
function away(){return document.hidden||!document.hasFocus();}
function lastN(id){const l=msgs[id]||[];return l.length?l[l.length-1].n:0;}
function unread(p){return (msgs[p.id]||[]).filter(m=>m.n>(seen[p.id]||0)&&m.from!=='user').length;}
function notify(p,m){if(window.Notification&&Notification.permission==='granted')
  new Notification(m.from+' in '+p.name,{body:m.text.slice(0,300),tag:'bullpen-'+p.id+'-'+m.n});}
function say(note,err){$('note').textContent=note||'';$('err').textContent=err||'';}

async function refresh(){
  if(busy)return;busy=true;
  try{
    projects=(await api('api/projects')).projects;
    if(!projects.some(p=>p.id===cur))cur=projects.length?projects[0].id:null;
    for(const p of projects){
      const list=msgs[p.id]||(msgs[p.id]=[]);
      const got=await api(`api/projects/${p.id}/messages?after=${lastN(p.id)}`),fresh=got.messages;
      reactsBy[p.id]=got.reactions||{};pinsBy[p.id]=got.pins||[];
      list.push(...fresh);
      if(notified[p.id]===undefined){  // first load: history is not new
        notified[p.id]=lastN(p.id);if(seen[p.id]===undefined)seen[p.id]=lastN(p.id);continue;}
      for(const m of fresh)if(m.from!=='user'&&m.n>notified[p.id]&&(away()||p.id!==cur))notify(p,m);
      notified[p.id]=lastN(p.id);
    }
    const p=projects.find(p=>p.id===cur);
    const lists=await Promise.all(projects.map(p=>api(`api/projects/${p.id}/agents`)));  // every project's, for colors
    agentsBy={};renamesBy={};projects.forEach((p,i)=>{agentsBy[p.id]=lists[i].agents;renamesBy[p.id]=lists[i].renames||{};avatarsBy[p.id]=lists[i].avatars||{};});
    for(const q of projects)for(const a of agentsBy[q.id]){  // a Claude agent that was listening and no longer is
      const k=q.id+'/'+a.name,was=lastStatus[k];lastStatus[k]=a.status;
      if(a.kind==='claude'&&a.status==='offline'&&(was==='waiting'||was==='busy')){
        notify(q,{from:a.name,text:'stopped listening to the chat. Right-click it: Remind or Resume.',n:'off-'+k+Date.now()});
        if(q.id===cur)say(a.name+' stopped listening to the chat. Right-click it: Remind or Resume.');}}
    agents=agentsBy[cur]||[];
    // every project's starts: an agent stuck on a question in another project must reach you too
    const [starts,board]=await Promise.all([Promise.all(projects.map(p=>api(`api/projects/${p.id}/spawned`).then(x=>x.spawned))),
      cur?api(`api/projects/${cur}/board`):null]);
    spawnedBy={};projects.forEach((p,i)=>{spawnedBy[p.id]=starts[i];});spawned=spawnedBy[cur]||[];boardData=board;
    assignHues();
    if(cur&&(usageFor!==cur||Date.now()-usageAt>30e3)){usageAt=Date.now();usageFor=cur;  // every 30 s is plenty
      const pid=cur;api(`api/projects/${pid}/usage`).then(r=>{usageBy[pid]=r.usage;drawRoster();}).catch(()=>{});}
    for(const q of projects)for(const s of spawnedBy[q.id])if(s.state==='needs_you'&&!needed.has(s.token))
      notify(q,{from:s.name||('new '+(KIND[s.tool]||s.tool)),text:'needs you',n:'need-'+s.token});
    needed=new Set(projects.flatMap(q=>spawnedBy[q.id]).filter(s=>s.state==='needs_you').map(s=>s.token));
    if(cur&&!away()&&atEnd())seen[cur]=lastN(cur);
    saved.set('seen',seen);if(down){say();down=false;}render();
    if(typeof drawBoard==='function'&&!$('boardview').hidden&&boardData)drawBoard(boardData);
    if(typeof drawEpics==='function'&&!$('epicview').hidden&&boardData)drawEpics(boardData);
  }catch(e){down=true;say('','Cannot reach the bullpen service: '+e.message+'. Is it running? systemctl --user status bullpen');}
  finally{busy=false;}
}
function atEnd(){return log.scrollHeight-log.scrollTop-log.clientHeight<60;}

function select(id){
  if(cur)drafts[cur]=t.value;saved.set('drafts',drafts);
  cur=id;saved.set('cur',cur);say();replyTo=null;dmTo=null;drawReply();query='';$('search').value='';t.value=drafts[cur]||'';grow();
  document.body.classList.remove('rail');if(typeof bdrawn!=='undefined')bdrawn='';refresh();}
// the project list's order is the service's: drag a project, or Alt+Shift+↑/↓ the open one
async function saveOrder(ids){projects=ids.map(id=>projects.find(p=>p.id===id));render();
  try{projects=(await api('api/projects/order',{ids})).projects;}catch(e){say('',e.message);}refresh();}
function moveProject(id,by){const ids=projects.map(p=>p.id),i=ids.indexOf(id),j=i+by;
  if(i<0||j<0||j>=ids.length)return;ids.splice(j,0,ids.splice(i,1)[0]);saveOrder(ids);}
let dragging=null;
$('projects').ondragstart=e=>{const d=e.target.closest('.p');if(!d)return;dragging=d.dataset.id;
  e.dataTransfer.setData('text/plain',dragging);e.dataTransfer.effectAllowed='move';d.classList.add('dragging');};
$('projects').ondragover=e=>{const d=e.target.closest('.p');if(!dragging||!d)return;e.preventDefault();
  for(const x of $('projects').querySelectorAll('.p'))x.classList.remove('dropbefore','dropafter');
  const r=d.getBoundingClientRect();d.classList.add(e.clientY<r.top+r.height/2?'dropbefore':'dropafter');};
$('projects').ondragend=()=>{dragging=null;for(const x of $('projects').querySelectorAll('.p'))x.classList.remove('dragging','dropbefore','dropafter');};
$('projects').ondrop=e=>{e.preventDefault();const d=e.target.closest('.p');if(!dragging||!d||d.dataset.id===dragging)return;
  const ids=projects.map(p=>p.id).filter(x=>x!==dragging),at=ids.indexOf(d.dataset.id)+(d.classList.contains('dropafter')?1:0);
  ids.splice(at,0,dragging);saveOrder(ids);};
function render(){
  if(typeof drawMe==='function')drawMe();
  $('projects').replaceChildren(...projects.map((p,i)=>{
    const d=el('div','p'+(p.id===cur?' on':'')+(p.missing?' missing':''));
    d.title=p.path+(p.missing?' (folder missing)':'');d.append(el('span','pn',p.name));
    const n=unread(p),ask=(spawnedBy[p.id]||[]).filter(s=>s.state==='needs_you').length;
    if(ask){const b=el('span','badge need','!');b.title=ask+' agent'+(ask>1?'s':'')+' waiting for you in the terminal';d.append(b);}
    if(n)d.append(el('span','badge',String(n)));else if(i<9&&!ask)d.append(el('kbd','','Alt+'+(i+1)));
    d.onclick=()=>select(p.id);d.oncontextmenu=e=>projectMenu(e,p);d.draggable=true;d.dataset.id=p.id;return d;}));
  if(typeof drawAnswers==='function')drawAnswers();const total=projects.reduce((s,p)=>s+unread(p),0);
  document.title=(needed.size||answerCount?'● ':'')+(total?`(${total}) `:'')+'bullpen';
  const p=projects.find(p=>p.id===cur);
  $('name').textContent=p?p.name:'bullpen';$('path').textContent=p?p.path:'';
  const open=boardData?boardData.cards.filter(c=>c.column!=='done'):[];
  $('boardcount').textContent=String(open.filter(c=>c.kind!=='epic').length||'');
  $('epiccount').textContent=String(open.filter(c=>c.kind==='epic').length||'');
  drawNeeds();drawRoster();hint();
  const key=cur+':'+lastN(cur)+':'+query+':'+agents.map(a=>a.name).join()+JSON.stringify(reactsBy[cur]||{})+(pinsBy[cur]||[]);if(key===drawn)return;
  const switched=!drawn.startsWith(cur+':'),end=atEnd(),before=drawn;drawn=key;
  drawLog(p);
  if(end||switched||!before)log.scrollTop=log.scrollHeight;else $('jump').hidden=false;
}

// transcript: messages grouped by speaker, board notices as one line, a rule per day
function drawLog(p){
  drawPins();
  if(!p){log.replaceChildren(empty('Add a project to start',
    'Choose + next to Projects and give a folder, or run bullpen add <folder> in a terminal.'));return;}
  const q=query.toLowerCase(),all=msgs[p.id]||[];
  const list=q?all.filter(m=>m.text.toLowerCase().includes(q)||m.from.includes(q)):all;
  if(!all.length){log.replaceChildren(empty('No messages yet',
    'Start an agent from the Agents list, or run claude "join the chat" in '+p.path+'. Then say hello.'));return;}
  if(!list.length){log.replaceChildren(empty('Nothing matches “'+query+'”','Clear the search to see every message.'));return;}
  const out=[];let g=null,lastDay='';
  for(const m of list){
    const d=day(m.time);if(d!==lastDay){out.push(el('div','day',d));lastDay=d;g=null;}
    const time=m.time.slice(11,16);
    if(m.kind==='board'){g=null;const n=el('div','notice');n.dataset.n=m.n;n.append(boardText(m.text),el('span','t',time));
      const prev=out[out.length-1];  // three or more board changes in a row fold into one line
      if(prev&&prev.classList.contains('notices')){prev.lastChild.append(n);prev.firstChild.textContent=prev.lastChild.children.length+' board changes';}
      else if(prev&&prev.classList.contains('notice')&&out[out.length-2]&&out[out.length-2].classList.contains('notice')){
        const f=el('details','notices'),inner=el('div'),id=out[out.length-2].dataset.n;inner.append(out[out.length-2],prev,n);
        f.open=folds.has(id);f.ontoggle=()=>{folds[f.open?'add':'delete'](id);};
        f.append(el('summary','','3 board changes'),inner);out.splice(-2,2,f);}
      else out.push(n);continue;}
    const from=current(m.from);
    if(!g||g.from!==from||Date.parse(m.time)-g.at>5*60e3){
      g={from,at:Date.parse(m.time),node:who(el('div','g '+m.kind),from)};
      const h=el('div','gh'),n=el('span','n',from==='user'?myName():from);if(from!==m.from)n.title='posted as '+m.from;h.append(n,
        el('span','',(m.kind!=='user'?(KIND[m.kind]||m.kind)+'  ':'')+time));
      g.node.append(avatar(from),h);out.push(g.node);}
    g.at=Date.parse(m.time);g.node.append(message(m,time,q));}
  log.replaceChildren(...out);}
function empty(title,text){const d=el('div','empty');d.append(el('h2','',title),el('p','',text));return d;}
function message(m,time,q){
  const d=el('div','m rich'+(q?' hit':''));d.id='m'+m.n;
  if(m.dm)d.classList.add('dm');
  if(m.reply)d.append(quote(m.reply));
  if(m.dm)d.append(el('span','dmtag','Private: you and '+m.dm));
  const body=renderText(m.text);if(typeof addPrChips==='function')addPrChips(body);
  d.append(body,el('span','t',time));
  d.title=new Date(m.time).toLocaleString();
  const acts=el('div','acts');
  acts.append(btn('Reply',()=>{const from=current(m.from);replyTo={n:m.n,from,text:m.text};dmTo=m.dm?current(m.dm):null;drawReply();
    if(from!=='user')t.value='@'+from+' '+t.value.replace(/^@[\w-]+\s*/,'');t.focus();grow();hint();}));
  acts.append(btn('React',ev=>{ev.stopPropagation();pickReaction(ev,m);}),btn(isPinned(m.n)?'Unpin':'Pin',()=>togglePin(m.n)),
    btn('Copy',()=>navigator.clipboard.writeText(m.text)),btn('Add to board',()=>cardFrom(m)));
  d.append(reactRow(m),acts);return d;}
// a reply's quote of the message it answers; a click shows that message
function quote(r){const q=who(el('button','quote'),r.from);q.type='button';q.title='Show the message this answers';
  q.append(el('b','',r.from==='user'?myName():current(r.from)),el('span','',' '+r.text.replace(/\s+/g,' ').slice(0,140)));
  q.onclick=()=>{const o=$('m'+r.n);if(!o){say('That message is not shown; clear the search.');return;}
    o.scrollIntoView({block:'center'});o.classList.remove('flash');void o.offsetWidth;o.classList.add('flash');};return q;}
let replyTo=null,dmTo=null;  // the message the composer answers; the agent it writes to privately
function drawReply(){const bar=$('replybar');bar.hidden=!replyTo&&!dmTo;hint();if(bar.hidden)return;
  bar.replaceChildren(...(dmTo?[el('span','dmtag','Private to '+dmTo)]:[]),...(replyTo?[el('span','','Replying to '),quote(replyTo)]:[el('span','grow')]),
    btn('×',()=>{replyTo=null;dmTo=null;drawReply();t.focus();},'icon'));
  bar.lastChild.setAttribute('aria-label',replyTo?'Cancel the reply':'Write to everyone instead');}
function boardText(text){  // "#3" in a board notice opens that card
  const f=document.createDocumentFragment();
  text.split(/(#\d+|@[\w-]+)/).forEach((part,i)=>{if(!(i%2)){if(part)f.append(part);return;}
    if(part[0]==='@'){f.append(who(el('span','mention',part),part.slice(1)));return;}
    const a=el('a','',part);a.tabIndex=0;a.onclick=()=>typeof openCardById==='function'&&openCardById(+part.slice(1));f.append(a);});
  return f;}
async function cardFrom(m){
  const first=m.text.replace(/```[\s\S]*?```/g,' ').split('\n').find(l=>l.trim())||m.text;
  const title=await ask({title:'Add to board',label:'Card title',value:first.trim().slice(0,200),
    help:`The card's description keeps ${m.from==='user'?'your':m.from+"'s"} whole message.`});
  if(!title)return;
  try{await api(`api/projects/${cur}/board/cards`,{by:'user',title,column:'todo',
    description:(m.text+`\n\n(from ${m.from==='user'?'you':m.from}, message #${m.n})`).slice(0,4000)});
    say('Added to the board’s To do column.');}catch(e){say('','Not added: '+e.message);}refresh();}
$('jump').onclick=()=>{log.scrollTop=log.scrollHeight;$('jump').hidden=true;};
log.onscroll=()=>{if(atEnd()){$('jump').hidden=true;if(cur&&!away()&&seen[cur]!==lastN(cur)){seen[cur]=lastN(cur);saved.set('seen',seen);render();}}};
$('search').oninput=()=>{query=$('search').value.trim();render();};
$('search').onkeydown=e=>{if(e.key==='Escape'){$('search').value='';query='';render();t.focus();}};

// agents that need you: a banner above the chat
function drawNeeds(){
  $('needs').replaceChildren(...spawned.filter(s=>s.state==='needs_you').map(s=>{
    const d=el('div','need'),name=s.name||(KIND[s.tool]||s.tool)+' (starting)';
    const b=el('b','',name),text=el('span');text.append(b,' is waiting for your answer in its terminal.');
    d.append(text,btn('Open terminal',()=>openTerm(s.token,name)));return d;}));}

// roster: groups by state, right-click or click for options
const GROUPS=[['needs_you','Needs you'],['starting','Starting'],['waiting','Available'],
  ['busy','Working'],['offline','Offline'],['removed','Removed']];
const kfmt=n=>n==null?'?':n>=1e6?(n/1e6).toFixed(n>=1e7?0:1)+'M':n>=1e3?Math.round(n/1e3)+'k':String(n);
function usageLine(u){  // Claude: context and tokens out; Codex: context of its window and plan limits; local: memory
  let text,title;
  if(u.out!=null){text=`ctx ${kfmt(u.ctx)} · ${kfmt(u.out)} out`;title=`${u.model||'Claude'}: ${u.ctx} tokens of context in use, ${u.out} tokens written this session`;}
  else if(u.window!=null){const l=u.limits||{};text=`ctx ${kfmt(u.ctx)}/${kfmt(u.window)}`+(l.primary!=null?` · 5h ${Math.round(l.primary)}%`:'')+(l.secondary!=null?` · wk ${Math.round(l.secondary)}%`:'');
    title=`Codex: ${u.ctx} of ${u.window} context tokens; ${u.total} tokens this session; plan limits used: 5 hours ${l.primary}%, week ${l.secondary}%`;}
  else{text=u.loaded?`ctx ${kfmt(u.ctx)} · ${(u.vram/2**30).toFixed(1)} GB`:'not loaded';title=`${u.model}: `+(u.loaded?`context ${u.ctx} tokens, ${u.vram} bytes of video memory`:'not in memory now');}
  const s=el('span','usage',text);s.title=title;return s;}
function drawRoster(){
  const starting=spawned.filter(s=>!s.name).map(s=>({name:KIND[s.tool]||s.tool,kind:s.tool,starting:true,
    spawn:s.token,status:s.state==='needs_you'?'needs_you':'starting'}));
  const members=[...starting,...agents];
  if(!members.length){$('members').replaceChildren(el('p','rosterempty',cur?
    'Nobody here yet. Start Claude, Codex or a local model with Start agent.':'Add a project first.'));return;}
  $('members').replaceChildren(...GROUPS.flatMap(([st,title])=>{
    const g=members.filter(a=>a.status===st);if(!g.length)return [];
    return [el('h3','',title+' ('+g.length+')'),...g.map(a=>{const d=who(el('div','a '+a.status),a.name);
      const sub=a.starting?'starting…':a.model?a.model:a.kind==='opencode'?'OpenCode '+((spawned.find(s=>s.token===a.spawn)||{}).model||''):KIND[a.kind]||a.kind;
      d.title=a.error?'Offline: '+a.error:'Click or right-click for options';
      d.append(avatar(a.name),el('b','',a.starting?'new '+a.name:a.name),
        el('small','',sub+(a.last_seen&&['offline','removed'].includes(a.status)?', seen '+ago(a.last_seen):'')));
      if(a.personality)d.append(el('span','pers',a.personality));
      const doing=(boardData?boardData.cards:[]).filter(c=>c.assignee===a.name&&c.column==='doing'&&c.kind!=='epic');
      if(doing.length){const w=el('span','doing','▶ '+doing.map(c=>'#'+c.id+' '+c.title).join(', '));  // its In progress cards
        w.title='In progress: '+doing.map(c=>'#'+c.id+' '+c.title).join('; ');d.append(w);}
      const u=(usageBy[cur]||{})[a.name];if(u&&!a.starting)d.append(usageLine(u));
      d.onclick=d.oncontextmenu=e=>{e.stopPropagation();openMenu(e,agentItems(a));};return d;})];}));}
const menu=$('menu');
function openMenu(e,items){
  e.preventDefault();menu.classList.remove('emojis');
  menu.replaceChildren(...items.map(it=>{if(!it)return el('hr');const [text,fn,off]=it,item=el('div',off?'off':'',text);
    item.setAttribute('role','menuitem');if(off)item.title=off;
    item.onclick=async ev=>{ev.stopPropagation();if(off)return;menu.hidden=true;
      try{const r=await fn();if(typeof r==='string')say(r);}catch(err){say('',err.message);}refresh();};return item;}));
  menu.hidden=false;const r=menu.getBoundingClientRect();
  menu.style.left=Math.max(8,Math.min(e.clientX,innerWidth-r.width-8))+'px';
  menu.style.top=Math.max(8,Math.min(e.clientY,innerHeight-r.height-8))+'px';}
const path=(token,x)=>`api/projects/${cur}/spawned/${token}/${x}`;
const agentPath=(a,x)=>`api/projects/${cur}/agents/${encodeURIComponent(a.name)}/${x}`;
const NO_TMUX=navigator.platform.startsWith('Win')?'Needs tmux: run bullpen in WSL to start agents from the page':'tmux is not installed';
function agentItems(a){
  const items=[];
  if(!a.starting&&a.status!=='removed')items.push(['Message '+a.name,()=>{t.value='@'+a.name+' ';t.focus();grow();hint();}],
    ['Message '+a.name+' privately',()=>{dmTo=a.name;replyTo=null;t.value='@'+a.name+' ';drawReply();t.focus();grow();}]);
  if(a.spawn&&a.status==='offline'&&a.kind==='claude')  // its terminal is there, its wait is not
    items.push(['Remind to listen',async()=>{await api(path(a.spawn,'keys'),{text:'Your bullpen wait stopped. Restart it in the background now (the command from your join), and keep it running.'});return `Reminded ${a.name}.`;}]);
  if(a.spawn){items.push(['View terminal',()=>openTerm(a.spawn,a.starting?a.name+' (starting)':a.name)]);
    items.push(['Stop',()=>{if(confirm(`Stop ${a.name}? Its tmux session ends.`))return api(path(a.spawn,'stop'),{});}]);}
  if(a.status==='offline'&&!a.spawn&&(a.kind==='claude'||a.kind==='codex'||a.kind==='opencode'))
    items.push(['Resume',async()=>{await api(agentPath(a,'resume'),{});return `Resuming ${a.name}…`;}]);
  if(a.starting)return items;
  if(!a.starting&&a.status!=='removed')items.push(['Picture…',()=>openProfile(cur,a.name)]);
  items.push(['Rename',async()=>{const n=await ask({title:'Rename '+a.name,label:'New name',value:a.name,
    help:"a-z, 0-9 and '-'. Its color, personality and board cards move along, and its running session keeps working."});
    if(n&&n.trim().toLowerCase()!==a.name){await api(agentPath(a,'rename'),{name:n.trim()});return `${a.name} is now ${n.trim().toLowerCase()}.`;}}]);
  items.push(['Edit personality',async()=>{const p=await askPersonality(a.personality,a.name);
    if(p!==null)return api(agentPath(a,'personality'),{personality:p||null});}],null);
  const [text,action]=a.status==='removed'?['Add back','readd']:['Remove from chat','remove'];
  items.push([text,()=>api(agentPath(a,action),{})]);
  if(a.status==='removed')items.push(['Forget',()=>{if(confirm(`Forget ${a.name}? Its name becomes free again; its messages stay.`))
    return api(agentPath(a,'forget'),{});}]);
  return items;}
const PRESETS=[['Reviewer','You review changes critically: correctness first, then clarity. Point to exact lines.'],
  ['Architect','You think about structure and trade-offs before code, and keep designs small.'],
  ['Tester','You look for how things break and write the smallest test that shows it.'],
  ['Terse helper','You answer in as few words as possible.']];
function askPersonality(current,name){  // null: cancelled; '': none
  return ask({title:'Personality for '+name,label:'How this agent should work. It reaches the agent with every message.',
    value:current||'',multiline:true,presets:PRESETS,help:'Leave empty for none.'}).then(r=>r===null?null:r.trim());}
async function startWith(tool,model){
  const p=await askPersonality('',model?`OpenCode (${model})`:KIND[tool]);if(p===null)return;
  await api(`api/projects/${cur}/spawned`,{tool,model,personality:p||null});return `Starting ${model?'OpenCode with '+model:KIND[tool]}…`;}
function startItems(){
  const items=['claude','codex'].map(tool=>{
    const off=!cur?'Add a project first':!tools.tmux?NO_TMUX:!tools[tool]?`${KIND[tool]} is not installed`:'';
    return ['Start '+KIND[tool],()=>startWith(tool),off];});
  items.push(null);
  const ocOff=!cur?'Add a project first':!tools.tmux?NO_TMUX:!tools.opencode?'OpenCode is not installed':
    !locals.ok?locals.reason:'';
  if(ocOff||!oc.models.length)items.push(['Start OpenCode',()=>{},ocOff||'No model that can call tools; get one in Local models']);
  else for(const m of oc.models)items.push(['Start OpenCode: '+m+(m===oc.default?' (default)':''),()=>startWith('opencode',m),'']);
  items.push(null);
  if(!locals.ok)items.push(['Add local model',()=>{},locals.reason]);
  else if(!locals.names.length)items.push(['Add local model',()=>{},'No models installed; get one in Local models']);
  else for(const n of locals.names)items.push(['Add local model: '+n,()=>addLocal(n),cur?'':'Add a project first']);
  return items;}
$('addagent').onclick=e=>{e.stopPropagation();const b=e.currentTarget.getBoundingClientRect();
  openMenu({preventDefault(){},clientX:b.right-210,clientY:b.bottom+4},startItems());};
$('roster').oncontextmenu=e=>openMenu(e,startItems());
addEventListener('click',()=>{menu.hidden=true;});
addEventListener('keydown',e=>{if(e.key==='Escape'){menu.hidden=true;ac.hidden=true;}});

// composer: who a message goes to, drafts, @ autocomplete
// the names a message is addressed to: the @names it starts with
const lead=text=>((text.match(/^\s*(@[\w-]+[,:]?\s*)+/)||[''])[0].match(/@[\w-]+/g)||[]).map(x=>x.slice(1).toLowerCase());
function addressees(){return lead(t.value);}
function hint(){
  const to=addressees(),live=agents.filter(a=>a.status!=='removed');
  const line=$('to');
  if(t.value.startsWith('/')&&!t.value.startsWith('//'))line.textContent='Command: Enter runs it. Press ? for the list.';
  else if(dmTo)line.textContent=`Private: only ${dmTo} reads it; the other agents never see it.`;
  else if(to.length){line.replaceChildren('To ');to.forEach((n,i)=>line.append(i?', ':'',who(el('span','',n),n)));
    line.append('. The others read it but do not answer.');}
  else line.textContent=live.length?`To everyone: ${live.map(a=>a.name).join(', ')} may answer.`:'';
  $('hint').textContent=agents.filter(a=>to.includes(a.name)&&a.status!=='waiting').map(a=>
    a.status==='busy'?`${a.name} is working and will see this when its current task ends.`:
    a.status==='removed'?`${a.name} was removed from this chat.`:
    a.status==='needs_you'?`${a.name} is waiting for you in its terminal.`:`${a.name} is offline.`).join(' ');}
function grow(){t.style.height='auto';t.style.height=t.scrollHeight+'px';}
let matches=[],sel=0,field=t;  // field: the box @ autocomplete works in
function token(){const m=field.value.slice(0,field.selectionStart).match(/(^|\s)@([\w-]*)$/);return m?m[2]:null;}
function showAc(){
  const q=token();
  matches=q===null?[]:agents.filter(a=>a.status!=='removed'&&a.name.startsWith(q.toLowerCase()));
  if(!matches.length){ac.hidden=true;return;}
  sel=Math.min(sel,matches.length-1);
  const r=field.closest('.box').getBoundingClientRect();
  ac.style.left=r.left+'px';ac.style.bottom=(innerHeight-r.top+4)+'px';
  ac.replaceChildren(...matches.map((a,i)=>{const d=who(el('div',i===sel?'sel':''),a.name);
    d.append(el('b','','@'+a.name),el('small','',GROUPS.find(g=>g[0]===a.status)[1]));d.setAttribute('role','option');
    d.onmousedown=e=>{e.preventDefault();pick(i);};return d;}));
  ac.hidden=false;}
function pick(i){
  const pos=field.selectionStart,q=token(),start=pos-q.length-1,ins='@'+matches[i].name+' ';
  field.value=field.value.slice(0,start)+ins+field.value.slice(pos);
  field.selectionStart=field.selectionEnd=start+ins.length;ac.hidden=true;sel=0;field.focus();hint();}
function acKeys(e){  // arrows, Enter, Tab and Esc in an open autocomplete list; true when used
  if(ac.hidden)return false;
  if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();
    sel=(sel+(e.key==='ArrowDown'?1:matches.length-1))%matches.length;showAc();return true;}
  if(e.key==='Enter'||e.key==='Tab'){e.preventDefault();pick(sel);return true;}
  if(e.key==='Escape'){ac.hidden=true;return true;}
  return false;}
function mentions(box){box.onclick=()=>{field=box;showAc();};box.onblur=()=>{ac.hidden=true;};}
mentions(t);
t.oninput=()=>{field=t;sel=0;showAc();hint();grow();if(cur){drafts[cur]=t.value;saved.set('drafts',drafts);}};
t.onkeydown=e=>{
  if(acKeys(e))return;
  if(e.key==='Escape'&&(replyTo||dmTo)){replyTo=null;dmTo=null;drawReply();return;}
  if(e.key==='ArrowUp'&&!t.value&&lastSent){e.preventDefault();t.value=lastSent;grow();return;}
  if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();$('f').requestSubmit();}};
let lastSent='';
$('f').onsubmit=async e=>{e.preventDefault();
  const text=t.value.trim();if(!text||!cur)return;t.value='';grow();ac.hidden=true;delete drafts[cur];saved.set('drafts',drafts);
  if(text.startsWith('/')&&!text.startsWith('//')){
    try{say(await runCommand(text));lastSent=text;}catch(e){t.value=text;say('',e.message);}
    grow();refresh();return;}
  try{await api(`api/projects/${cur}/messages`,{from:'user',text:text.startsWith('//')?text.slice(1):text,
      ...(replyTo?{reply:replyTo.n}:{}),...(dmTo?{dm:dmTo}:{})});
    say();lastSent=text;replyTo=null;dmTo=null;drawReply();log.scrollTop=log.scrollHeight;}
  catch(e){t.value=text;say('','Not sent: '+e.message);}
  grow();hint();refresh();};

// dialogs: ask() for text, close buttons, the terminal
function ask({title,label,value='',multiline=false,presets=[],help='',placeholder=''}){
  const d=$('ask'),input=multiline?$('askarea'):$('askinput');
  $('asktitle').textContent=title;$('asklabel').textContent=label;$('asklabel').htmlFor=input.id;$('askhelp').textContent=help;
  $('askinput').hidden=multiline;$('askarea').hidden=!multiline;input.value=value;input.placeholder=placeholder;
  $('askpresets').replaceChildren(...presets.map(([n,text])=>{const b=btn(n,()=>{input.value=text;mark();input.focus();},'ghost');return b;}));
  const mark=()=>[...$('askpresets').children].forEach((b,i)=>b.classList.toggle('on',presets[i][1]===input.value));
  input.oninput=mark;mark();
  d.returnValue='';d.showModal();input.focus();input.select();
  input.onkeydown=e=>{if(multiline&&e.key==='Enter'&&(e.ctrlKey||e.metaKey)){e.preventDefault();d.close('ok');}};
  return new Promise(res=>d.addEventListener('close',()=>res(d.returnValue==='ok'?input.value:null),{once:true}));}
$('ask').querySelector('form').onsubmit=e=>{e.preventDefault();$('ask').close('ok');};
for(const d of document.querySelectorAll('dialog'))
  for(const b of d.querySelectorAll('button[value=close]'))b.onclick=()=>d.close();
let termToken=null,termTimer=null;
const KEYBTNS=[['1','1'],['2','2'],['3','3'],['↑','up'],['↓','down'],['Enter','enter'],['Esc','esc']];
$('termkeys').replaceChildren(...KEYBTNS.map(([label,key])=>btn(label,()=>sendKeys({key}),'ghost')));
async function sendKeys(body){
  try{await api(path(termToken,'keys'),body);tick();}catch(e){$('screen').textContent+='\n['+e.message+']';}}
async function tick(){
  if(!termToken)return;
  try{const s=$('screen'),end=s.scrollHeight-s.scrollTop-s.clientHeight<30;
    s.textContent=(await api(path(termToken,'screen'))).screen;if(end)s.scrollTop=s.scrollHeight;}
  catch(e){$('screen').textContent='The terminal has ended.';clearInterval(termTimer);}}
function openTerm(token,name){
  termToken=token;$('termtitle').textContent=name+': terminal';
  const s=spawned.find(s=>s.token===token);
  $('attach').textContent=s?`Take over in a terminal: tmux attach -t =${s.session}`:'';
  $('term').showModal();$('ti').value='';tick();clearInterval(termTimer);termTimer=setInterval(tick,1500);$('ti').focus();}
$('term').addEventListener('close',()=>{termToken=null;clearInterval(termTimer);t.focus();});
$('term').addEventListener('cancel',e=>{if(document.activeElement===$('ti')&&$('ti').value)e.preventDefault();});
$('tf').onsubmit=e=>{e.preventDefault();const text=$('ti').value;if(!text)return;$('ti').value='';sendKeys({text});};

$('add').onclick=async()=>{
  const path=await ask({title:'Add a project',label:'Project folder',placeholder:'/home/you/code/project',
    help:'An absolute path. Each project gets its own chat and board.'});
  if(!path||!path.trim())return;
  try{const r=await api('api/projects',{path:path.trim()});select(r.project.id);
    say(r.existing?`${r.project.name} already exists; opened it.`:`Added ${r.project.name}.`);
  }catch(e){say('','Not added: '+e.message);}};
function bellLabel(){
  if(!window.Notification){bell.hidden=true;return;}
  bell.hidden=Notification.permission==='granted';
  bell.textContent=Notification.permission==='denied'?'Blocked':'Notifications';  // short: the header also has Rules
  bell.title=Notification.permission==='denied'?'Notifications are blocked in this browser':'Enable notifications';}
bell.onclick=async()=>{await Notification.requestPermission();bellLabel();};bellLabel();
addEventListener('focus',refresh);document.addEventListener('visibilitychange',refresh);
const THEMES=['auto','light','dark'];let theme=saved.get('theme','auto');
function applyTheme(){if(theme==='auto')delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme=theme;$('theme').textContent='Theme: '+theme;}
$('theme').onclick=()=>{theme=THEMES[(THEMES.indexOf(theme)+1)%3];saved.set('theme',theme);applyTheme();};applyTheme();
$('menubtn').onclick=e=>{e.stopPropagation();document.body.classList.toggle('rail');};
$('rosterbtn').onclick=e=>{e.stopPropagation();document.body.classList.toggle('roster');};
$('roster').addEventListener('click',e=>e.stopPropagation());
addEventListener('click',()=>document.body.classList.remove('roster'));
document.querySelector('main').addEventListener('click',()=>document.body.classList.remove('rail'));

api('api/tools').then(x=>{tools=x;}).catch(()=>{});
// installed local models for the start menu, refreshed every 30 s
let locals={ok:false,reason:'Checking Ollama…',names:[]},oc={models:[],default:null};
async function loadLocals(){
  try{const s=await api('api/models/status');
    if(!s.reachable){locals={ok:false,reason:'Ollama is not reachable',names:[]};return;}
    locals={ok:true,reason:'',names:(await api('api/models')).models.map(m=>m.name)};
    oc=await api('api/opencode/models').catch(()=>({models:[],default:null}));}
  catch(e){locals={ok:false,reason:e.message,names:[]};oc={models:[],default:null};}}
loadLocals();setInterval(loadLocals,30000);
function suggestName(model){return model.split(':')[0].split('/').pop().toLowerCase()
  .replace(/[^a-z0-9-]+/g,'-').replace(/^-+|-+$/g,'')||'local';}
async function addLocal(model,name){
  let role=null;
  if(name===undefined){name=await ask({title:'Add '+model,label:'Its name in this chat',value:suggestName(model)});
    if(!name)return;role=await askPersonality('',name.trim());if(role===null)return;}
  await api(`api/projects/${cur}/locals`,{model,name:name.trim(),role:role||null});
  return `Added ${name.trim()} (${model}).`;}

// keyboard shortcuts (Alt, since the browser owns most Ctrl keys) and commands
const typing=()=>['TEXTAREA','INPUT','SELECT'].includes((document.activeElement||{}).tagName);
function stepProject(by){const i=projects.findIndex(p=>p.id===cur);
  if(projects.length)select(projects[(i+by+projects.length)%projects.length].id);}
function needsYou(){  // the first question for you, else the first started agent whose terminal asks
  if(typeof focusAnswer==='function'&&focusAnswer())return;
  const s=spawned.find(s=>s.state==='needs_you');if(s)openTerm(s.token,s.name||(KIND[s.tool]||s.tool)+' (starting)');
  else say('No agent here needs you.');}
addEventListener('keydown',e=>{
  if(document.querySelector('dialog[open]'))return;
  if(e.altKey&&!e.ctrlKey&&!e.metaKey){
    const go=f=>{e.preventDefault();f();};
    if(e.key==='ArrowUp')return go(()=>e.shiftKey?moveProject(cur,-1):stepProject(-1));
    if(e.key==='ArrowDown')return go(()=>e.shiftKey?moveProject(cur,1):stepProject(1));
    const digit=/^Digit([1-9])$/.exec(e.code);
    if(digit)return go(()=>{const p=projects[digit[1]-1];if(p)select(p.id);});
    if(e.code==='KeyU')return go(()=>{const i=projects.findIndex(p=>p.id===cur);
      const p=[...projects.slice(i+1),...projects.slice(0,i+1)].find(p=>unread(p)&&p.id!==cur);
      if(p)select(p.id);else say('No unread messages elsewhere.');});
    if(e.code==='KeyN')return go(needsYou);
    if(e.code==='KeyA')return go(()=>$('addagent').click());
    if(e.code==='KeyB')return go(()=>showView($('boardview').hidden));
  }
  if((e.ctrlKey||e.metaKey)&&e.key==='k'){e.preventDefault();showView(false);$('search').focus();$('search').select();return;}
  if(!typing()&&!e.altKey&&!e.ctrlKey&&!e.metaKey){
    if(e.key==='/'){e.preventDefault();t.focus();}
    else if(e.key==='?'){e.preventDefault();$('help').showModal();}
  }});
$('keys').onclick=()=>$('help').showModal();
async function runCommand(text){
  const [cmd,arg='',arg2='']=text.slice(1).trim().split(/\s+/);
  const agent=()=>{const a=agents.find(a=>a.name===arg.toLowerCase());
    if(!a)throw new Error(arg?`No agent named ${arg} in this project.`:`Say which agent: /${cmd} <name>`);return a;};
  const started=()=>{const a=agent();if(!a.spawn)throw new Error(`${a.name} was not started from the page.`);return a;};
  if(cmd==='start'){
    if(arg==='opencode'){const model=arg2||oc.default;if(!model)throw new Error('No model that can call tools; get one in Local models.');
      await api(`api/projects/${cur}/spawned`,{tool:'opencode',model});return `Starting OpenCode with ${model}…`;}
    if(!['claude','codex'].includes(arg))throw new Error('Use /start claude, /start codex or /start opencode [model].');
    await api(`api/projects/${cur}/spawned`,{tool:arg});return `Starting ${KIND[arg]}…`;}
  if(cmd==='stop'){const a=started();await api(path(a.spawn,'stop'),{});return `Stopped ${a.name}.`;}
  if(cmd==='term'){const a=started();openTerm(a.spawn,a.name);return '';}
  if(cmd==='remove'){const a=agent();await api(agentPath(a,'remove'),{});return `Removed ${a.name}.`;}
  if(cmd==='local'){if(!arg||!arg2)throw new Error('Use /local <model> <name>.');return addLocal(arg,arg2);}
  if(cmd==='card'){const title=text.slice(1).trim().slice(4).trim();if(!title)throw new Error('Use /card <title>.');
    await api(`api/projects/${cur}/board/cards`,{by:'user',title,column:'todo'});return 'Added to the board’s To do column.';}
  throw new Error(`Unknown command /${cmd}. Press ? for the list; //text posts text starting with /.`);}
// after every script, so the first render has markdown.js and answers.js
addEventListener('DOMContentLoaded',()=>{t.value=drafts[cur]||'';grow();refresh();setInterval(refresh,2000);t.focus();});
