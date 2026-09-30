// Kanban board and epics views (bullpen/board.js). Uses the page's $, el, btn, api, avatar, who, cur,
// agents, boardData (loaded with every refresh), saved, say and t. An epic is a card of kind "epic";
// items point to theirs with "epic". The board shows items only, the Epics view groups them.
const bv=$('boardview'),ev=$('epicview'),cp=$('cardpanel'),COLC={todo:'var(--mute)',doing:'var(--busy)',review:'var(--proof)',done:'var(--ok)'};
let bdrawn='',edrawn='',editing=null,adding=null,epicFilter='',closed=saved.get('epicsClosed',{});
function showView(v){  // true/false: board/chat (Alt+B), or 'chat', 'board', 'epics'
  v=v===true?'board':v===false?'chat':v;
  for(const n of ['chat','board','epics'])$('view'+n).classList.toggle('on',n===v);
  $('chat').hidden=v!=='chat';bv.hidden=v!=='board';ev.hidden=v!=='epics';bdrawn=edrawn='';
  history.replaceState(null,'',v==='chat'?location.pathname:'#'+v);
  if(v==='board'&&boardData)drawBoard(boardData);if(v==='epics'&&boardData)drawEpics(boardData);if(v==='chat')t.focus();}
const epicsOf=b=>b.cards.filter(c=>c.kind==='epic'),itemsOf=b=>b.cards.filter(c=>c.kind!=='epic');
function epicHue(e){return `oklch(var(--sl) .13 ${(e.id*67)%360})`;}
function epicChip(b,id){const e=b.cards.find(c=>c.id===id);if(!e)return null;
  const s=el('span','chip',e.title);s.style.setProperty('--ec',epicHue(e));s.title='Epic #'+e.id;return s;}
async function boardCall(path,body){
  try{await api(`api/projects/${cur}/board/${path}`,Object.assign({by:'user'},body));say();}
  catch(e){say('',e.message);}
  refresh();}
function drawBoard(b){
  const key=cur+JSON.stringify(b)+adding+epicFilter+agents.map(a=>a.name);if(key===bdrawn||bv.contains(document.activeElement)&&adding)return;bdrawn=key;
  const epics=epicsOf(b);if(epicFilter&&epicFilter!=='none'&&!epics.some(e=>String(e.id)===epicFilter))epicFilter='';
  const shown=itemsOf(b).filter(c=>!epicFilter||(epicFilter==='none'?!c.epic:String(c.epic)===epicFilter));
  const bar=el('div','bfilter');
  if(epics.length){const sel=el('select');sel.setAttribute('aria-label','Show items of');
    sel.append(new Option('All items',''),...epics.map(e=>new Option('Epic: '+e.title,String(e.id))),new Option('Items without an epic','none'));
    sel.value=epicFilter;sel.onchange=()=>{epicFilter=sel.value;drawBoard(boardData);};bar.append(sel);}
  const cols=el('div','cols');bv.replaceChildren(bar,cols);
  cols.replaceChildren(...b.columns.map(([k,label])=>{
    const col=el('div','col'),h=el('h3'),cards=shown.filter(c=>c.column===k);
    col.style.setProperty('--colc',COLC[k]||'var(--line)');
    h.append(el('span','',label),el('span','count',String(cards.length)));col.append(h);
    col.ondragover=e=>{e.preventDefault();col.classList.add('over');};
    col.ondragleave=e=>{if(!col.contains(e.relatedTarget))col.classList.remove('over');};
    col.ondrop=e=>{e.preventDefault();col.classList.remove('over');
      const id=e.dataTransfer.getData('text/plain');if(/^\d+$/.test(id))boardCall('cards/'+id,{column:k});};
    for(const c of cards){
      const d=el('div','card');d.draggable=true;d.tabIndex=0;d.title='Click to edit, drag to move';
      d.append(el('span','ct',c.title));
      if(c.description)d.append(el('span','cd',c.description));
      const f=el('div','cf');f.append(el('span','','#'+c.id));
      const chip=c.epic&&epicChip(b,c.epic);if(chip)f.prepend(chip);
      if(c.assignee){const a=avatar(c.assignee);a.title='Assigned to '+(c.assignee==='user'?'you':c.assignee);f.append(a);}
      d.append(f);
      d.ondragstart=e=>{e.dataTransfer.setData('text/plain',String(c.id));d.classList.add('dragging');};
      d.ondragend=()=>d.classList.remove('dragging');
      d.onclick=()=>openCard(c);d.onkeydown=e=>{if(e.key==='Enter')openCard(c);};col.append(d);}
    col.append(addForm(k,label));return col;}));}
