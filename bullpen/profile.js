// Profile (bullpen/profile.js): your picture with a cropper and previews, your name and a few lines about you
// (agents get them); the same picture part for an agent. The crop is a position on the original picture,
// no canvas, so a browser that blanks canvas read-back (a privacy setting) cannot spoil it.
// Uses the page's $, el, btn, api, cur, say, refresh, avatarsBy, picUrl, cropStyle and pickPicture.
let profileData={name:'',about:''};
const myName=()=>profileData.name||'you';
function showName(p){profileData=p;if('avatar' in p){myAv=p.avatar;redecorate(null,'user');drawMe();}$('profilebtn').querySelector('.me b').textContent=p.name||'Profile';
  $('profilebtn').querySelector('.me small').textContent=p.away?'Away · agents paused':'Online';
  for(const n of document.querySelectorAll('.g.user .gh .n'))n.textContent=myName();}  // messages already shown
api('api/profile').then(showName).catch(()=>{});
let meKey='';
function drawMe(){  // the rail's chip: your picture in the open project, and your name
  const k=myAv?myAv.v+JSON.stringify(myAv.crop):'';if(k===meKey)return;meKey=k;
  $('profilebtn').firstChild.replaceWith(avatar('user'));}
const pd=el('dialog','');pd.id='profile';document.body.append(pd);
pd.onclick=e=>{const r=pd.getBoundingClientRect();  // a click on the backdrop, outside the box, closes it
  if(e.target===pd&&(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom))pd.close();};
async function openProfile(pid,name,edit){
  pid=pid||cur;const me=name==='user',a=avOf(pid,name);
  const top=el('div','top'),x=btn('×',()=>pd.close(),'icon');x.setAttribute('aria-label','Close');
  top.append(el('h2','',me?'Profile':name+'’s picture'),x);
  const body=[top];
  const pics=el('div','pics'),big=el('div','av big'),small=el('div','av');pics.append(big,small);
  for(const d of [big,small]){d.textContent=me?'Y':name[0];d.style.setProperty('--c','var(--mute)');}
  const upload=btn(a?'Upload new picture':'Upload a picture',()=>{pd.close();pickPicture(pid,name);});
  const row=el('div','row');row.append(upload);
  if(a&&!edit)row.append(btn('Edit picture',()=>openProfile(pid,name,true),'ghost'));
  if(a)row.append(btn('Remove picture',async()=>{tookAv(pid,name,await api(avPath(pid,name)+'/delete',{}));pd.close();refresh();},'ghost danger'));
  const url=a&&await picUrl(pid,name);
  if(url&&!edit)for(const d of [big,small]){d.textContent='';d.style.background=`${cropStyle(a.crop)} no-repeat url(${url})`;}
  if(url&&edit){  // the cropper: the whole picture, a square frame to drag, a zoom slider
    const img=new Image();img.src=url;await img.decode().catch(()=>{});
    const k=Math.min(320/img.naturalWidth,320/img.naturalHeight),W=img.naturalWidth*k,H=img.naturalHeight*k,m=Math.min(W,H);
    const stage=el('div','stage'),frame=el('div','frame'),zoom=el('input');stage.style.width=W+'px';stage.style.height=H+'px';
    img.draggable=false;stage.append(img,frame);zoom.type='range';zoom.min=15;zoom.max=100;zoom.setAttribute('aria-label','Zoom');
    const c=a.crop||{w:m/W,h:m/H,x:(1-m/W)/2,y:(1-m/H)/2};let f={x:c.x*W,y:c.y*H,s:c.w*W};zoom.value=Math.round(f.s/m*100);
    const crop=()=>({x:f.x/W,y:f.y/H,w:f.s/W,h:f.s/H});
    const draw=()=>{f.s=Math.min(f.s,m);f.x=Math.max(0,Math.min(W-f.s,f.x));f.y=Math.max(0,Math.min(H-f.s,f.y));
      Object.assign(frame.style,{left:f.x+'px',top:f.y+'px',width:f.s+'px',height:f.s+'px'});
      for(const d of [big,small]){d.textContent='';d.style.background=`${cropStyle(crop())} no-repeat url(${url})`;}};
    frame.onpointerdown=e=>{try{frame.setPointerCapture(e.pointerId);}catch{}const sx=e.clientX-f.x,sy=e.clientY-f.y;
      frame.onpointermove=ev=>{f.x=ev.clientX-sx;f.y=ev.clientY-sy;draw();};
      frame.onpointerup=()=>{frame.onpointermove=null;};};
    zoom.oninput=()=>{const cx=f.x+f.s/2,cy=f.y+f.s/2;f.s=m*zoom.value/100;f.x=cx-f.s/2;f.y=cy-f.s/2;draw();};
    draw();
    const save=btn('Save picture',async()=>{try{tookAv(pid,name,await api(avPath(pid,name)+'/crop',crop()));say('Picture saved.');openProfile(pid,name);refresh();}catch(e){say('',e.message);}});
    const zrow=el('label','zoom');zrow.append('Zoom ',zoom);
    const head=el('div','head');head.append(pics,row);
    body.push(el('p','hint','Drag the square to the part to show; the previews show it as in the chat.'),stage,zrow,head,save);
  }else{const head=el('div','head');head.append(pics,row);body.push(head);}
  if(me){  // who you are, for the chat and for agents
    const st=el('div','views'),sh=el('small','',''),set=async away=>{try{showName(await api('api/profile',{away}));mark();}catch(e){say('',e.message);}};
    const on=btn('Online',()=>set(false)),off=btn('Away',()=>set(true));st.append(on,off);
    const mark=()=>{on.classList.toggle('on',!profileData.away);off.classList.toggle('on',profileData.away);
      sh.textContent=profileData.away?'Agents sleep until you write or come back.':'Agents wake for each other.';};mark();
    const l0=el('div','status');l0.append(el('span','','Status'),st,sh);body.push(l0);
    const nm=el('input'),ab=el('textarea');nm.maxLength=32;nm.value=profileData.name;nm.placeholder='you';
    ab.maxLength=500;ab.rows=3;ab.value=profileData.about;ab.placeholder='e.g. Prefers short answers; works in CET; ask before pushing';
    const l1=el('label','','Your name (shown instead of “you”)'),l2=el('label','','About you (agents get this with their instructions)');
    l1.append(nm);l2.append(ab);
    body.push(l1,l2,btn('Save profile',async()=>{try{showName(await api('api/profile',{name:nm.value,about:ab.value}));say('Profile saved.');}catch(e){say('',e.message);}}));}
  pd.replaceChildren(...body);if(!pd.open)pd.showModal();}
$('profilebtn').onclick=()=>openProfile(cur,'user');drawMe();
