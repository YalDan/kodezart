import ast, json, os, re, sys
S="/private/tmp/claude-503/-Users-kodezart-Projects-kodezart/f325c30b-eb65-4b5e-83ca-1ef5846ade2d/scratchpad/"
MAIN=S+"tree_main/"; DONOR=S+"tree_donor/"
spec=json.load(open(S+"cut_specs.json"))["M1"]
OUT=S+"m1_materialized/"; os.makedirs(OUT,exist_ok=True)

def seg(lines,node):
    st=node.lineno
    for d in getattr(node,"decorator_list",[]) or []: st=min(st,d.lineno)
    return "\n".join(lines[st-1:node.end_lineno])

def topkey(node):
    if isinstance(node,(ast.Import,ast.ImportFrom)):
        k="import:"+ " ".join(x for x in [] ) # placeholder
    return None

def import_key(lines,node):
    body=seg(lines,node)
    return re.sub(r"\s+"," ","import:"+body.strip().replace("\n"," "))

def subkey(cls,sub):
    if isinstance(sub,(ast.FunctionDef,ast.AsyncFunctionDef)): return [f"{cls}.{sub.name}"]
    if isinstance(sub,ast.Assign): return [f"{cls}.{t.id}" for t in sub.targets if isinstance(t,ast.Name)]
    if isinstance(sub,ast.AnnAssign) and isinstance(sub.target,ast.Name): return [f"{cls}.{sub.target.id}"]
    return []

def parse(path,tree):
    src=open(tree+path).read(); lines=src.splitlines()
    t=ast.parse(src)
    top=[]   # (key, node)
    for n in t.body:
        if isinstance(n,(ast.Import,ast.ImportFrom)): top.append((import_key(lines,n),n))
        elif isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)): top.append((n.name,n))
        elif isinstance(n,ast.Assign):
            ks=[t2.id for t2 in n.targets if isinstance(t2,ast.Name)]
            top.append((ks[0] if ks else None,n))
        elif isinstance(n,ast.AnnAssign) and isinstance(n.target,ast.Name): top.append((n.target.id,n))
        else: top.append((None,n))
    return src,lines,top

def build_class(cls_node, side_lines, main_cls, main_lines, donor_cls, donor_lines, take, remove, from_donor):
    name=cls_node.name
    head_lines=[]
    st=cls_node.lineno
    for d in cls_node.decorator_list: st=min(st,d.lineno)
    # class header = lines from st up to first body stmt line -1
    first=cls_node.body[0].lineno
    for d in getattr(cls_node.body[0],"decorator_list",[]) or []: first=min(first,d.lineno)
    head="\n".join(side_lines[st-1:first-1])
    dsubs={}
    if donor_cls:
        for sub in donor_cls.body:
            for k in subkey(name,sub): dsubs[k]=seg(donor_lines,sub)
    msubs={}
    if main_cls:
        for sub in main_cls.body:
            for k in subkey(name,sub): msubs[k]=seg(main_lines,sub)
    out=[head]
    # member order: main's members first (kept or replaced by donor), then donor-only taken members
    seen=set()
    if main_cls:
        for sub in main_cls.body:
            ks=subkey(name,sub)
            if not ks:
                out.append(seg(main_lines,sub)); continue
            k=ks[0]; seen.add(k)
            if k in remove: continue
            out.append(dsubs[k] if (k in take and k in dsubs) else seg(main_lines,sub))
    if donor_cls:
        for sub in donor_cls.body:
            ks=subkey(name,sub)
            if not ks:
                if not main_cls: out.append(seg(donor_lines,sub))
                continue
            k=ks[0]
            if k in seen or k in remove: continue
            if k in take: out.append(dsubs[k])
    body=[x for x in out[1:] if x.strip()]
    if not body: body=["    pass"]
    return "\n".join([out[0]]+body)


def materialize(path):
    sp=spec[path]
    take=set(sp["take_from_donor"]); remove=set(sp["remove"])
    msrc,mlines,mtop=parse(path,MAIN)
    dsrc,dlines,dtop=parse(path,DONOR)
    dmap={k:n for k,n in dtop if k}
    mkeys={k for k,_ in mtop if k}
    pieces=[]
    for k,n in mtop:
        if k is None:
            pieces.append(seg(mlines,n)); continue
        if k in remove: continue
        if isinstance(n,ast.ClassDef):
            dn=dmap.get(k)
            from_donor = k in take and isinstance(dn,ast.ClassDef)
            pieces.append(build_class(dn if from_donor else n,
                                      dlines if from_donor else mlines,
                                      n, mlines, dn if isinstance(dn,ast.ClassDef) else None, dlines,
                                      take, remove, from_donor))
        elif k in take and k in dmap:
            pieces.append(seg(dlines,dmap[k]))
        else:
            pieces.append(seg(mlines,n))
    # donor-only top-level symbols taken by M1
    added_imports=[];added_other=[]
    for k,n in dtop:
        if k and k in take and k not in mkeys:
            if isinstance(n,(ast.Import,ast.ImportFrom)): added_imports.append(seg(dlines,n))
            elif isinstance(n,ast.ClassDef):
                added_other.append(build_class(n,dlines,None,mlines,n,dlines,take|{f"{k}.{s.name}" for s in n.body if isinstance(s,(ast.FunctionDef,ast.AsyncFunctionDef))},remove,True))
            else: added_other.append(seg(dlines,n))
    # insert added imports after last main import
    last_imp=0
    for i,(k,n) in enumerate(mtop):
        if isinstance(n,(ast.Import,ast.ImportFrom)): last_imp=i+1
    pieces = pieces[:last_imp]+added_imports+pieces[last_imp:]+added_other
    out="\n\n".join(p for p in pieces if p.strip())+"\n"
    fp=OUT+os.path.basename(path)
    open(fp,"w").write(out)
    return fp

for p in ["src/kodezart/core/constants.py","src/kodezart/core/protocols.py","src/kodezart/types/domain/agent.py"]:
    fp=materialize(p)
    print(p,"->",fp,os.path.getsize(fp),"bytes")
