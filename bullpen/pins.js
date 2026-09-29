// Pinned messages (bullpen/pins.js): a bar above the chat with what matters for a while; agents get
// them with the rules. Uses the page's $, el, btn, api, cur, current, say, refresh and renderText.
let pinsBy={};
async function togglePin(n){try{await api(`api/projects/${cur}/messages/${n}/pin`,{});say();}catch(e){say('',e.message);}refresh();}
function isPinned(n){return (pinsBy[cur]||[]).includes(n);}
function jumpTo(n){const o=$('m'+n);if(!o){say('That message is not shown; clear the search.');return;}
  o.scrollIntoView({block:'center'});o.classList.remove('flash');void o.offsetWidth;o.classList.add('flash');}
function drawPins(){
  const bar=$('pinbar'),list=(pinsBy[cur]||[]).map(n=>(msgs[cur]||[]).find(m=>m.n===n)).filter(Boolean);
  bar.hidden=!list.length;
  bar.replaceChildren(...list.map(m=>{const row=el('div','pin'),txt=el('button','ptext');txt.type='button';
    txt.append(el('b','','📌 '+(m.from==='user'?'you':current(m.from))+' '),el('span','',m.text.replace(/\s+/g,' ').slice(0,160)));
    txt.title='Show the message';txt.onclick=()=>jumpTo(m.n);
    const x=btn('×',()=>togglePin(m.n),'icon');x.title='Unpin';x.setAttribute('aria-label','Unpin');
    row.append(txt,x);return row;}));}
