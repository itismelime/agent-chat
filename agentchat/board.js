// Kanban board view (agentchat/board.js). Uses the page's $, el, btn, api, avatar, who, cur, agents,
// boardData (loaded with every refresh), say and t.
const bv=$('boardview'),cp=$('cardpanel'),COLC={todo:'var(--mute)',doing:'var(--busy)',review:'var(--attn)',done:'var(--ok)'};
let bdrawn='',editing=null,adding=null;
function showView(board){
  $('viewchat').classList.toggle('on',!board);$('viewboard').classList.toggle('on',board);
  $('chat').hidden=board;bv.hidden=!board;bdrawn='';history.replaceState(null,'',board?'#board':location.pathname);
  if(board&&boardData)drawBoard(boardData);if(!board)t.focus();}
async function boardCall(path,body){
  try{await api(`api/projects/${cur}/board/${path}`,Object.assign({by:'user'},body));say();}
  catch(e){say('',e.message);}
  refresh();}
function drawBoard(b){
  const key=cur+JSON.stringify(b)+adding+agents.map(a=>a.name);if(key===bdrawn||bv.contains(document.activeElement)&&adding)return;bdrawn=key;
  bv.replaceChildren(...b.columns.map(([k,label])=>{
    const col=el('div','col'),h=el('h3'),cards=b.cards.filter(c=>c.column===k);
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
    await boardCall('cards',{title,column:k});bdrawn='';drawBoard(boardData);bv.querySelector('.addcard input')?.focus();};
  f.append(input);box.append(f);return box;}
function openCard(c){
  editing=c;$('cardtitle').textContent=`#${c.id}, added by ${c.created_by==='user'?'you':c.created_by}`;
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
    column:$('ccolumn').value,assignee:$('cassignee').value||null});};
$('cdelete').onclick=async()=>{const c=editing;if(!confirm(`Delete #${c.id} "${c.title}"?`))return;cp.close();
  await boardCall(`cards/${c.id}/delete`,{});};
cp.addEventListener('close',()=>{editing=null;});
$('viewchat').onclick=()=>showView(false);$('viewboard').onclick=()=>showView(true);
if(location.hash==='#board')showView(true);
