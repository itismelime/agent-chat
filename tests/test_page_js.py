"""The page's option parser (answers.js), run under node when it is installed."""
import shutil
import subprocess
import unittest
from pathlib import Path

ANSWERS = Path(__file__).parent.parent / "bullpen" / "answers.js"
CHECK = r"""
// evaluates only this repo's own answers.js, to test options() without a browser
const src=require('fs').readFileSync(process.argv[1],'utf8');
eval(src.match(/const OPT_RE=.*;/)[0]+src.match(/function options[\s\S]*?return found.length>=2\?found:\[\];\}/)[0]
  +';globalThis.options=options');
const keys=t=>options(t).map(o=>o.key).join(''),assert=require('assert');
assert.strictEqual(keys('Q? (A) its own screen; my pick. (B) The same without. (C) Mixed in.'),'ABC');
assert.deepStrictEqual(options('**A)** one\n**B)** two').map(o=>o.label),['one','two']);
assert.strictEqual(keys('Option 1: x\nOption 2: y'),'12');
assert.strictEqual(keys('- A: x\n- B: y\n- C: z'),'ABC');
assert.strictEqual(keys('1. Signing key\n2. Compat'),'');  // a numbered list is not a choice
assert.strictEqual(keys('Plan A is fine'),'');
assert.strictEqual(keys('A) only one'),'');
assert.strictEqual(keys('```\nA) in code\nB) too\n```'),'');
// pending(): only messages that ask something reach Needs an answer
eval(src.match(/function pending[\s\S]*?\n  return out.sort[^\n]*\n/)[0]+';globalThis.pending=pending');
globalThis.projects=[{id:'p'}];globalThis.dismissed={};globalThis.current=n=>n;
globalThis.lead=t=>((t.match(/^\s*(@[\w-]+[,:]?\s*)+/)||[''])[0].match(/@[\w-]+/g)||[]).map(x=>x.slice(1).toLowerCase());
const m=(n,from,text,x)=>Object.assign({n,from,text,time:'t'+n,kind:'claude'},x);
globalThis.msgs={p:[m(1,'a','@user FYI: done'),m(2,'b','@user Merge? (A) yes (B) no'),
  m(3,'c','Should I push?',{ask:true}),m(4,'d','private note',{dm:'d'})]};
globalThis.reactsBy={};
assert.deepStrictEqual(pending().map(x=>x.m.n),[2,3]);
reactsBy.p={3:{'👍':['user']},2:{'👀':['b']}};  // your reaction acknowledges #3; an agent's does not
assert.deepStrictEqual(pending().map(x=>x.m.n),[2]);
"""


FORMAT = Path(__file__).parent.parent / "bullpen" / "format.js"
FORMAT_CHECK = r"""
// format.js against a stand-in textarea (no DOM; insertText falls back to setRangeText)
const src=require('fs').readFileSync(process.argv[1],'utf8'),assert=require('assert');
globalThis.document={execCommand:()=>false};globalThis.Event=class{};
globalThis.t={value:'',selectionStart:0,selectionEnd:0,focus(){},dispatchEvent(){},
  setSelectionRange(s,e){this.selectionStart=s;this.selectionEnd=e;},
  setRangeText(x,s,e){this.value=this.value.slice(0,s)+x+this.value.slice(e);this.selectionStart=this.selectionEnd=s+x.length;}};
eval(src.split('\n$(\'fmt\')')[0]+';globalThis.wrap=wrap;globalThis.codeBlock=codeBlock;globalThis.prefixLines=prefixLines;globalThis.link=link');
const set=(v,s,e=s)=>{t.value=v;t.setSelectionRange(s,e);},sel=()=>t.value.slice(t.selectionStart,t.selectionEnd);
set('say hi now',4,6);wrap('**','**','bold text');assert.strictEqual(t.value,'say **hi** now');assert.strictEqual(sel(),'hi');
wrap('**','**','bold text');assert.strictEqual(t.value,'say hi now');assert.strictEqual(sel(),'hi');  // off again
set('',0);wrap('`','`','code');assert.strictEqual(t.value,'`code`');assert.strictEqual(sel(),'code');
set('look: x=1',6,9);codeBlock();assert.strictEqual(t.value,'look: \n```\nx=1\n```');assert.strictEqual(sel(),'x=1');
set('a\nb',0,3);prefixLines(i=>(i+1)+'. ',/^\d+\. /);assert.strictEqual(t.value,'1. a\n2. b');
prefixLines(i=>(i+1)+'. ',/^\d+\. /);assert.strictEqual(t.value,'a\nb');
set('see docs',4,8);link();assert.strictEqual(t.value,'see [docs](https://)');assert.strictEqual(sel(),'https://');
set('https://x.io',0,12);link();assert.strictEqual(t.value,'[text](https://x.io)');assert.strictEqual(sel(),'text');
"""


