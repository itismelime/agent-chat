// Emoji (bullpen/emoji.js): the 😀 picker in the formatting toolbar, `:name` completion in the message
// box, and More… for reactions. Uses EMOJI (emoji-data.js) and the page's $, el, t, saved, insertText
// (format.js) and react (reactions.js).
const EMOJI_ALL=EMOJI.flatMap(([,items])=>items),ep=el('div','');ep.id='emojipick';ep.hidden=true;
document.body.append(ep);
let recent=saved.get('emojiRecent',[]),pickDone=null;
function emojiFind(q,n){q=q.toLowerCase().trim();
  if(!q)return EMOJI_ALL.slice(0,n);
  const words=q.split(/\s+/),hit=EMOJI_ALL.filter(([,w])=>words.every(x=>w.split(' ').some(y=>y.startsWith(x))));
  return hit.slice(0,n);}
const label=w=>[...new Set(w.split(' '))].slice(-4).join(' ');  // a few words, each once
function used(e){recent=[e,...recent.filter(x=>x!==e)].slice(0,24);saved.set('emojiRecent',recent);}
function openPicker(anchor,done){  // done(emoji): insert it, or react with it
  pickDone=done;const search=el('input');search.placeholder='Search emoji';search.setAttribute('aria-label','Search emoji');
  const grid=el('div','egrid');
  const draw=()=>{const q=search.value;grid.replaceChildren();
    const groups=q?[['Found',emojiFind(q,120)]]:[['Recent',recent.map(e=>EMOJI_ALL.find(x=>x[0]===e)||[e,''])],...EMOJI];
    for(const [g,items] of groups){if(!items.length)continue;grid.append(el('h4','',g));
      for(const [e,w] of items){const b=el('button','',e);b.type='button';b.title=label(w);
        b.onclick=()=>{closePicker();used(e);done(e);};grid.append(b);}}
    if(!grid.children.length)grid.append(el('p','','No emoji match “'+q+'”.'));};
  search.oninput=draw;search.onkeydown=e=>{if(e.key==='Escape'){e.stopPropagation();closePicker();}
    if(e.key==='Enter'){e.preventDefault();grid.querySelector('button')?.click();}};
  ep.replaceChildren(search,grid);draw();ep.hidden=false;
  const r=anchor.getBoundingClientRect(),h=ep.offsetHeight,w=ep.offsetWidth;
  ep.style.left=Math.max(8,Math.min(r.left,innerWidth-w-8))+'px';
  ep.style.top=(r.top-h-6>8?r.top-h-6:Math.min(r.bottom+6,innerHeight-h-8))+'px';search.focus();}
function closePicker(){ep.hidden=true;pickDone=null;}
addEventListener('mousedown',e=>{if(!ep.hidden&&!ep.contains(e.target)&&!e.target.closest('.emojibtn'))closePicker();});
// the toolbar button
{const b=el('button','emojibtn','😀');b.type='button';b.title='Emoji (or type :name)';b.setAttribute('aria-label','Emoji');
  b.onmousedown=e=>e.preventDefault();
  b.onclick=()=>ep.hidden?openPicker(b,e=>{t.focus();insertText(e);}):closePicker();$('fmt').append(b);}
// :name completion: the word before the caret after a colon, at least two letters
const eac=el('div','');eac.id='emojiac';eac.hidden=true;document.body.append(eac);let eacSel=0,eacList=[];
function eacWord(){const v=t.value.slice(0,t.selectionStart),m=/(?:^|\s):([a-z0-9_+-]{2,})$/i.exec(v);return m&&m[1];}
function eacDraw(){const w=eacWord();eacList=w?emojiFind(w.replace(/_/g,' '),6):[];
  if(!eacList.length){eac.hidden=true;return;}eacSel=Math.min(eacSel,eacList.length-1);
  eac.replaceChildren(...eacList.map(([e,words],i)=>{const d=el('div',i===eacSel?'sel':'');
    d.append(el('span','',e),el('small','',label(words)));
    d.onmousedown=ev=>{ev.preventDefault();eacPick(i);};return d;}));
  const r=t.getBoundingClientRect();eac.hidden=false;eac.style.left=r.left+'px';eac.style.top=(r.top-eac.offsetHeight-6)+'px';}
function eacPick(i){const w=eacWord();if(!w)return;const e=eacList[i][0],at=t.selectionStart;
  t.setSelectionRange(at-w.length-1,at);insertText(e);used(e);eac.hidden=true;}
t.addEventListener('input',()=>{eacSel=0;eacDraw();});
t.addEventListener('blur',()=>{eac.hidden=true;});
t.addEventListener('keydown',e=>{  // before the page's own keys: Enter picks while the list is open
  if(eac.hidden)return;
  if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();e.stopImmediatePropagation();
    eacSel=(eacSel+(e.key==='ArrowDown'?1:eacList.length-1))%eacList.length;eacDraw();}
  else if(e.key==='Enter'||e.key==='Tab'){e.preventDefault();e.stopImmediatePropagation();eacPick(eacSel);}
  else if(e.key==='Escape'){e.stopImmediatePropagation();eac.hidden=true;}},true);
