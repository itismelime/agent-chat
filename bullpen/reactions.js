// Reactions (bullpen/reactions.js): acknowledge a message without a reply. Uses the page's el, btn,
// api, cur, current, openMenu, menu, say and refresh; reactsBy comes with every message poll.
const REACTIONS=['👍','✅','👀','❤️','🎉','🙏','😄','👎'];let reactsBy={};
async function react(m,e){
  try{await api(`api/projects/${cur}/messages/${m.n}/react`,{from:'user',emoji:e});say();}catch(err){say('',err.message);}
  refresh();}
function reactRow(m){  // chips under a message: emoji and count; yours are marked, a click toggles
  const row=el('div','reacts'),r=(reactsBy[cur]||{})[m.n]||{};
  for(const [e,who] of Object.entries(r)){
    const b=btn(e+' '+who.length,()=>react(m,e),'react'+(who.includes('user')?' mine':''));
    b.title=who.map(w=>w==='user'?'you':current(w)).join(', ');b.setAttribute('aria-pressed',String(who.includes('user')));
    row.append(b);}
  return row;}
function pickReaction(ev,m){openMenu(ev,REACTIONS.map(e=>[e,()=>react(m,e)]));menu.classList.add('emojis');}