EMOJI_DATA = Path(__file__).parent.parent / "bullpen" / "emoji-data.js"
EMOJI_CHECK = r"""
// emojiFind from emoji.js over the shipped list
const fs=require('fs'),assert=require('assert');eval(fs.readFileSync(process.argv[1],'utf8').replace('const EMOJI=','globalThis.EMOJI='));
const src=fs.readFileSync(process.argv[2],'utf8');
eval('globalThis.EMOJI_ALL=EMOJI.flatMap(([,items])=>items);'+src.match(/function emojiFind[\s\S]*?return hit\.slice\(0,n\);\}/)[0]+';globalThis.emojiFind=emojiFind');
assert.strictEqual(emojiFind('thu',3)[0][0],'👍');
assert.strictEqual(emojiFind('rocket',3)[0][0],'🚀');
assert.strictEqual(emojiFind('party',9).some(x=>x[0]==='🎉'),true);
assert.strictEqual(emojiFind('zzqx',3).length,0);
assert.ok(EMOJI_ALL.length>500);
"""


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class EmojiTest(unittest.TestCase):
    def test_find(self):
        r = subprocess.run(["node", "-e", EMOJI_CHECK, str(EMOJI_DATA),
                            str(EMOJI_DATA.with_name("emoji.js"))], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


AWAY = Path(__file__).parent.parent / "bullpen" / "away.js"
AWAY_CHECK = r"""
// awaySummary from away.js over stand-in messages
const src=require('fs').readFileSync(process.argv[1],'utf8'),assert=require('assert');
eval(src.match(/function awaySummary[\s\S]*?\n  return out;\}/)[0]+';globalThis.awaySummary=awaySummary');
const t=m=>new Date(Date.UTC(2026,8,30,m)).toISOString();
globalThis.projects=[{id:'a',name:'A'},{id:'b',name:'B'}];
globalThis.msgs={a:[{n:1,from:'x',kind:'claude',text:'old',time:t(0)},
  {n:2,from:'x',kind:'claude',text:'see https://github.com/o/r/pull/7 and o/r#8',time:t(30)},
  {n:3,from:'board',kind:'board',text:'#1 moved',time:t(31)},{n:4,from:'user',kind:'user',text:'mine',time:t(32)}],
  b:[{n:1,from:'y',kind:'claude',text:'old',time:t(1)}]};
globalThis.pending=()=>[{p:{id:'a'},m:msgs.a[1]}];
const r=awaySummary(Date.UTC(2026,8,30,10));
assert.strictEqual(r.length,1);  // B has nothing new
assert.deepStrictEqual([r[0].messages,r[0].board,r[0].questions,r[0].prs],[1,1,1,['#7','#8']]);
"""


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class AwayTest(unittest.TestCase):
    def test_summary(self):
        r = subprocess.run(["node", "-e", AWAY_CHECK, str(AWAY)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class FormatTest(unittest.TestCase):
    def test_buttons(self):
        r = subprocess.run(["node", "-e", FORMAT_CHECK, str(FORMAT)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class OptionsTest(unittest.TestCase):
    def test_options(self):
        r = subprocess.run(["node", "-e", CHECK, str(ANSWERS)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
