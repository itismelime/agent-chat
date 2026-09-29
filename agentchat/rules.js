// Rules (agentchat/rules.js): the project's standing instructions for every agent. Uses the page's
// $, el, btn, api, cur, say and ask. Agents get them on join and with every message that wakes them.
const rp=$('rulespanel');let rulesOf=null,rulesFor=null;
async function loadRules(){
  if(!cur){$('rulecount').textContent='';return;}
  const pid=cur;try{const r=(await api(`api/projects/${pid}/rules`)).rules;if(pid!==cur)return;
    rulesOf=r;rulesFor=pid;$('rulecount').textContent=r.length?String(r.length):'';if(rp.open)drawRules();}
  catch(e){say('',e.message);}}
async function ruleCall(path,body){try{await api(`api/projects/${cur}/rules${path}`,body);say();}
  catch(e){say('',e.message);}await loadRules();}
function drawRules(){
  $('rulesproj').textContent=(projects.find(p=>p.id===cur)||{}).name||'';
  $('rulelist').replaceChildren(...(rulesOf||[]).map(r=>{
    const li=el('li'),text=el('span','rtext',r.text);
    li.append(text,
      btn('Edit',async()=>{const t2=await ask({title:'Edit rule',label:'Rule',value:r.text,multiline:true,
        help:'Every agent in this project follows it.'});if(t2&&t2.trim()&&t2.trim()!==r.text)ruleCall('/'+r.id,{text:t2.trim()});},'ghost'),
      btn('Delete',()=>{if(confirm(`Delete the rule "${r.text}"?`))ruleCall(`/${r.id}/delete`,{});},'ghost danger'));
    return li;}));
  $('rulenone').hidden=!!(rulesOf&&rulesOf.length);}
$('rulesbtn').onclick=async()=>{if(!cur)return say('Add a project first.');drawRules();rp.showModal();$('ruletext').focus();await loadRules();};
$('ruleadd').onsubmit=async e=>{e.preventDefault();const text=$('ruletext').value.trim();if(!text)return;
  $('ruletext').value='';await ruleCall('',{text});$('ruletext').focus();};
$('ruletext').onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();$('ruleadd').requestSubmit();}};
setInterval(()=>{if(cur!==rulesFor)loadRules();},1000);  // the count follows the open project
