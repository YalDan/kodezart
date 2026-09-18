import ast, json
S="<scratch>/recut/"
WT={"M1":"wt3-view-m1","M2":"wt3-view-m2","M3":"wt3-view-m3","M4":"wt3-view-m4","M5":"wt3-view-m5","M6":"wt3-view-m6","M7":"wt3-view-m7"}
out={}
for m,wt in WT.items():
    rep=json.load(open(S+f"cut_report_{m}.json"))
    paths=sorted({p for p in rep["whole"]+rep["shared"]+rep["created"] if p.endswith(".py")})
    bad=[]
    for p in paths:
        try:
            ast.parse(open(S+wt+"/"+p).read())
        except Exception as e:
            bad.append(p)
    out[m]={"checked":len(paths),"failures":bad}
json.dump(out,open(S+"syntax_check_r.json","w"),indent=1)
print(json.dumps({k:(v["checked"],len(v["failures"])) for k,v in out.items()}))
