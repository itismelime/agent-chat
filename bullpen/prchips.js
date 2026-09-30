// PR chips (bullpen/prchips.js): after a GitHub pull request link, or owner/repo#12, a chip with its state
// and checks, from the service (gh, cached a minute). Uses the page's api.
const PR_URL=/^https:\/\/github\.com\/([\w.-]+\/[\w.-]+)\/pull\/(\d+)\/?(?:[#?].*)?$/,PR_REF=/([\w.-]+\/[\w.-]+)#(\d+)/g;
const prCache={};  // "repo#n" -> {at, promise, value}: merged and closed PRs are kept for good
const PR_LABEL={open:'open',draft:'draft',merged:'merged',closed:'closed'},CHECK_MARK={passed:'✓',failed:'✗',running:'●'};
function prState(repo,n){const k=repo+'#'+n,c=prCache[k];
  if(c&&(c.value&&['merged','closed'].includes(c.value.state)||Date.now()-c.at<120e3))return c.promise;
  const promise=api(`api/pr?repo=${encodeURIComponent(repo)}&n=${n}`);const e={at:Date.now(),promise,value:c&&c.value};
  prCache[k]=e;promise.then(v=>{e.value=v;},()=>{});return promise;}
function fillChip(c,repo,n,s){c.textContent=(PR_LABEL[s.state]||s.state)+(s.checks?' '+CHECK_MARK[s.checks]:'');
  c.className='prchip '+s.state+(s.checks?' '+s.checks:'');c.hidden=false;c.style.visibility='';
  c.title=`${repo}#${n}: ${s.title}`+(s.detail.length?'\n'+s.detail.map(([name,st])=>`${st||'…'}: ${name}`).join('\n'):'');}
// only chips that come into view ask the service, so a long history is not fetched all at once
const chipSeen=new IntersectionObserver(es=>{for(const e of es)if(e.isIntersecting){chipSeen.unobserve(e.target);
  const c=e.target;prState(c.dataset.repo,c.dataset.n).then(s=>fillChip(c,c.dataset.repo,c.dataset.n,s)).catch(()=>c.remove());}});
function prChip(after,repo,n){
  const c=document.createElement('span');c.className='prchip';c.dataset.repo=repo;c.dataset.n=n;after.after(c);
  const known=(prCache[repo+'#'+n]||{}).value;
  if(known)fillChip(c,repo,n,known);else{c.textContent='…';c.style.visibility='hidden';}  // a placeholder the observer can see
  if(!known||!['merged','closed'].includes(known.state))chipSeen.observe(c);}
function addPrChips(node){
  for(const a of node.querySelectorAll('a[href]')){const m=PR_URL.exec(a.href);if(m)prChip(a,m[1],m[2]);}
  const walk=document.createTreeWalker(node,NodeFilter.SHOW_TEXT),texts=[];
  while(walk.nextNode()){const t=walk.currentNode,pe=t.parentElement;  // no parent element: straight in a fragment
    if(!(pe&&pe.closest('a,code,pre'))&&PR_REF.test(t.data))texts.push(t);PR_REF.lastIndex=0;}
  for(const t of texts){const frag=document.createDocumentFragment();let at=0;
    for(const m of t.data.matchAll(PR_REF)){frag.append(t.data.slice(at,m.index));
      const a=document.createElement('a');a.href=`https://github.com/${m[1]}/pull/${m[2]}`;a.target='_blank';a.rel='noopener';
      a.textContent=m[0];frag.append(a);const mark=document.createTextNode('');frag.append(mark);prChip(mark,m[1],m[2]);at=m.index+m[0].length;}
    frag.append(t.data.slice(at));t.replaceWith(frag);}}
