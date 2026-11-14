"""Conservative relational SSA/CFG checker for pinned Luau bytecode metadata.

Administrative loads/copies and effect-free branch regions are interpreted.
Potentially throwing/metamethod operations remain ordered observations. The
product explores every Boolean partition, not a sampled set of paths. Loop
cutpoints use an explicit equality/alias invariant, generalized only over
paired opaque results; failure to close the invariant stays UNVERIFIED.
"""
import collections
import math
import struct
import itertools


class Unsupported(Exception):
    def __init__(self, reason, pc=None):
        self.reason, self.pc = reason, pc
        super().__init__(reason)


class BDD:
    def __init__(self):
        self.atoms, self.keys, self.nodes, self.unique, self.cache = {}, [], [None, None], {}, {}

    def node(self, var, lo, hi):
        if lo == hi: return lo
        key = (var, lo, hi)
        if key not in self.unique:
            if len(self.nodes) > 8192: raise Unsupported('Boolean partition budget exceeded')
            self.unique[key] = len(self.nodes); self.nodes.append(key)
        return self.unique[key]

    def atom(self, key):
        if key not in self.atoms:
            self.atoms[key] = len(self.keys); self.keys.append(key)
        return self.node(self.atoms[key], 0, 1)

    def neg(self, n): return self.op('xor', n, 1)

    def op(self, op, a, b):
        key = (op, a, b)
        if key in self.cache: return self.cache[key]
        if a < 2 and b < 2:
            return int((bool(a) and bool(b)) if op == 'and' else (bool(a) or bool(b)) if op == 'or' else bool(a) != bool(b))
        va = self.nodes[a][0] if a > 1 else 10**9
        vb = self.nodes[b][0] if b > 1 else 10**9
        v = min(va, vb)
        al, ah = self.nodes[a][1:] if va == v else (a, a)
        bl, bh = self.nodes[b][1:] if vb == v else (b, b)
        r = self.node(v, self.op(op, al, bl), self.op(op, ah, bh))
        self.cache[key] = r; return r

    def remap(self, n, transform):
        if n < 2: return n
        v, lo, hi = self.nodes[n]
        a = self.atom(transform(self.keys[v]))
        return self.op('or', self.op('and', self.neg(a), self.remap(lo, transform)), self.op('and', a, self.remap(hi, transform)))

    def model(self, n):
        result = []
        while n > 1:
            v, lo, hi = self.nodes[n]
            take = hi != 0
            result.append((self.keys[v], take)); n = hi if take else lo
        return result


NIL = ('nil',)
def num(value): return ('number', struct.pack('>d', float(value)).hex())
def number(value): return struct.unpack('>d', bytes.fromhex(value[1]))[0]
def boolean(value): return ('bool', int(bool(value)))


