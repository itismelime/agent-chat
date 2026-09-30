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
function pickPicture(pid,name){  // the file as it is when it can be; the page crops it square on show
  const f=el('input');f.type='file';f.accept='image/png,image/jpeg,image/webp,image/gif';
  f.onchange=async()=>{const file=f.files[0];if(!file)return;
    try{const data=['image/png','image/jpeg','image/webp'].includes(file.type)&&file.size<=1e6?await asDataUrl(file):await shrink(file);
      await api(`api/projects/${pid}/avatars/${encodeURIComponent(name)}`,{data});say((name==='user'?'Your':name+'’s')+' picture is set.');refresh();}
    catch(e){say('',e.message);}};
  f.click();}
const asDataUrl=file=>new Promise((ok,bad)=>{const r=new FileReader();r.onload=()=>ok(r.result);r.onerror=()=>bad(r.error);r.readAsDataURL(file);});
async function shrink(file){  // too big, or a GIF: a 256 px square, drawn here
  const img=await createImageBitmap(file),s=Math.min(img.width,img.height),c=document.createElement('canvas');
  c.width=c.height=256;const g=c.getContext('2d');g.drawImage(img,(img.width-s)/2,(img.height-s)/2,s,s,0,0,256,256);
  const px=g.getImageData(0,0,256,256).data;  // privacy settings (Firefox's resistFingerprinting) blank what a page reads back
  if(px.every((v,i)=>v===px[i%4]))throw new Error('Your browser would not let the page resize the picture (a privacy setting). Choose a PNG, JPEG or WebP of at most 1 MB instead.');
  return c.toDataURL('image/png');}
async function removePicture(pid,name){try{await api(`api/projects/${pid}/avatars/${encodeURIComponent(name)}/delete`,{});}
  catch(e){say('',e.message);}refresh();}
$('mepic').onclick=()=>{if(!cur)return say('Add a project first.');pickPicture(cur,'user');};
