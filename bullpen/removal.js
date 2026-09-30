// Removing a project (bullpen/removal.js): right-click it in the sidebar. From bullpen only, or with
// its folder moved to the Trash behind a warning and the typed project name. Uses the page's api,
// ask, openMenu, say, refresh, cur and select.
const gb=n=>n>=2**30?(n/2**30).toFixed(1)+' GB':n>=2**20?(n/2**20).toFixed(1)+' MB':Math.max(1,Math.round(n/1024))+' KB';
function projectMenu(e,p){openMenu(e,[
  ['Remove from bullpen',async()=>{
    if(!confirm(`Remove ${p.name} from bullpen?\n\nIts folder and chat history stay; add the folder again to bring it back. Agents started here from the page are stopped.`))return;
    await api(`api/projects/${p.id}/remove`,{});if(cur===p.id)cur=null;refresh();return `Removed ${p.name} from bullpen.`;}],
  ['Delete folder too…',async()=>{
    const i=await api(`api/projects/${p.id}/removal`);
    if(i.refused)throw new Error(`${p.name} can't go to the Trash: ${i.refused}.`);
    const what=[`⚠️ DANGER: this moves the whole folder to the Trash:`,i.path,
      i.files?`${i.files}${i.more?'+':''} files, ${gb(i.size)}`:'an empty folder'+(i.git_changes?`, ${i.git_changes} uncommitted git change${i.git_changes>1?'s':''}`:''),
      'You can restore it from the Trash. Agents started here from the page are stopped.',`Type ${p.name} to confirm.`].join('\n');
    $('ask').classList.add('danger');
    const typed=await ask({title:'Move '+p.name+' to the Trash',label:'Project name',help:what,placeholder:p.name});
    $('ask').classList.remove('danger');
    if(typed===null)return;
    if(typed.trim()!==p.name)throw new Error('The name did not match; nothing was moved.');
    await api(`api/projects/${p.id}/remove`,{trash:true,confirm:typed.trim()});if(cur===p.id)cur=null;refresh();
    return `${p.name} is in the Trash, and out of bullpen.`;}]]);}
