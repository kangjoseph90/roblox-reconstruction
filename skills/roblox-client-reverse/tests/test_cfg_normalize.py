import json,os,subprocess,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from cfg_verify import disassemble
from cfg_normalize import ModuleProof

@unittest.skipUnless(os.environ.get('RCR_TEST_TOOLS'),'native tools required')
class NormalizationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.tools=json.loads(Path(os.environ['RCR_TEST_TOOLS']).read_text());self.n=0
    def module(self,s,opt=1):
        self.n+=1;p=self.root/(str(self.n)+'.luau');p.write_text(s)
        result=subprocess.run([self.tools['compiler']['argv'][0],'--binary','-O'+str(opt),str(p)],capture_output=True,check=True)
        p=p.with_suffix('.bytecode');p.write_bytes(result.stdout)
        return disassemble(self.tools['decompiler']['argv'][0],p,'standard')
    def proof(self,a,b,oa=1,ob=1):
        l,r=self.module(a,oa),self.module(b,ob)
        return ModuleProof(l,r).run(l['mainProto'],r['mainProto'])
    def same(self,a,b,oa=1,ob=1):
        p=self.proof(a,b,oa,ob);self.assertEqual(p['status'],'CFG_VALIDATED',p)
    def different(self,a,b):self.assertNotEqual(self.proof(a,b)['status'],'CFG_VALIDATED')
    def test_copy_and_register_reuse(self):self.same('local a=...; local b=a; return b','local x=...; return x')
    def test_constant_load_optimizations(self):self.same('return (2+3)*4','return 20',0,2)
    def test_boolean_materialization(self):self.same('local x=...; local b=not not x; if b then return 1 else return 2 end','local x=...; if x then return 1 else return 2 end')
    def test_short_circuit_partition(self):self.same('local x,y=...; if x and y then return 1 else return 2 end','local x,y=...; if not x then return 2 elseif not y then return 2 end; return 1')
    def test_effect_order_preserved(self):self.same('local a=f(); local b=g(); return a+b','local x=f(); local y=g(); local z=x+y; return z')
    def test_effect_order_change_rejected(self):self.different('f();g();return 1','g();f();return 1')
    def test_short_circuit_effect_change_rejected(self):self.different('local x=...; if x then f() end','local x=...; f()')
    def test_wrong_operand_rejected(self):self.different('local x,y=...; return f(x,y)','local x,y=...; return f(y,x)')
    def test_wrong_predicate_rejected(self):self.different('local x,y=...; if x then return 1 end; return 2','local x,y=...; if y then return 1 end; return 2')
    def test_for_loop_identity(self):self.same('local n=...; for i=1,n do f(i)end;return n','local m=...;for j=1,m do local a=j; f(a)end;return m')
    def test_while_loop_identity(self):self.same('local x=...;while x do x=f(x)end;return x','local y=...;while y do local z=f(y);y=z end;return y')
    def test_loop_bound_change_rejected(self):self.different('for i=1,3 do f(i)end','for i=1,4 do f(i)end')
    def test_multi_return_and_varargs(self):self.same('local a=...;return f(a,...)','local x=...;return f(x,...)')
    def test_closure_identity(self):self.same('return function(x)return x end','return function(y)return y end')
    def test_table_write_order_change_rejected(self):self.different('local t=...;t.x=f();t.y=g();return t','local t=...;t.y=g();t.x=f();return t')
    def test_metatable_arithmetic_order_not_assumed_pure(self):self.different('local x,y=...; local z=x+y; f();return z','local x,y=...; f();return x+y')

if __name__=='__main__':unittest.main()
