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


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class OptionsTest(unittest.TestCase):
    def test_options(self):
        r = subprocess.run(["node", "-e", CHECK, str(ANSWERS)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