class Proto:
    def __init__(self, module, index):
        self.module, self.p = module, module['protos'][index]
        self.ins = self.p['instructions']; self.by_pc = {i['pc']: n for n,i in enumerate(self.ins)}
        self.succ = [self.successors(n) for n in range(len(self.ins))]
        self.uses, self.defs = zip(*(self.access(n) for n in range(len(self.ins))))
        self.live = [set() for _ in self.ins]
        changed = True
        while changed:
            changed = False
            for n in range(len(self.ins)-1, -1, -1):
                after = set().union(*(self.live[j] for j in self.succ[n])) if self.succ[n] else set()
                live = self.uses[n] | (after-self.defs[n])
                if live != self.live[n]: self.live[n] = live; changed = True
        self.analysis = self.analyze()

    def target(self, n): return self.by_pc[self.ins[n]['target']]

    def successors(self, n):
        i=self.ins[n];op=i['op']; next_=[n+1] if n+1<len(self.ins) else []
        if op=='RETURN': return []
        if op in {'JUMP','JUMPBACK','JUMPX'} or op.startswith('FORGPREP') or (op=='LOADB' and i['c']): return [self.target(n)]
        if op.startswith('JUMP') or op.startswith('FASTCALL') or op in {'FORNPREP','FORNLOOP','FORGLOOP','CMPPROTO'}: return list(dict.fromkeys(next_+[self.target(n)]))
        return next_

    def capture(self, n):
        i=self.ins[n];p=self.p
        child = p['children'][i['d']] if i['op']=='NEWCLOSURE' else int(p['constants'][i['d']]['text'].split()[1])
        count=self.module['protos'][child]['upvalues']
        caps=self.ins[n+1:n+1+count]
        if len(caps)!=count or any(c['op']!='CAPTURE' for c in caps): raise Unsupported('closure capture metadata invalid',i['pc'])
        return child,caps

    def access(self,n):
        i=self.ins[n];o=i['op'];a,b,c=i['a'],i['b'],i['c'];u=set();d=set();maxr=self.p['maxstack']
        if o in {'LOADNIL','LOADB','LOADN','LOADK','LOADKX','GETGLOBAL','GETIMPORT','GETUPVAL','NEWTABLE','DUPTABLE'}: d={a}
        elif o in {'MOVE','NOT','MINUS','LENGTH','GETTABLEKS','GETTABLEN','ADDK','SUBK','MULK','DIVK','IDIVK','MODK','POWK','ANDK','ORK'}:u={b};d={a}
        elif o in {'SUBRK','DIVRK'}:u={c};d={a}
        elif o in {'GETTABLE','ADD','SUB','MUL','DIV','IDIV','MOD','POW','AND','OR'}:u={b,c};d={a}
        elif o=='CONCAT':u=set(range(b,c+1));d={a}
        elif o in {'SETGLOBAL','SETUPVAL'}:u={a}
        elif o in {'SETTABLEKS','SETTABLEN'}:u={a,b}
        elif o=='SETTABLE':u={a,b,c}
        elif o=='NAMECALL':u={b};d={a,a+1}
        elif o in {'CALL','CALLFB'}:u=set(range(a,a+b if b else maxr));d=set(range(a,maxr))
        elif o=='RETURN':u=set(range(a,a+b-1 if b else maxr))
        elif o=='GETVARARGS':d=set(range(a,a+b-1 if b else maxr))
        elif o=='SETLIST':u={a}|set(range(b,b+c-1 if c else maxr))
        elif o in {'JUMPIF','JUMPIFNOT','JUMPXEQKNIL','JUMPXEQKB','JUMPXEQKN','JUMPXEQKS','CMPPROTO'}:u={a}
        elif o in {'JUMPIFEQ','JUMPIFNOTEQ','JUMPIFLT','JUMPIFNOTLT','JUMPIFLE','JUMPIFNOTLE'}:u={a,i['aux']}
        elif o in {'FORNPREP','FORNLOOP'}:u=set(range(a,a+3));d=set(range(a,a+3)) if o=='FORNPREP'else{a+2}
        elif o.startswith('FORGPREP'):u=set(range(a,a+3));d=set(range(a,a+3))
        elif o=='FORGLOOP':u=set(range(a,a+3));d=set(range(a+2,a+3+(i['aux']&255)))
        elif o in {'NEWCLOSURE','DUPCLOSURE'}:
            _,caps=self.capture(n);u={v['b']for v in caps if v['a']!=2};d={a}
        elif o=='CAPTURE':u={b}if a!=2 else set()
        elif o.startswith('FASTCALL'):
            end=self.target(n);call=self.ins[end-1]
            if call['op']not in {'CALL','CALLFB'}:raise Unsupported('FASTCALL fallback boundary invalid',i['pc'])
            base,argc=call['a'],call['b'];u=set(range(base+1,base+argc if argc else maxr))
            if o!='FASTCALL':u.add(b)
            if o=='FASTCALL2':u.add(i['aux'])
            if o=='FASTCALL3':u|={i['aux']&255,(i['aux']>>8)&255}
            d=set(range(base,maxr))
        return u,d

    def analyze(self):
        reachable=set();work=[0]
        while work:
            n=work.pop()
            if n in reachable:continue
            reachable.add(n);work.extend(self.succ[n])
        leaders={0}
        for n,children in enumerate(self.succ):
            if children!=([n+1]if n+1<len(self.ins)else[]):
                leaders.update(children)
                if n+1<len(self.ins):leaders.add(n+1)
        starts=sorted(leaders);blocks=[];owner={}
        for k,start in enumerate(starts):
            end=starts[k+1]if k+1<len(starts)else len(self.ins)
            blocks.append((start,end))
            for n in range(start,end):owner[n]=k
        active={owner[n]for n in reachable};succ={k:{owner[n]for n in self.succ[end-1]}for k,(start,end)in enumerate(blocks)}
        pred={k:set()for k in active}
        for k in active:
            for to in succ[k]:pred[to].add(k)
        dom={k:({0}if k==0 else set(active))for k in active}
        changed=True
        while changed:
            changed=False
            for k in sorted(active-{0}):
                d={k}|set.intersection(*(dom[x]for x in pred[k]))
                if d!=dom[k]:dom[k]=d;changed=True
        back=[(blocks[k][1]-1,blocks[to][0])for k in active for to in succ[k]if to in dom[k]]
        exit_=len(blocks);pd={k:set(active)|{exit_}for k in active};pd[exit_]={exit_}
        reaches={k for k in active if not succ[k]};todo=list(reaches)
        while todo:
            for k in pred[todo.pop()]:
                if k not in reaches:reaches.add(k);todo.append(k)
        for k in active-reaches:pd[k]={k}
        changed=True
        while changed:
            changed=False
            for k in sorted(reaches,reverse=True):
                new={k}|set.intersection(*(pd[x]for x in (succ[k]or{exit_})))
                if new!=pd[k]:pd[k]=new;changed=True
        return {'reachable_instructions':len(reachable),'basic_blocks':len(active),
                'unreachable_pcs':[i['pc']for n,i in enumerate(self.ins)if n not in reachable],
                'backedges':[(self.ins[a]['pc'],self.ins[b]['pc'])for a,b in back],
                'loop_headers':sorted({self.ins[b]['pc']for _,b in back}),
                'dominators':dom,'post_dominators':pd}

    def constant(self,k):
        c=self.p['constants'][k];kind=c['kind'];t=c['text']
        if kind=='number':
            f=float(t)
            if math.isnan(f):raise Unsupported('exact NaN bits unavailable')
            return num(f)
        if kind=='nil':return NIL
        if kind=='boolean':return boolean(t=='true')
        if kind=='string':
            if 'stringBytes'not in c:raise Unsupported('full string bytes unavailable')
            return('string',c['stringBytes'])
        if kind=='integer':raise Unsupported('experimental integer value semantics require exact fallback')
        if kind=='import':return('import',tuple(c['importPath']))
        if kind=='closure':return('closure-constant',('pool',self.p['index'],k,'closure'),('proto',int(t.split()[1])))
        if kind=='table':return('template',('pool',self.p['index'],k,'table'),tuple(self.constant(x)for x in c['keys']),tuple(NIL if x<0 else self.constant(x)for x in c['values']))
        raise Unsupported('unsupported exact constant: '+kind)