function addForm(k,label){  // "+ Add a card" turns into a title field in place
  const box=el('div','addcard');
  if(adding!==k){box.append(btn('+ Add a card',()=>{adding=k;bdrawn='';drawBoard(boardData);bv.querySelector('.addcard input').focus();}));return box;}
  const f=el('form'),input=el('input');input.placeholder=`Title, then Enter (${label})`;input.maxLength=200;
  const stop=()=>{adding=null;bdrawn='';drawBoard(boardData);};
  input.onkeydown=e=>{if(e.key==='Escape'){e.stopPropagation();stop();}};input.onblur=()=>{if(!input.value.trim())stop();};
  f.onsubmit=async e=>{e.preventDefault();const title=input.value.trim();if(!title)return;input.value='';
    const epic=/^\d+$/.test(epicFilter)?Number(epicFilter):null;  // added to the epic on show
    await boardCall('cards',{title,column:k,epic});bdrawn='';drawBoard(boardData);bv.querySelector('.addcard input')?.focus();};
  f.append(input);box.append(f);return box;}
function inlineAdd(label,placeholder,key,onAdd){  // a button that turns into a title field
  const box=el('div','addcard');
  if(adding!==key){box.append(btn(label,()=>{adding=key;edrawn='';drawEpics(boardData);ev.querySelector('.addcard input')?.focus();}));return box;}
  const f=el('form'),input=el('input');input.placeholder=placeholder;input.maxLength=200;
  const stop=()=>{adding=null;edrawn='';drawEpics(boardData);};
  input.onkeydown=e=>{if(e.key==='Escape'){e.stopPropagation();stop();}};input.onblur=()=>{if(!input.value.trim())stop();};
  f.onsubmit=async e=>{e.preventDefault();const title=input.value.trim();if(!title)return;input.value='';
    await onAdd(title);edrawn='';drawEpics(boardData);ev.querySelector('.addcard input')?.focus();};
  f.append(input);box.append(f);return box;}
function itemRow(c){
  const r=el('div','eitem');r.tabIndex=0;r.title='Click to edit';
  const dot=el('span','dot');dot.style.background=COLC[c.column];dot.title=boardData.columns.find(x=>x[0]===c.column)[1];
  r.append(dot,el('span','ct',c.title),el('span','num','#'+c.id));
  if(c.assignee)r.append(avatar(c.assignee));
  r.onclick=()=>openCard(c);r.onkeydown=e=>{if(e.key==='Enter')openCard(c);};return r;}
