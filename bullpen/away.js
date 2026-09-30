// While you were away (bullpen/away.js): back after AWAY_MIN minutes or more, a card above the chat sums
// up each project since you left: messages, questions for you, board changes and PRs mentioned.
// Uses the page's $, el, btn, msgs, projects, select, saved, pending (answers.js) and away.
const AWAY_MIN=20;let leftAt=saved.get('activeAt',Date.now());
function awaySummary(since){  // [{p, messages, questions, board, prs}] for projects with news since then
  const out=[],q=typeof pending==='function'?pending():[];
  for(const p of projects){const news=(msgs[p.id]||[]).filter(m=>Date.parse(m.time)>since&&m.from!=='user');
    if(!news.length)continue;
    const prs=new Set();for(const m of news)for(const x of m.text.matchAll(/github\.com\/[\w.-]+\/[\w.-]+\/pull\/(\d+)|[\w.-]+\/[\w.-]+#(\d+)/g))prs.add('#'+(x[1]||x[2]));
    out.push({p,messages:news.filter(m=>m.kind!=='board').length,board:news.filter(m=>m.kind==='board').length,
      questions:q.filter(x=>x.p.id===p.id&&Date.parse(x.m.time)>since).length,prs:[...prs]});}
  return out;}
function showAway(since){
  const rows=awaySummary(since),card=$('awaycard');if(!rows.length)return;
  const mins=Math.round((Date.now()-since)/60e3),span=mins<120?mins+' min':Math.round(mins/60)+' h';
  const head=el('div','awayhead');head.append(el('b','','While you were away ('+span+')'),btn('×',()=>{card.hidden=true;},'icon'));
  card.replaceChildren(head,...rows.map(r=>{const d=el('div','awayrow'),name=btn(r.p.name,()=>select(r.p.id),'awayp');
    const bits=[r.messages&&`${r.messages} message${r.messages>1?'s':''}`,r.questions&&`${r.questions} question${r.questions>1?'s':''} for you`,
      r.board&&`${r.board} board change${r.board>1?'s':''}`,r.prs.length&&'PRs '+r.prs.slice(0,6).join(' ')].filter(Boolean);
    d.append(name,el('span','',bits.join(' · ')));if(r.questions)d.classList.add('ask');return d;}));
  card.hidden=false;}
function markActive(){if(away())return;
  if(Date.now()-leftAt>AWAY_MIN*60e3&&projects.length)showAway(leftAt);
  leftAt=Date.now();saved.set('activeAt',leftAt);}
setInterval(markActive,30e3);addEventListener('focus',()=>setTimeout(markActive,2500));
document.addEventListener('visibilitychange',()=>{if(!document.hidden)setTimeout(markActive,2500);});
setTimeout(markActive,4000);  // a fresh page: after the first load of every project's messages
