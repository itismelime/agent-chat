// Kanban board view (agentchat/board.js). Uses the page's $, el, api, cur, agents, t.
const bv=$('boardview'),cp=$('cardpanel');let btimer=null,bdrawn='',editing=null;
function showView(board){
  $('viewchat').classList.toggle('on',!board);$('viewboard').classList.toggle('on',board);
  $('chat').hidden=board;bv.hidden=!board;clearInterval(btimer);
  if(board){bdrawn='';loadBoard();btimer=setInterval(loadBoard,2000);}else t.focus();}
async function loadBoard(){
  if(!cur)return;
  try{drawBoard(await api(`api/projects/${cur}/board`));}catch(e){$('err').textContent=e.message;}}
async function boardCall(path,body){
  try{await api(`api/projects/${cur}/board/${path}`,Object.assign({by:'user'},body));$('err').textContent='';}
  catch(e){$('err').textContent=e.message;}
  loadBoard();}
function drawBoard(b){
  const key=cur+JSON.stringify(b);if(key===bdrawn)return;bdrawn=key;
  bv.replaceChildren(...b.columns.map(([k,label])=>{
    const col=el('div','col'),h=el('h3'),add=el('button','ghost','+'),cards=b.cards.filter(c=>c.column===k);
    add.type='button';add.title='Add a card to '+label;
    add.onclick=()=>{const title=prompt(`New card in ${label}`);if(title&&title.trim())boardCall('cards',{title,column:k});};
    h.append(el('span','',`${label} — ${cards.length}`),add);col.append(h);
    col.ondragover=e=>{e.preventDefault();col.classList.add('over');};
    col.ondragleave=()=>col.classList.remove('over');
    col.ondrop=e=>{e.preventDefault();col.classList.remove('over');
      const id=e.dataTransfer.getData('text/plain');if(/^\d+$/.test(id))boardCall('cards/'+id,{column:k});};
    for(const c of cards){
      const d=el('div','card');d.draggable=true;d.title='Click to edit, drag to move';
      d.append(el('b','','#'+c.id+' '),c.title,el('small','',c.assignee?'→ '+c.assignee:''));
      d.ondragstart=e=>e.dataTransfer.setData('text/plain',String(c.id));d.onclick=()=>openCard(c);col.append(d);}
    return col;}));}
function openCard(c){
  editing=c;$('cardtitle').textContent=`#${c.id} · by ${c.created_by}`;$('ctitle').value=c.title;$('cdesc').value=c.description||'';
  const sel=$('cassignee');
  sel.replaceChildren(new Option('nobody',''),new Option('you','user'),
    ...agents.filter(a=>a.status!=='removed').map(a=>new Option(a.name,a.name)));
  sel.value=c.assignee||'';cp.hidden=false;$('ctitle').focus();}
function closeCard(){cp.hidden=true;editing=null;}
$('csave').onclick=async()=>{const c=editing;closeCard();
  await boardCall('cards/'+c.id,{title:$('ctitle').value,description:$('cdesc').value,assignee:$('cassignee').value||null});};
$('cdelete').onclick=async()=>{const c=editing;if(!confirm(`Delete #${c.id} "${c.title}"?`))return;closeCard();
  await boardCall(`cards/${c.id}/delete`,{});};
$('cardclose').onclick=closeCard;
$('viewchat').onclick=()=>showView(false);$('viewboard').onclick=()=>showView(true);
addEventListener('keydown',e=>{if(e.key==='Escape'&&!cp.hidden)closeCard();});
