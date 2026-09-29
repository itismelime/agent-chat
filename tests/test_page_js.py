"""The page's option parser (answers.js), run under node when it is installed."""
import shutil
import subprocess
import unittest
from pathlib import Path

ANSWERS = Path(__file__).parent.parent / "agentchat" / "answers.js"
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
assert.deepStrictEqual(pending().map(x=>x.m.n),[2,3]);
"""


FORMAT = Path(__file__).parent.parent / "agentchat" / "format.js"
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
