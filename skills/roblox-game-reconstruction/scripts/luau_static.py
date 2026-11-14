"""Read-only Luau token/literal extraction. Never imports or executes game code."""
import bisect,decimal,re,struct
from dataclasses import dataclass

@dataclass
class Token:
    text:str
    start:int
    end:int
    kind:str

NUM=re.compile(r'(?:0[xX][\da-fA-F]+(?:\.[\da-fA-F]*)?(?:[pP][+-]?\d+)?|0[bB][01_]+|(?:\d[\d_]*(?:\.\d[\d_]*)?|\.\d[\d_]*)(?:[eE][+-]?\d+)?)')
LONG=re.compile(r'\[(=*)\[')

def tokenize(source):
    out=[];p=0;n=len(source)
    while p<n:
        c=source[p]
        if c.isspace():p+=1;continue
        if source.startswith('--',p):
            m=LONG.match(source,p+2)
            if m:
                end=source.find(']'+m[1]+']',m.end());p=n if end<0 else end+len(m[1])+2
            else:
                end=source.find('\n',p);p=n if end<0 else end+1
            continue
        start=p
        if c in "\"'`":
            quote=c;p+=1
            while p<n:
                if source[p]=='\\':p+=2
                elif source[p]==quote:p+=1;break
                else:p+=1
            out.append(Token(source[start:p],start,p,'interpolated'if quote=='`'else'string'));continue
        m=LONG.match(source,p)
        if m:
            end=source.find(']'+m[1]+']',m.end());p=n if end<0 else end+len(m[1])+2
            out.append(Token(source[start:p],start,p,'longstring'));continue
        m=NUM.match(source,p)
        if m:
            p=m.end();out.append(Token(m[0],start,p,'number'));continue
        if c.isalpha()or c=='_':
            p+=1
            while p<n and(source[p].isalnum()or source[p]=='_'):p+=1
            out.append(Token(source[start:p],start,p,'identifier'));continue
        op=next((o for o in ['...','..=','//=','::','==','~=','<=','>=','+=','-=','*=','/=','%=','^=','..','//','->']if source.startswith(o,p)),c)
        p+=len(op);out.append(Token(op,start,p,'symbol'))
    return out

def string_value(text):
    m=LONG.match(text)
    if m:
        s=text[m.end():-len(m[1])-2]
        return s[2:]if s.startswith('\r\n')else s[1:]if s.startswith(('\n','\r'))else s
    body=text[1:-1];out=[];p=0
    short={'a':'\a','b':'\b','f':'\f','n':'\n','r':'\r','t':'\t','v':'\v'}
    while p<len(body):
        c=body[p];p+=1
        if c!='\\':out.append(c);continue
        if p>=len(body):raise ValueError('unterminated escape')
        c=body[p];p+=1
        if c=='z':
            while p<len(body)and body[p].isspace():p+=1
        elif c=='x':out.append(chr(int(body[p:p+2],16)));p+=2
        elif c=='u'and p<len(body)and body[p]=='{':
            end=body.index('}',p);out.append(chr(int(body[p+1:end],16)));p=end+1
        elif c.isdigit():
            digits=c
            while p<len(body)and body[p].isdigit()and len(digits)<3:digits+=body[p];p+=1
            out.append(chr(int(digits)))
        else:out.append(short.get(c,c))
    return ''.join(out)