function epicPanel(title,items,epic){  // one group; epic is null for items without one
  const id=epic?String(epic.id):'none',shut=!!(closed[cur]||[]).includes(id),p=el('section','epic'+(shut?' shut':''));
  if(epic)p.style.setProperty('--ec',epicHue(epic));
  const h=el('div','ehead'),tog=btn(shut?'▸':'▾',()=>{const l=closed[cur]=closed[cur]||[];
    closed[cur]=shut?l.filter(x=>x!==id):[...l,id];saved.set('epicsClosed',closed);edrawn='';drawEpics(boardData);});
  tog.className='etog';tog.setAttribute('aria-expanded',String(!shut));tog.setAttribute('aria-label',(shut?'Show ':'Hide ')+title);
  const name=el(epic?'button':'span','ename',title);if(epic){name.type='button';name.title='Edit the epic';name.onclick=()=>openCard(epic);}
  h.append(tog,name);
  if(epic){h.append(el('span','num','#'+epic.id));
    const st=el('span','estate',boardData.columns.find(x=>x[0]===epic.column)[1]);st.style.color=COLC[epic.column];h.append(st);
    if(epic.assignee)h.append(avatar(epic.assignee));}
  const done=items.filter(c=>c.column==='done').length,prog=el('div','eprog');
  const bar=el('span','ebar'),fill=el('span');fill.style.width=(items.length?100*done/items.length:0)+'%';bar.append(fill);
  prog.append(bar,el('span','',`${done}/${items.length} done`));h.append(prog);p.append(h);
  if(!shut){const body=el('div','ebody');
    for(const c of items)body.append(itemRow(c));
    if(!items.length)body.append(el('div','eempty',epic?'No items yet.':'Every item is in an epic.'));
    if(epic)body.append(inlineAdd('+ Add item','Item title, then Enter','item'+epic.id,title=>boardCall('cards',{title,epic:epic.id})));
    p.append(body);}
  return p;}
function drawEpics(b){
  const key=cur+JSON.stringify(b)+adding+JSON.stringify(closed[cur]||[])+agents.map(a=>a.name);
  if(key===edrawn||ev.contains(document.activeElement)&&adding)return;edrawn=key;
  const epics=epicsOf(b),items=itemsOf(b);
  ev.replaceChildren(inlineAdd('+ New epic','Epic title, then Enter','epic',title=>boardCall('cards',{title,kind:'epic'})),
    ...(epics.length?[]:[el('p','eempty','No epics yet. An epic groups the work items of one bigger piece of work.')]),
    ...epics.map(e=>epicPanel(e.title,items.filter(c=>c.epic===e.id),e)),
    ...(epics.length?[epicPanel('No epic',items.filter(c=>!c.epic),null)]:[]));}
function openCard(c){
  editing=c;$('cardtitle').textContent=`${c.kind==='epic'?'Epic ':''}#${c.id}, added by ${c.created_by==='user'?'you':c.created_by}`;
  $('cepicrow').hidden=c.kind==='epic';
  $('cepic').replaceChildren(new Option('none',''),...epicsOf(boardData).map(e=>new Option(e.title,String(e.id))));
  $('cepic').value=c.epic?String(c.epic):'';
  $('ctitle').value=c.title;$('cdesc').value=c.description||'';
  $('ccolumn').replaceChildren(...boardData.columns.map(([k,l])=>new Option(l,k)));$('ccolumn').value=c.column;
  const sel=$('cassignee');
  sel.replaceChildren(new Option('nobody',''),new Option('you','user'),
    ...agents.filter(a=>a.status!=='removed'||a.name===c.assignee).map(a=>new Option(a.name,a.name)));
  sel.value=c.assignee||'';cp.showModal();$('ctitle').focus();}
function openCardById(n){const c=boardData&&boardData.cards.find(c=>c.id===n);
  if(c)openCard(c);else say('Card #'+n+' is no longer on the board.');}
$('csave').onclick=async()=>{const c=editing;cp.close();
  await boardCall('cards/'+c.id,{title:$('ctitle').value,description:$('cdesc').value,
    column:$('ccolumn').value,assignee:$('cassignee').value||null,
    ...(c.kind==='epic'?{}:{epic:$('cepic').value?Number($('cepic').value):null})});};
$('cdelete').onclick=async()=>{const c=editing;
  if(!confirm(c.kind==='epic'?`Delete epic #${c.id} "${c.title}"? Its items stay, without an epic.`:`Delete #${c.id} "${c.title}"?`))return;cp.close();
  await boardCall(`cards/${c.id}/delete`,{});};
cp.addEventListener('close',()=>{editing=null;});
$('viewchat').onclick=()=>showView('chat');$('viewboard').onclick=()=>showView('board');$('viewepics').onclick=()=>showView('epics');
if(location.hash==='#board'||location.hash==='#epics')showView(location.hash.slice(1));
