import json,subprocess,sys
S="/private/tmp/claude-503/-Users-kodezart-Projects-kodezart/f325c30b-eb65-4b5e-83ca-1ef5846ade2d/scratchpad/"
mile,wt=sys.argv[1],sys.argv[2].rstrip('/')+'/'
rep=json.load(open(S+f"cut_report_{mile}.json"))
paths=sorted({p for p in rep["whole"]+rep["shared"]+rep["created"] if p.endswith(".py")})
bad=[]
for p in paths:
    r=subprocess.run(["/Users/kodezart/.local/bin/python3.12","-c","import ast,sys; ast.parse(open(sys.argv[1]).read())",wt+p],capture_output=True)
    if r.returncode: bad.append((p,r.stderr.decode().strip().splitlines()[-2:]))
print(json.dumps({"checked":len(paths),"bad":len(bad)}))
for b in bad: print(" BAD",b[0],b[1])
