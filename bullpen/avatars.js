// Avatar pictures (bullpen/avatars.js): yours and each agent's, over the letter and color, cropped as saved
// in Profile (profile.js). Uses the page's $, el, api, cur, say and refresh. The API needs its header,
// so pictures come as blob: URLs.
let avatarsBy={},myAv=null;const picUrls={};  // myAv: your picture, the same in every project (profile.js loads it)
const avOf=(pid,name)=>name==='user'?myAv:(avatarsBy[pid]||{})[name];
const avPath=(pid,name)=>name==='user'?'api/avatars/user':`api/projects/${pid}/avatars/${encodeURIComponent(name)}`;
const avKey=(pid,name)=>name==='user'?'user':pid+'/'+name;
function tookAv(pid,name,r){if(name==='user')myAv=r.avatar;else avatarsBy[pid]=r.avatars;redecorate(pid,name);}
function cropStyle(c){  // the saved square of the picture, as CSS background size and position
  if(!c)return 'center / cover';const pos=(o,s)=>s>=1?0:o/(1-s)*100;
  return `${pos(c.x,c.w)}% ${pos(c.y,c.h)}% / ${100/c.w}% ${100/c.h}%`;}
// inline, so no avatar rule's background shorthand (the user's dark one) resets it
const showPic=(d,url,crop)=>{d.style.background=`${cropStyle(crop)} no-repeat url(${url})`;d.classList.add('pic');};
function picUrl(pid,name){  // a promise of the picture's blob: URL, fetched once per version
  const a=avOf(pid,name);if(!a)return Promise.resolve(null);
  const key=avKey(pid,name)+'/'+a.v;
  if(!picUrls[key])picUrls[key]=fetch(avPath(pid,name),{headers:{'X-Bullpen':'1'}})
    .then(r=>r.ok?r.blob():null).then(b=>b&&URL.createObjectURL(b));
  return picUrls[key];}
function decorateAvatar(div,name,pid){
  div.dataset.who=avKey(pid,name);const a=avOf(pid,name);if(!a)return;
  picUrl(pid,name).then(url=>{if(url)showPic(div,url,a.crop);});}
function redecorate(pid,name){  // after a new picture or crop: the avatars already on the page too
  for(const d of document.querySelectorAll('.av'))if(d.dataset.who===avKey(pid,name)){d.style.removeProperty('background');d.classList.remove('pic');decorateAvatar(d,name,pid);}}
function pickPicture(pid,name){  // the file as it is when it can be; the page crops it square on show
  const f=el('input');f.type='file';f.accept='image/png,image/jpeg,image/webp,image/gif';
  f.onchange=async()=>{const file=f.files[0];if(!file)return;
    try{const data=['image/png','image/jpeg','image/webp'].includes(file.type)&&file.size<=1e6?await asDataUrl(file):await shrink(file);
      tookAv(pid,name,await api(avPath(pid,name),{data}));
      say('Picture uploaded: choose the part to show, then Save.');if(typeof openProfile==='function')openProfile(pid,name,true);refresh();}
    catch(e){say('',e.message);}};
  f.click();}
const asDataUrl=file=>new Promise((ok,bad)=>{const r=new FileReader();r.onload=()=>ok(r.result);r.onerror=()=>bad(r.error);r.readAsDataURL(file);});
async function shrink(file){  // too big, or a GIF: a 256 px square, drawn here
  const img=await createImageBitmap(file),s=Math.min(img.width,img.height),c=document.createElement('canvas');
  c.width=c.height=256;const g=c.getContext('2d');g.drawImage(img,(img.width-s)/2,(img.height-s)/2,s,s,0,0,256,256);
  const px=g.getImageData(0,0,256,256).data;  // privacy settings (Firefox's resistFingerprinting) blank what a page reads back
  if(px.every((v,i)=>v===px[i%4]))throw new Error('Your browser would not let the page resize the picture (a privacy setting). Choose a PNG, JPEG or WebP of at most 1 MB instead.');
  return c.toDataURL('image/png');}
async function removePicture(pid,name){try{await api(avPath(pid,name)+'/delete',{});}
  catch(e){say('',e.message);}refresh();}