class FunctionProof:
    def __init__(self, owner, left, right, state_budget=600):
        self.owner,self.l,self.r=owner,left,right
        self.b=BDD();self.counter=0;self.state_budget=state_budget;self.rules=collections.Counter();self.locations=[]

    def fresh(self,kind='any'):
        self.counter+=1;return('v',('fresh',self.counter),kind)

    def truth(self,v):
        if v[0]=='bool':return v[1]
        if v==NIL:return 0
        if v[0]in{'number','integer','string'}:return 1
        if v[0]=='select':
            return self.b.op('or',self.b.op('and',v[1],self.truth(v[2])),self.b.op('and',self.b.neg(v[1]),self.truth(v[3])))
        return self.b.atom(('truthy',v))

    def equal(self,a,b,guard):
        if a==b:return True
        if a[0]=='bool'and b[0]=='bool':return self.b.op('and',guard,self.b.op('xor',a[1],b[1]))==0
        return False

    def eq_condition(self,a,b):
        if a[0]=='v'and a[2]=='bool':a=('bool',self.truth(a))
        if b[0]=='v'and b[2]=='bool':b=('bool',self.truth(b))
        if a[0]=='bool'and b[0]=='bool':return self.b.neg(self.b.op('xor',a[1],b[1]))
        primitive={'number','integer','string','nil'}
        if a[0]in primitive and b[0]in primitive:
            if a[0]=='number'and b[0]=='number':return int(number(a)==number(b))
            return int(a==b)
        if a[0]=='bool'and b[0]in primitive or b[0]=='bool'and a[0]in primitive:return 0
        if a==b and a[0]=='v'and a[2]in{'function','table','bool','string'}:return 1
        if a[0]in primitive|{'bool'}or b[0]in primitive|{'bool'}:
            if repr(a)>repr(b):a,b=b,a
            return self.b.atom(('scalar_eq',a,b))
        return None

    def get(self,env,r,top):
        if r in env:return env[r]
        if top and r>=top[0]:return('pick',top[1],r-top[0])
        raise Unsupported('read without a supported reaching definition R'+str(r))

    def values(self,env,a,count,top):
        if count is not None:return tuple(self.get(env,r,top)for r in range(a,a+count))
        if top is None or top[0]<a:raise Unsupported('MULTRET producer/range unsupported')
        return tuple(self.get(env,r,top)for r in range(a,top[0]))+(('expand',top[1]),)

    def frontier(self,p,start):
        # state = pc, register SSA environment, dynamic top
        work=[(start[0],dict(start[1]),start[2],1,frozenset())];result=[];steps=0
        while work:
            n,e,top,g,seen=work.pop()
            if not g:continue
            steps+=1
            if steps>600 or len(work)+len(result)>64:raise Unsupported('effect-free path partition budget exceeded',p.ins[n]['pc'])
            key=(n,tuple(sorted(e.items())),top)
            if key in seen:
                result.append((g,{'kind':'diverge','label':('diverge',),'pc':p.ins[n]['pc'],'n':n,'env':e,'top':top}));continue
            seen=seen|{key}
            i=p.ins[n];o=i['op'];a,b,c=i['a'],i['b'],i['c'];next_=n+1
            get=lambda r:self.get(e,r,top)
            event=None; branch=None
            if o in {'NOP','COVERAGE','PREPVARARGS','CLOSEUPVALS'}:
                # REF captures are rejected at their creation, so CLOSEUPVALS is empty.
                pass
            elif o=='MOVE':e[a]=get(b);self.rules['register_copy_propagation']+=1
            elif o=='LOADNIL':e[a]=NIL
            elif o=='LOADN':e[a]=num(i['d']);self.rules['literal_load_unification']+=1
            elif o=='LOADB':e[a]=boolean(b);next_=p.target(n)if c else next_
            elif o in {'LOADK','LOADKX'}:
                value=p.constant(i['d']if o=='LOADK'else i['aux'])
                if value[0]in{'template','closure-constant'}:event=('constant_object',value)
                else:e[a]=value
            elif o=='GETVARARGS':
                if b:
                    for k in range(b-1):e[a+k]=('vararg',k)
                else:
                    top=(a,('vararg_packet',));e={r:v for r,v in e.items()if r<a}
            elif o=='NOT':e[a]=('bool',self.b.neg(self.truth(get(b))));self.rules['Boolean_value_normalization']+=1
            elif o in {'AND','OR','ANDK','ORK'}:
                x=get(b);y=p.constant(c)if o.endswith('K')else get(c);cond=self.truth(x)
                if o.startswith('AND'):yes,no=y,x
                else:yes,no=x,y
                if cond<2:e[a]=yes if cond else no
                elif yes==no:e[a]=yes
                elif yes[0]=='bool'and no[0]=='bool':e[a]=('bool',self.b.op('or',self.b.op('and',cond,yes[1]),self.b.op('and',self.b.neg(cond),no[1])))
                else:e[a]=('select',cond,yes,no)
            elif o in {'JUMP','JUMPBACK','JUMPX'}:next_=p.target(n)
            elif o in {'JUMPIF','JUMPIFNOT'}:
                cond=self.truth(get(a));cond=self.b.neg(cond)if o=='JUMPIFNOT'else cond
                branch=(cond,p.target(n),n+1)
            elif o.startswith('JUMP'):
                invert=False
                if o.startswith('JUMPXEQ'):
                    x=get(a);invert=bool(i['aux']>>31)
                    y=NIL if o=='JUMPXEQKNIL'else boolean(i['aux']&1)if o=='JUMPXEQKB'else p.constant(i['aux']&0xffffff)
                    operation='eq'
                else:
                    x,y=get(a),get(i['aux']);invert=o in {'JUMPIFNOTEQ','JUMPIFNOTLT','JUMPIFNOTLE'}
                    operation='eq'if o in {'JUMPIFEQ','JUMPIFNOTEQ'}else'lt'if o in {'JUMPIFLT','JUMPIFNOTLT'}else'le'if o in {'JUMPIFLE','JUMPIFNOTLE'}else None
                if operation is None:raise Unsupported('unsupported conditional '+o,i['pc'])
                cond=self.eq_condition(x,y)if operation=='eq'else None
                if cond is None and operation in {'lt','le'}and x[0]==y[0]=='number':cond=int(number(x)<number(y)if operation=='lt'else number(x)<=number(y))
                if cond is None:event=('compare',operation,x,y)
                else:branch=(self.b.neg(cond)if invert else cond,p.target(n),n+1)
                if event:event={'label':event,'kind':'compare','invert':invert,'target':p.target(n),'fall':n+1,'types':['bool']}
            elif o=='RETURN':event={'kind':'return','label':('return',self.values(e,a,b-1 if b else None,top)),'types':[]}
            elif o in {'GETGLOBAL','GETIMPORT','GETUPVAL'}:
                value=('upvalue',b)if o=='GETUPVAL'else p.constant(i['d']if o=='GETIMPORT'else i['aux'])
                event=(o,value)
            elif o in {'SETGLOBAL','SETUPVAL'}:event=(o,('upvalue',b)if o=='SETUPVAL'else p.constant(i['aux']),get(a))
            elif o in {'GETTABLE','GETTABLEKS','GETTABLEN','SETTABLE','SETTABLEKS','SETTABLEN','NAMECALL'}:
                obj=get(b)
                key_=get(c)if o in {'GETTABLE','SETTABLE'}else num(c+1)if o.endswith('N')else p.constant(i['aux'])
                event=('get',obj,key_)if o.startswith('GET')else('namecall',obj,key_)if o=='NAMECALL'else('set',obj,key_,get(a))
            elif o in {'ADD','SUB','MUL','DIV','IDIV','MOD','POW','ADDK','SUBK','MULK','DIVK','IDIVK','MODK','POWK','SUBRK','DIVRK'}:
                op=o.replace('RK','').removesuffix('K')
                x,y=(p.constant(b),get(c))if o.endswith('RK')else(get(b),p.constant(c)if o.endswith('K')else get(c))
                folded=False
                if op in {'ADD','SUB','MUL'}and x[0]==y[0]=='number':
                    vx,vy=number(x),number(y)
                    if all(math.isfinite(z)and z==int(z)and abs(z)<=2**26 for z in [vx,vy]):
                        z=vx+vy if op=='ADD'else vx-vy if op=='SUB'else vx*vy
                        if abs(z)<=2**53:e[a]=num(z);folded=True;self.rules['exact_integer_constant_fold']+=1
                if not folded:event=('arithmetic',op,x,y)
            elif o in {'MINUS','LENGTH'}:
                v=get(b)
                if o=='MINUS'and v[0]=='number':e[a]=num(-number(v))
                elif o=='LENGTH'and v[0]=='string':e[a]=num(len(v[1]))
                else:event=(o,v)
            elif o=='CONCAT':event=('concat',tuple(get(r)for r in range(b,c+1)))
            elif o in {'CALL','CALLFB'}:event={'kind':'call','label':('call',get(a),self.values(e,a+1,b-1 if b else None,top)),'types':['any']*(c-1)if c else['packet'],'base':a,'multi':c==0}
            elif o=='NEWTABLE':event=('newtable',b,i['aux'])
            elif o=='DUPTABLE':event=('duptable',p.constant(i['d']))
            elif o=='SETLIST':event=('setlist',get(a),i['aux'],self.values(e,b,c-1 if c else None,top))
            elif o in {'NEWCLOSURE','DUPCLOSURE'}:
                child,caps=p.capture(n);capvalues=[]
                for cap in caps:
                    if cap['a']==1:raise Unsupported('REF closure lifetime requires exact certificate',i['pc'])
                    if cap['a']==0 and cap['b']==a:raise Unsupported('recursive self capture requires exact certificate',i['pc'])
                    capvalues.append(('up',cap['b'])if cap['a']==2 else('value',get(cap['b'])))
                slot=('pool',p.p['index'],i['d'],'closure')if o=='DUPCLOSURE'else None
                event=('closure',o,('proto',child),slot,tuple(capvalues))
                next_=n+1+len(caps)
            elif o in {'FORNPREP','FORNLOOP'}:
                event={'kind':'forn','label':(o,tuple(get(r)for r in range(a,a+3))),'types':['number']*3+['bool']if o=='FORNPREP'else['number','bool'],'base':a,'target':p.target(n),'fall':n+1,'invert':o=='FORNPREP'}
            elif o.startswith('FORGPREP'):event={'kind':'forprep','label':(o,tuple(get(r)for r in range(a,a+3))),'types':['any']*3,'base':a,'next':p.target(n)}
            elif o=='FORGLOOP':event={'kind':'forg','label':(o,i['aux'],tuple(get(r)for r in range(a,a+3))),'types':['any']*(1+(i['aux']&255))+['bool'],'base':a,'target':p.target(n),'fall':n+1,'invert':False}
            elif o.startswith('FASTCALL'):
                target=p.target(n);call=p.ins[target-1]
                if call['op']not in {'CALL','CALLFB'}:raise Unsupported('FASTCALL boundary invalid',i['pc'])
                if not call['b']:raise Unsupported('generic MULTRET FASTCALL arguments unsupported',i['pc'])
                args=tuple(get(r)for r in range(call['a']+1,call['a']+call['b']))if o=='FASTCALL'else(get(b),)
                if o=='FASTCALL2':args+=(get(i['aux']),)
                if o=='FASTCALL2K':args+=(p.constant(i['aux']),)
                if o=='FASTCALL3':args+=(get(i['aux']&255),get((i['aux']>>8)&255))
                event={'kind':'fastcall','label':(o,a,args,call['b'],call['c']),'types':['any']*(call['c']-1)+['bool']if call['c']else['packet','bool'],'base':call['a'],'multi':call['c']==0,'target':target,'fall':n+1}
            else:raise Unsupported('unsupported normalization instruction '+o,i['pc'])
            if event is not None:
                if not isinstance(event,dict):
                    kinds=['function']if o in {'NEWCLOSURE','DUPCLOSURE'}else['table']if o in {'NEWTABLE','DUPTABLE'}else['any']
                    writes=o not in {'SETGLOBAL','SETUPVAL','SETTABLE','SETTABLEKS','SETTABLEN','SETLIST'}
                    event={'kind':'normal','label':event,'types':kinds if writes else[],'base':a,'next':next_}
                    if o=='NAMECALL':event['self']=get(b)
                event.update(pc=i['pc'],n=n,env=e,top=top)
                event.setdefault('next',next_)
                result.append((g,event));continue
            if branch:
                cond,yes,no=branch
                work.append((yes,dict(e),top,self.b.op('and',g,cond),seen))
                work.append((no,dict(e),top,self.b.op('and',g,self.b.neg(cond)),seen));self.rules['effect_free_branch_partition']+=1
            else:
                if next_>=len(p.ins):raise Unsupported('fallthrough beyond function',i['pc'])
                work.append((next_,e,top,g,seen))
        return result

    def same(self,a,b,g):
        if isinstance(a,tuple)and isinstance(b,tuple):
            if a and b and a[0]in{'proto','pool'}and b[0]==a[0]:return self.owner.reference(a,b)
            if a and b and a[0]=='bool'and b[0]=='bool':return self.equal(a,b,g)
            return len(a)==len(b)and all(self.same(x,y,g)for x,y in zip(a,b))
        return a==b

    def apply(self,event,outputs):
        e=dict(event['env']);top=event['top'];kind=event['kind'];a=event.get('base',0)
        if kind in {'return','diverge'}:return []
        if kind=='compare':
            test=self.truth(outputs[0]);test=self.b.neg(test)if event['invert']else test
            return [(test,(event['target'],e,top)),(self.b.neg(test),(event['fall'],e,top))]
        if kind in {'forn','forg'}:
            op=event['label'][0]
            regs=list(range(a,a+3))if op=='FORNPREP'else[a+2]if op=='FORNLOOP'else list(range(a+2,a+2+len(outputs)-1))
            for r,v in zip(regs,outputs[:-1]):e[r]=v
            test=self.truth(outputs[-1]);test=self.b.neg(test)if event['invert']else test
            return [(test,(event['target'],e,top)),(self.b.neg(test),(event['fall'],e,top))]
        if kind=='forprep':
            for r,v in zip(range(a,a+3),outputs):e[r]=v
        elif kind=='fastcall':
            yes={r:v for r,v in e.items()if r<a};out=outputs[:-1]
            ytop=(a,out[0])if event['multi']else None
            if not event['multi']:
                for k,v in enumerate(out):yes[a+k]=v
            test=self.truth(outputs[-1])
            return[(test,(event['target'],yes,ytop)),(self.b.neg(test),(event['fall'],e,top))]
        elif kind=='call':
            e={r:v for r,v in e.items()if r<a};top=(a,outputs[0])if event['multi']else None
            if not event['multi']:
                for k,v in enumerate(outputs):e[a+k]=v
        else:
            for k,v in enumerate(outputs):e[a+k]=v
            if 'self'in event:e[a+1]=event['self']
        return[(1,(event['next'],e,top))]

    def generalize(self,left,right):
        names={}
        def value(v):
            if isinstance(v,tuple):
                if len(v)==3 and v[0]=='v':
                    names.setdefault(v,len(names));return('v',('bound',names[v]),v[2])
                if len(v)==2 and v[0]=='bool':return('bool',self.b.remap(v[1],value))
                return tuple(value(x)for x in v)
            return v
        def state(p,s):
            n,e,t=s
            live=p.live[n]
            e={r:value(v)for r,v in sorted(e.items())if r in live}
            t=value(t)if t else None
            return(n,e,t)
        l=state(self.l,left);r=state(self.r,right)
        key=(l[0],r[0],tuple(l[1].items()),tuple(r[1].items()),l[2],r[2])
        return key,l,r

    def run(self):
        if (self.l.p['params'],self.l.p['upvalues'],self.l.p['vararg'])!=(self.r.p['params'],self.r.p['upvalues'],self.r.p['vararg']):
            return{'status':'UNVERIFIED','reason':'parameter/upvalue/vararg interface differs'}
        lp={n:('param',n)for n in range(self.l.p['params'])};rp=dict(lp)
        work=[((0,lp,None),(0,rp,None))];seen=set();matched=0
        try:
            while work:
                key,l,r=self.generalize(*work.pop())
                if key in seen:self.rules['relational_loop_invariant_closure']+=1;continue
                seen.add(key)
                if len(seen)>self.state_budget:raise Unsupported('relational product state budget exceeded')
                lf,rf=self.frontier(self.l,l),self.frontier(self.r,r)
                lg=rg=0
                for g,_ in lf:lg=self.b.op('or',lg,g)
                for g,_ in rf:rg=self.b.op('or',rg,g)
                if lg!=1 or rg!=1:raise Unsupported('incomplete pure-path coverage')
                for ga,ea in lf:
                    for gb,eb in rf:
                        g=self.b.op('and',ga,gb)
                        if not g:continue
                        if ea['kind']!=eb['kind']or ea.get('types',[])!=eb.get('types',[]):
                            return self.failure(ea,eb,g,'observable action kind/arity differs',len(seen),matched)
                        if not self.same(ea['label'],eb['label'],g):return self.failure(ea,eb,g,'ordered action/data operands differ',len(seen),matched)
                        matched+=1
                        outputs=[self.fresh(k)for k in ea.get('types',[])]
                        for ca,sa in self.apply(ea,outputs):
                            for cb,sb in self.apply(eb,outputs):
                                if self.b.op('and',g,self.b.op('and',ca,cb)):work.append((sa,sb))
            return{'status':'CFG_VALIDATED','method':'relational SSA product with ordered effects/v1','states':len(seen),'matched_actions':matched,'rules':dict(self.rules)}
        except (Unsupported,KeyError,IndexError,ValueError)as exc:
            return{'status':'UNVERIFIED','reason':str(exc),'pc':getattr(exc,'pc',None),'states':len(seen),'matched_actions':matched,'rules':dict(self.rules)}

    def failure(self,a,b,g,reason,states,matched):
        report={'status':'UNVERIFIED','reason':reason,'original_pc':a['pc'],'recompiled_pc':b['pc'],
                'original_action':a['label'],'recompiled_action':b['label'],'condition_model':self.b.model(g),
                'states':states,'matched_actions':matched,'rules':dict(self.rules)}
        if states==1 and self.l.p['params']<=5:
            # A concrete witness is produced only in the explicitly interpreted
            # effect-free subset, never from satisfiability of opaque atoms.
            for args in itertools.product([NIL,boolean(False),boolean(True)],repeat=self.l.p['params']):
                env=dict(enumerate(args))
                try:
                    x=self.frontier(self.l,(0,dict(env),None));y=self.frontier(self.r,(0,dict(env),None))
                except (Unsupported,KeyError,IndexError):continue
                if len(x)!=1 or len(y)!=1 or x[0][0]!=1 or y[0][0]!=1:continue
                ex,ey=x[0][1],y[0][1]
                if ex['kind']not in {'return','diverge'}or ey['kind']not in {'return','diverge'}:continue
                def literal(v):
                    return isinstance(v,tuple)and(v[0]in{'nil','number','string'}or(v[0]=='bool'and v[1]<2))
                if ex['kind']=='return'and not all(literal(v)for v in ex['label'][1]):continue
                if ey['kind']=='return'and not all(literal(v)for v in ey['label'][1]):continue
                if ex['label']!=ey['label']:
                    report.update(status='CFG_MISMATCH',counterexample={'kind':'static_pure_bytecode_subset','arguments':args,'original':ex['label'],'recompiled':ey['label'],
                                  'scope':'context-free function inputs; no game execution or behavior-verification claim'})
                    break
        return report


