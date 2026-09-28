// Markdown in messages and files, and the file viewer. marked (vendored) turns text into HTML with raw
// HTML escaped; clean() then keeps only the tags and attributes below, so an agent's message cannot run
// script in this page. @mentions and file paths become links; files open in the viewer, only from inside
// the project folder (the server checks).
const escHtml=s=>s.replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'})[c]);
marked.use({gfm:true,renderer:{html:({text})=>escHtml(text)}});
const TAGS={P:[],BR:[],HR:[],H1:[],H2:[],H3:[],H4:[],H5:[],H6:[],STRONG:[],EM:[],DEL:[],B:[],I:[],CODE:[],PRE:[],
  BLOCKQUOTE:[],UL:[],OL:['start'],LI:[],A:['href','title'],IMG:['src','alt','title'],TABLE:[],THEAD:[],TBODY:[],
  TR:[],TH:['align'],TD:['align'],INPUT:['type','checked']};
function clean(node){
  for(const e of [...node.children]){const ok=TAGS[e.tagName];
    if(!ok||(e.tagName==='INPUT'&&e.getAttribute('type')!=='checkbox')){e.replaceWith(e.textContent);continue;}
    for(const a of [...e.attributes])if(!ok.includes(a.name))e.removeAttribute(a.name);
    clean(e);}}
// a file path: has a folder or a known extension, optional :line[:col]
const TEXT_EXT='md|markdown|txt|py|js|mjs|cjs|ts|tsx|jsx|json|jsonl|yaml|yml|toml|ini|cfg|conf|css|scss|html|htm|sh|bash|zsh|'+
  'fish|rs|go|c|h|cc|cpp|hpp|java|kt|rb|php|sql|xml|csv|tsv|log|service|lock|swift|lua|pl|r|ipynb|env|spec|patch|diff|svg';