class Parser:
    def __init__(self,source):
        self.s=source;self.t=tokenize(source);self.starts=[0]+[m.end()for m in re.finditer('\n',source)]
    def loc(self,start,end):
        line=bisect.bisect_right(self.starts,start);last=bisect.bisect_right(self.starts,max(start,end-1))
        return{'line_start':line,'column_start':start-self.starts[line-1]+1,'line_end':last,'column_end':end-self.starts[last-1]+1}
    def atom(self,a,b):
        tokens=self.t[a:b];text=self.s[tokens[0].start:tokens[-1].end]if tokens else''
        node={'kind':'expression','source':text,'evaluated':False}
        if len(tokens)==1 and tokens[0].kind in{'string','longstring'}:
            try:node={'kind':'string','value':string_value(text),'source':text}
            except ValueError:pass
        elif text in {'true','false','nil'}:node={'kind':text if text=='nil'else'boolean','value':None if text=='nil'else text=='true','source':text}
        elif len(tokens)==1 and tokens[0].kind=='number'or len(tokens)==2 and tokens[0].text in {'-','+'}and tokens[1].kind=='number':
            node={'kind':'number','literal':text,'numeric_fidelity':'decompiled source literal; original constant association not inferred'}
            try:
                value=text.replace('_','')
                if '0x'in value.lower():f=float.fromhex(value);dec=str(decimal.Decimal.from_float(f))
                elif '0b'in value.lower():f=float(int(value,2));dec=str(int(value,2))
                else:dec=str(decimal.Decimal(value));f=float(value)
                node.update(decimal=dec,binary64_hex=struct.pack('>d',f).hex())
            except(ValueError,OverflowError,decimal.InvalidOperation):pass
        if tokens:node.update(self.loc(tokens[0].start,tokens[-1].end))
        return node
    def expression_end(self,a,stops={',',';','}'}):
        p=a;levels=[];blocks=[];pending_do=0
        while p<len(self.t):
            x=self.t[p].text;previous=self.t[p-1].text if p else''
            if not levels and not blocks and x in stops:return p
            if x in {'(','[','{'}:levels.append({'(':')','[':']','{':'}'}[x])
            elif levels and x==levels[-1]:levels.pop()
            elif x=='function':blocks.append('function')
            elif x in {'for','while'}:blocks.append(x);pending_do+=1
            elif x=='do':
                if pending_do:pending_do-=1
                else:blocks.append('do')
            elif x=='if' and previous not in {'=','return','(',':',',','and','or','then','else','['}:blocks.append('if')
            elif x=='repeat':blocks.append('repeat')
            elif x=='until'and blocks and blocks[-1]=='repeat':blocks.pop()
            elif x=='end'and blocks:blocks.pop()
            p+=1
        return p
    def value(self,a):
        if self.t[a].text=='{':
            node,b=self.table(a)
            if b==len(self.t)or self.t[b].text in {',',';','}'}:return node,b
        b=self.expression_end(a)
        if b==a:raise ValueError('empty value')
        return self.atom(a,b),b
    def table(self,a):
        assert self.t[a].text=='{';p=a+1;entries=[];array=0
        while p<len(self.t)and self.t[p].text!='}':
            start=p
            if self.t[p].text in {',',';'}:p+=1;continue
            if p+1<len(self.t)and self.t[p].kind=='identifier'and self.t[p+1].text=='=':
                key={'kind':'string','value':self.t[p].text};p+=2;implicit=False
            elif self.t[p].text=='[':
                end=self.expression_end(p+1,{']'})
                if end+1>=len(self.t)or self.t[end+1].text!='=':raise ValueError('computed key not terminated')
                key=self.atom(p+1,end);p=end+2;implicit=False
            else:
                array+=1;key={'kind':'number','literal':str(array),'decimal':str(array)};implicit=True
            value,p=self.value(p)
            entries.append({'key':key,'value':value,'implicit_key':implicit,**self.loc(self.t[start].start,self.t[p-1].end)})
            if p<len(self.t)and self.t[p].text not in {',',';','}'}:raise ValueError('table separator missing')
        if p>=len(self.t):raise ValueError('unterminated table')
        node={'kind':'table_constructor','entries':entries,'entry_count':len(entries),**self.loc(self.t[a].start,self.t[p].end)}
        return node,p+1
    def constructors(self):
        out=[];errors=[];p=0
        while p<len(self.t):
            if self.t[p].text!='{':p+=1;continue
            start=p
            try:
                node,p=self.table(p);node['source_prefix']=self.s[max(0,self.t[start].start-110):self.t[start].start];out.append(node)
            except(ValueError,IndexError,RecursionError)as e:
                errors.append({'reason':str(e),**self.loc(self.t[p].start,self.t[p].end)});p+=1
        return out,errors

def plain(node):
    if node['kind']in{'string','boolean','nil'}:return node['value']
    if node['kind']=='number':return{'$number':node.get('decimal',node['literal']),'$literal':node['literal']}
    if node['kind']=='table_constructor':
        return{'$table':[{'key':plain(e['key']),'value':plain(e['value'])}for e in node['entries']]}
    return{'$expression':node.get('source'),'$evaluated':False}