class ModuleProof:
    def __init__(self,left,right,state_budget=600):
        self.left,self.right=left,right;self.pm={};self.pr={};self.cm={};self.cr={};self.todo=[];self.results=[];self.state_budget=state_budget

    def bind_proto(self,a,b):
        if a in self.pm:return self.pm[a]==b
        if b in self.pr:return False
        self.pm[a]=b;self.pr[b]=a;self.todo.append((a,b));return True

    def reference(self,a,b):
        if a[0]=='proto':return self.bind_proto(a[1],b[1])
        if len(a)!=len(b)or a[-1]!=b[-1]or not self.bind_proto(a[1],b[1]):return False
        key=(a[1],a[2]);other=(b[1],b[2])
        if key in self.cm:return self.cm[key]==other
        if other in self.cr:return False
        self.cm[key]=other;self.cr[other]=key;return True

    def exact(self,a,b):
        from cfg_verify import CanonicalModule,Unverified
        x,y=CanonicalModule(self.left),CanonicalModule(self.right)
        snapshot=(dict(self.pm),dict(self.pr),dict(self.cm),dict(self.cr),list(self.todo))
        accepted=False
        try:
            if x.signature(a)!=y.signature(b):return None
            reverse={v:k for k,v in y.proto_ids.items()}
            for lp,identity in x.proto_ids.items():
                rp=reverse[identity]
                if not self.bind_proto(lp,rp):return None
                slots={v:k for k,v in y.slot_maps.get(rp,{}).items()}
                for constant,slot in x.slot_maps.get(lp,{}).items():
                    kind=self.left['protos'][lp]['constants'][constant]['kind']
                    if not self.reference(('pool',lp,constant,kind),('pool',rp,slots[slot],kind)):return None
            accepted=True
            return {'status':'CFG_VALIDATED','method':'v0.3.2 exact instruction/prototype certificate retained','states':0,'matched_actions':0,'rules':{'exact_fallback':1}}
        except (Unverified,KeyError,IndexError):return None
        finally:
            if not accepted:self.pm,self.pr,self.cm,self.cr,self.todo=snapshot

    def run(self,original_index,recompiled_index):
        self.bind_proto(original_index,recompiled_index);done=set()
        try:
            while self.todo:
                a,b=self.todo.pop()
                if (a,b)in done:continue
                done.add((a,b))
                p,q=Proto(self.left,a),Proto(self.right,b)
                # Check before mutating the global reference maps. Exact fallback
                # maps prototype/cache identities as well as matching bodies.
                result=self.exact(a,b)
                if result is None:result=FunctionProof(self,p,q,self.state_budget).run()
                result.update(original_proto=a,recompiled_proto=b,
                              original_analysis={k:v for k,v in p.analysis.items()if k not in {'dominators','post_dominators'}},
                              recompiled_analysis={k:v for k,v in q.analysis.items()if k not in {'dominators','post_dominators'}})
                self.results.append(result)
                if result['status']!='CFG_VALIDATED':return{'status':result['status'],'functions':self.results,'reason':result.get('reason'),'counterexample':result.get('counterexample'),'location':{'original_proto':a,'recompiled_proto':b,'original_pc':result.get('original_pc',result.get('pc')),'recompiled_pc':result.get('recompiled_pc')}}
            return{'status':'CFG_VALIDATED','method':'relational SSA/CFG plus bijective prototype/cache references/v1','functions':self.results,
                    'prototype_mapping':self.pm,'constant_object_mapping':[(a,b)for a,b in self.cm.items()],
                    'assumptions':['same external environment and coupled call outcomes','ordered upvalue slots','no asynchronous mutation between VM instructions','REF captures require exact fallback','debug/source locations, timing, allocation failure excluded']}
        except (Unsupported,KeyError,ValueError,IndexError)as e:
            return{'status':'UNVERIFIED','reason':str(e),'location':{'original_proto':a,'recompiled_proto':b,'original_pc':getattr(e,'pc',None)},'functions':self.results}