const PATH_RE=/(?:~|\.{1,2})?\/?(?:[\w.@+-]+\/)*[\w@+-][\w.@+-]*\.[A-Za-z]\w{0,9}(?::\d+){0,2}/;
const WORD_RE=new RegExp('(@[\\w-]+|'+PATH_RE.source+')');
function asPath(s){const m=/^(.*?)((?::\d+){0,2})$/.exec(s.trim()),p=m[1];
  if(!new RegExp('^'+PATH_RE.source+'$').test(s.trim())||/^[a-z]+:\/\//i.test(p))return null;
  if(!p.includes('/')&&!new RegExp('\\.('+TEXT_EXT+')$','i').test(p))return null;
  return {path:p,line:+(m[2].split(':')[1]||0)};}
function joinPath(base,rel){if(/^[~/]/.test(rel))return rel;const out=[];
  for(const s of (base?base+'/':'').concat(rel).split('/')){if(s==='..')out.pop();else if(s&&s!=='.')out.push(s);}
  return out.join('/');}
function fileLink(nodes,pid,f,base=''){const a=el('a','file');a.href='#';a.title='Open '+f.path;
  a.onclick=e=>{e.preventDefault();openFile(pid,f.path,f.line,false,base);};a.append(...nodes);return a;}
function decode(s){try{return decodeURI(s);}catch(e){return s;}}
function renderText(text,{pid=cur,base='',breaks=true}={}){
  const tpl=document.createElement('template');  // inert: nothing loads or runs until clean() is done
  tpl.innerHTML=marked.parse(text,{breaks,async:false});clean(tpl.content);
  for(const i of tpl.content.querySelectorAll('img')){const src=i.getAttribute('src')||'';
    if(!/^https:\/\//i.test(src)){i.removeAttribute('src');if(src&&!/^[a-z][\w+.-]*:/i.test(src))i.dataset.path=joinPath(base,src);}}
  const frag=document.importNode(tpl.content,true);
  for(const a of [...frag.querySelectorAll('a')]){const href=a.getAttribute('href')||'';
    if(/^(https?|mailto):/i.test(href)){a.target='_blank';a.rel='noopener noreferrer';continue;}
    a.removeAttribute('href');  // other schemes and #anchors stay plain text; the rest are files
    if(href&&!/^[a-z][\w+.-]*:|^#/i.test(href)){const m=/^(.*?)(?::(\d+))?(?::\d+)?$/.exec(decode(href.split('#')[0]));
      a.replaceWith(fileLink([...a.childNodes],pid,{path:joinPath(base,m[1]),line:+(m[2]||0)}));}}
  for(const i of frag.querySelectorAll('img[data-path]'))rawImage(i,pid);
  for(const c of frag.querySelectorAll('code')){const f=c.parentNode.nodeName!=='PRE'&&!c.closest('a')&&asPath(c.textContent);
    if(f)c.replaceWith(fileLink([c.cloneNode(true)],pid,f,base));}
  words(frag,pid,base);
  for(const pre of frag.querySelectorAll('pre')){const box=el('div','codeblock'),code=pre.textContent.replace(/\n$/,'');
    const copy=btn('Copy',()=>navigator.clipboard.writeText(code).then(()=>{copy.textContent='Copied';
      setTimeout(()=>{copy.textContent='Copy';},1200);}),'ghost copy');
    pre.replaceWith(box);box.append(pre,copy);}
  for(const tb of frag.querySelectorAll('table')){const w=el('div','tablewrap');tb.replaceWith(w);w.append(tb);}
  for(const i of frag.querySelectorAll('input'))i.disabled=true;
  return frag;}
// @mentions and paths in plain text (not inside code or links)
function words(frag,pid,base){
  const walk=document.createTreeWalker(frag,NodeFilter.SHOW_TEXT),nodes=[];
  while(walk.nextNode()){const p=walk.currentNode.parentElement;if(!p||!p.closest('a,code,pre'))nodes.push(walk.currentNode);}
  for(const n of nodes){const parts=n.data.split(WORD_RE);if(parts.length<2)continue;
    const out=document.createDocumentFragment();
    parts.forEach((part,i)=>{if(!(i%2)){if(part)out.append(part);return;}
      if(part[0]==='@'){const name=part.slice(1).toLowerCase();
        out.append(name==='user'||name==='you'?el('span','mention me',part):
          agents.some(a=>a.name===name)?who(el('span','mention',part),name):part);return;}
      const f=asPath(part);out.append(f?fileLink([part],pid,f,base):part);});
    n.replaceWith(out);}}
const rawCache=new Map();
async function rawImage(img,pid){const key=pid+':'+img.dataset.path;
  try{if(!rawCache.has(key)){const r=await fetch(`api/projects/${pid}/file?raw=1&path=${encodeURIComponent(img.dataset.path)}`,
      {headers:{'X-Agent-Chat':'1'}});if(!r.ok)throw new Error(r.statusText);rawCache.set(key,URL.createObjectURL(await r.blob()));}
    img.src=rawCache.get(key);}catch(e){img.replaceWith(el('span','ferr','[image '+img.dataset.path+' not found]'));}}

// the file viewer: Markdown rendered, other text with line numbers; links inside open the next file, Back returns
let fileStack=[];
// a bare path in a file may be relative to the project, the file's folder or a folder above it (a repo root)
async function openFile(pid,path,line,back,base=''){
  const d=$('fileview'),body=$('filebody');if(!back)fileStack.push({pid,path,line});
  $('fileback').hidden=fileStack.length<2;$('filetitle').textContent=path;body.replaceChildren(el('p','fmute','Loading…'));
  if(!d.open)d.showModal();
  const dirs=base.split('/').filter(Boolean),tries=[path,...dirs.map((_,i)=>dirs.slice(0,dirs.length-i).join('/')+'/'+path)];
  try{let f;for(const p of tries){try{f=await api(`api/projects/${pid}/file?path=${encodeURIComponent(p)}`);break;}
      catch(e){if(p===tries[tries.length-1]||!/no such file/.test(e.message))throw e;}}
    $('filetitle').textContent=f.path;fileStack[fileStack.length-1].path=f.path;
    if(/\.(md|markdown|mdx)$/i.test(f.path)){const md=el('div','rich md');
      md.append(renderText(f.text,{pid,base:f.path.split('/').slice(0,-1).join('/'),breaks:false}));body.replaceChildren(md);}
    else{const pre=el('pre','src');pre.append(...f.text.replace(/\n$/,'').split('\n').map(l=>el('span','',l+'\n')));
      body.replaceChildren(pre);const s=line&&pre.children[line-1];
      if(s){s.classList.add('hl');s.scrollIntoView({block:'center'});}else body.scrollTop=0;}}
  catch(e){body.replaceChildren(el('p','ferr',e.message));}}
$('fileback').onclick=()=>{fileStack.pop();const f=fileStack[fileStack.length-1];openFile(f.pid,f.path,f.line,true);};
$('filecopy').onclick=()=>navigator.clipboard.writeText($('filetitle').textContent);
$('fileview').addEventListener('close',()=>{fileStack=[];});
