// Formatting buttons above the message box. Each one writes Markdown around the selection (a
// placeholder, selected, when there is none); pressing it again on formatted text takes it off.
// insertText keeps the browser's undo, so Ctrl+Z reverts a button like typing.
function insertText(text){
  if(!document.execCommand('insertText',false,text)){  // fallback: no undo, but the same text
    t.setRangeText(text,t.selectionStart,t.selectionEnd,'end');t.dispatchEvent(new Event('input'));}}
function wrap(before,after,placeholder){
  const v=t.value,s=t.selectionStart,e=t.selectionEnd;t.focus();
  if(v.slice(s-before.length,s)===before&&v.slice(e,e+after.length)===after&&s>=before.length){
    const inner=v.slice(s,e);t.setSelectionRange(s-before.length,e+after.length);insertText(inner);
    t.setSelectionRange(s-before.length,s-before.length+inner.length);return;}
  const inner=v.slice(s,e)||placeholder;
  insertText(before+inner+after);t.setSelectionRange(s+before.length,s+before.length+inner.length);}
function codeBlock(){  // fenced, on lines of its own
  const v=t.value,s=t.selectionStart,e=t.selectionEnd,inner=v.slice(s,e)||'code';
  const lead=s>0&&v[s-1]!=='\n'?'\n':'',tail=e<v.length&&v[e]!=='\n'?'\n':'';t.focus();
  insertText(lead+'```\n'+inner+'\n```'+tail);
  const at=s+lead.length+4;t.setSelectionRange(at,at+inner.length);}
function prefixLines(prefix,match){  // the selected lines, whole; all prefixed already: take it off
  const v=t.value,s=v.lastIndexOf('\n',t.selectionStart-1)+1;let e=v.indexOf('\n',t.selectionEnd);if(e<0)e=v.length;
  const ls=v.slice(s,e).split('\n'),on=ls.every(l=>match.test(l));
  const out=ls.map((l,i)=>on?l.replace(match,''):prefix(i)+l).join('\n');
  t.focus();t.setSelectionRange(s,e);insertText(out);t.setSelectionRange(s,s+out.length);}
function link(){
  const s=t.selectionStart,sel=t.value.slice(s,t.selectionEnd);t.focus();
  if(/^https?:\/\/\S+$/.test(sel)){insertText('[text]('+sel+')');t.setSelectionRange(s+1,s+5);return;}
  const label=sel||'text';insertText('['+label+'](https://)');
  t.setSelectionRange(s+label.length+3,s+label.length+11);}
// [label, title, action, Ctrl+key]; only keys Firefox and Chrome leave to the page
const FORMATS=[
  ['<b>B</b>','Bold (Ctrl+B)',()=>wrap('**','**','bold text'),'b'],
  ['<i>I</i>','Italic (Ctrl+I)',()=>wrap('*','*','italic text'),'i'],
  ['<s>S</s>','Strikethrough',()=>wrap('~~','~~','struck text')],
  ['<code>`</code>','Inline code (Ctrl+E)',()=>wrap('`','`','code'),'e'],
  ['<code>```</code>','Code block',codeBlock],
  ['Link','Link',link],
  ['❝','Quote',()=>prefixLines(()=>'> ',/^> ?/)],
  ['•','Bulleted list',()=>prefixLines(()=>'- ',/^[-*] /)],
  ['1.','Numbered list',()=>prefixLines(i=>(i+1)+'. ',/^\d+\. /)]];
$('fmt').replaceChildren(...FORMATS.map(([label,title,act])=>{
  const b=document.createElement('button');b.type='button';b.innerHTML=label;b.title=title;b.setAttribute('aria-label',title);
  b.onmousedown=e=>e.preventDefault();  // keep the selection in the message box
  b.onclick=act;return b;}));
t.addEventListener('keydown',e=>{
  if(!(e.ctrlKey||e.metaKey)||e.altKey)return;
  const f=!e.shiftKey&&FORMATS.find(x=>x[3]===e.key);
  if(f){e.preventDefault();e.stopPropagation();f[2]();}});
