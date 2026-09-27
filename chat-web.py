#!/usr/bin/env python3
"""Web page for the shared chat, on http://127.0.0.1:8765 only.

The log is found like ./chat does, from the working directory. Posting runs
./chat, so messages reach Codex exactly as from the
terminal. Messages go to AI tools, so the server refuses cross-site posts:
the Host and Origin must be this server and the body must be JSON (a
browser will not send that cross-site without a preflight we never answer).
"""
import html
import json
import os
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = int(os.environ.get("AGENT_CHAT_PORT", 8765))
CHAT = Path(__file__).resolve().parent / "chat"
LOG = Path(subprocess.run([str(CHAT), "--where"], capture_output=True, text=True,
                          check=True).stdout.strip())
ENV = {**os.environ, "AGENT_CHAT_LOG": str(LOG)}  # posts go to the same log
HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}

PAGE = r"""<!doctype html><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>__PROJECT__ chat</title>
<style>
:root{--bg:#f6f6f4;--fg:#1d1d1b;--mute:#77756f;--card:#fff;--line:#e2e0da;
--user:#2f6f4f;--claude:#b4572e;--codex:#2e5fb4}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--fg:#e8e6e1;--mute:#8e8b84;
--card:#21201e;--line:#34322f;--user:#6fbf93;--claude:#e08a5f;--codex:#7aa2ea}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.5 system-ui,sans-serif;display:flex;flex-direction:column;height:100vh}
header{padding:10px 16px;border-bottom:1px solid var(--line);font-weight:600;
display:flex;justify-content:space-between;align-items:center;gap:12px}
#log{flex:1;overflow-y:auto;padding:12px 16px;max-width:900px;width:100%;margin:0 auto}
.m{background:var(--card);border:1px solid var(--line);border-radius:8px;
padding:8px 12px;margin:0 0 8px;white-space:pre-wrap;overflow-wrap:anywhere}
.h{font-size:13px;color:var(--mute);margin-bottom:2px}.n{font-weight:600}
.user .n{color:var(--user)}.claude .n{color:var(--claude)}.codex .n{color:var(--codex)}
form{position:relative;display:flex;gap:8px;padding:12px 16px;border-top:1px solid var(--line);
max-width:900px;width:100%;margin:0 auto}
textarea{flex:1;resize:none;height:64px;font:inherit;padding:8px;border-radius:8px;
border:1px solid var(--line);background:var(--card);color:var(--fg)}
button{font:inherit;padding:0 18px;border-radius:8px;border:0;background:var(--user);color:#fff;cursor:pointer}
.ghost{background:none;color:var(--mute);border:1px solid var(--line);padding:3px 10px;
font-size:13px;font-weight:400}
#err{color:var(--claude);font-size:13px;padding:0 16px}
#ac{position:absolute;bottom:calc(100% - 6px);left:16px;min-width:200px;background:var(--card);
border:1px solid var(--line);border-radius:8px;padding:4px;box-shadow:0 4px 16px #0003}
#ac div{padding:5px 10px;border-radius:6px;cursor:pointer}#ac .sel{background:var(--line)}
#ac small{color:var(--mute);margin-left:8px}[hidden]{display:none!important}
</style>
<header><span>__PROJECT__ chat · you, Claude, Codex</span>
<button id=bell class=ghost type=button hidden>Enable notifications</button></header>
<div id=log></div><div id=err></div>
<form id=f><div id=ac role=listbox hidden></div>
<textarea id=t placeholder="Message (Enter sends, Shift+Enter new line; type @ to address someone)"></textarea>
<button>Send</button></form>
<script>
const $=id=>document.getElementById(id);
const log=$('log'),t=$('t'),err=$('err'),ac=$('ac'),bell=$('bell');
const NAMES=[['claude','Claude Code'],['codex','Codex'],['user','you']];
let last='',seen=-1,unread=0;

function parse(text){
  const out=[];
  for(const line of text.split('\n')){
    const m=line.match(/^\[([^\]]*)\] ([\w-]+): (.*)$/);
    if(m)out.push({time:m[1],from:m[2],text:m[3]});
    else if(out.length&&line)out[out.length-1].text+='\n'+line.replace(/^    /,'');
  }
  return out;
}
function render(text){
  if(text===last)return;last=text;
  const msgs=parse(text),atEnd=log.scrollHeight-log.scrollTop-log.clientHeight<40;
  log.replaceChildren(...msgs.map(x=>{
    const d=document.createElement('div'),h=document.createElement('div'),n=document.createElement('span');
    d.className='m '+x.from.replace(/-.*/,'');h.className='h';n.className='n';n.textContent=x.from;
    h.append(n,' · '+x.time);d.append(h,x.text);return d;}));
  if(atEnd)log.scrollTop=log.scrollHeight;
  if(seen>=0)for(const x of msgs.slice(seen))if(x.from!=='user')alertFor(x);
  seen=msgs.length;
}
function away(){return document.hidden||!document.hasFocus();}
function alertFor(x){
  if(!away())return;
  unread++;document.title=`(${unread}) __PROJECT__ chat`;
  if(window.Notification&&Notification.permission==='granted')
    new Notification(x.from,{body:x.text.slice(0,300),tag:'agent-chat-'+x.time+x.from});
}
function back(){if(!away()){unread=0;document.title='__PROJECT__ chat';}}
addEventListener('focus',back);document.addEventListener('visibilitychange',back);
function bellLabel(){
  if(!window.Notification){bell.hidden=true;return;}
  bell.hidden=Notification.permission==='granted';
  bell.textContent=Notification.permission==='denied'?'Notifications blocked in browser':'Enable notifications';
}
bell.onclick=async()=>{await Notification.requestPermission();bellLabel();};bellLabel();

// @ autocomplete
let matches=[],sel=0;
function token(){const m=t.value.slice(0,t.selectionStart).match(/(^|\s)@(\w*)$/);return m?m[2]:null;}
function showAc(){
  const q=token();
  matches=q===null?[]:NAMES.filter(([n])=>n.startsWith(q.toLowerCase()));
  if(!matches.length){ac.hidden=true;return;}
  sel=Math.min(sel,matches.length-1);
  ac.replaceChildren(...matches.map(([n,label],i)=>{
    const d=document.createElement('div'),s=document.createElement('small');
    d.textContent='@'+n;s.textContent=label;d.append(s);d.className=i===sel?'sel':'';
    d.setAttribute('role','option');d.onmousedown=e=>{e.preventDefault();pick(i);};return d;}));
  ac.hidden=false;
}
function pick(i){
  const pos=t.selectionStart,q=token(),start=pos-q.length-1,ins='@'+matches[i][0]+' ';
  t.value=t.value.slice(0,start)+ins+t.value.slice(pos);
  t.selectionStart=t.selectionEnd=start+ins.length;ac.hidden=true;sel=0;t.focus();
}
t.oninput=()=>{sel=0;showAc();};
t.onclick=showAc;t.onblur=()=>{ac.hidden=true;};
t.onkeydown=e=>{
  if(!ac.hidden){
    if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();
      sel=(sel+(e.key==='ArrowDown'?1:matches.length-1))%matches.length;showAc();return;}
    if(e.key==='Enter'||e.key==='Tab'){e.preventDefault();pick(sel);return;}
    if(e.key==='Escape'){ac.hidden=true;return;}
  }
  if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();$('f').requestSubmit();}
};

async function poll(){try{render(await (await fetch('log')).text());err.textContent=''}
  catch(e){err.textContent='Server not reachable'}}
$('f').onsubmit=async e=>{e.preventDefault();
  const msg=t.value.trim();if(!msg)return;t.value='';ac.hidden=true;
  const r=await fetch('post',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({msg})});
  const x=await r.text();err.textContent=r.ok?(x==='ok'?'':x):'Not sent: '+x;poll();};
poll();setInterval(poll,2000);t.focus();
</script>"""
PAGE = PAGE.replace("__PROJECT__", html.escape(LOG.parent.parent.name))


class Handler(BaseHTTPRequestHandler):
    def send(self, code, body, ctype="text/plain; charset=utf-8"):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.headers.get("Host") not in HOSTS:
            return self.send(403, "bad host")
        if self.path == "/":
            return self.send(200, PAGE, "text/html; charset=utf-8")
        if self.path == "/log":
            return self.send(200, LOG.read_text() if LOG.exists() else "")
        self.send(404, "not found")

    def do_POST(self):
        host = self.headers.get("Host")
        if host not in HOSTS or self.headers.get("Origin") != f"http://{host}":
            return self.send(403, "cross-site post refused")
        if self.path != "/post" or self.headers.get("Content-Type") != "application/json":
            return self.send(400, "bad request")
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 < length <= 20000:
            return self.send(400, "message too long")
        try:
            msg = str(json.loads(self.rfile.read(length))["msg"]).strip()
        except (ValueError, KeyError, TypeError):
            return self.send(400, "bad json")
        if not msg:
            return self.send(400, "empty message")
        r = subprocess.run([str(CHAT), "user", msg], env=ENV,
                           capture_output=True, text=True, timeout=90)
        self.send(200 if r.returncode == 0 else 500, r.stderr.strip() or "ok")

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
