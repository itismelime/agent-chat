// Avatar pictures (bullpen/avatars.js): yours and each agent's, over the letter and color. Uses the page's
// $, el, api, cur, current, say and refresh. The API needs its header, so pictures come as blob: URLs.
let avatarsBy={};const picUrls={};
function decorateAvatar(div,name,pid){
  const v=(avatarsBy[pid]||{})[name];if(!v)return;
  const key=pid+'/'+name+'/'+v;div.dataset.pic=key;
  if(picUrls[key]){if(picUrls[key]!=='loading')div.style.backgroundImage=`url(${picUrls[key]})`,div.classList.add('pic');return;}
  picUrls[key]='loading';
  fetch(`api/projects/${pid}/avatars/${encodeURIComponent(name)}`,{headers:{'X-Bullpen':'1'}}).then(r=>r.ok?r.blob():null)
    .then(b=>{if(!b)return;picUrls[key]=URL.createObjectURL(b);
      for(const d of document.querySelectorAll('.av')) if(d.dataset.pic===key){d.style.backgroundImage=`url(${picUrls[key]})`;d.classList.add('pic');}});}
function pickPicture(pid,name){  // a square crop, 256 px, done here so the upload stays small
  const f=el('input');f.type='file';f.accept='image/png,image/jpeg,image/webp,image/gif';
  f.onchange=async()=>{const file=f.files[0];if(!file)return;
    try{const img=await createImageBitmap(file),s=Math.min(img.width,img.height),c=document.createElement('canvas');
      c.width=c.height=256;c.getContext('2d').drawImage(img,(img.width-s)/2,(img.height-s)/2,s,s,0,0,256,256);
      const data=c.toDataURL('image/png');
      await api(`api/projects/${pid}/avatars/${encodeURIComponent(name)}`,{data});say((name==='user'?'Your':name+'’s')+' picture is set.');refresh();}
    catch(e){say('',e.message);}};
  f.click();}
async function removePicture(pid,name){try{await api(`api/projects/${pid}/avatars/${encodeURIComponent(name)}/delete`,{});}
  catch(e){say('',e.message);}refresh();}
$('mepic').onclick=()=>{if(!cur)return say('Add a project first.');pickPicture(cur,'user');};
