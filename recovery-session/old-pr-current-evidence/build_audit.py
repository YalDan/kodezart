import ast,collections,datetime,difflib,hashlib,json,re,subprocess
from pathlib import Path
ROOT=Path('/private/tmp/kodezart-recovery-session'); E=ROOT/'old-pr-current-evidence';REPO='/private/tmp/kodezart-v03-recovery-integration'
REFS={'main':'4661a24b599d75503a997f3ce122f3ad2da77048','donor':'36083f83f42c03240ebb5861fe284da2c9f04180','M1':'241e85cca03c963ec1ddd17e29f10700d499ce14','M4':'267262342719c99d4279e4ba020292b32556a34c'}
def git(*a):return subprocess.check_output(['git','-C',REPO,*a])
def tree(sha):
 return {row.split('\t',1)[1]:row.split()[2] for row in git('ls-tree','-r',sha).decode().splitlines()}
TREES={k:tree(v) for k,v in REFS.items()};CACHE={}
def content(blob):
 if blob is None:return ''
 if blob not in CACHE:CACHE[blob]=git('cat-file','blob',blob).decode(errors='replace')
 return CACHE[blob]
owners={p['path']:p['owners'] for p in json.loads((ROOT/'extraction-manifest-d2c6fce.json').read_text())['paths']}
allprs=[]
for n in [72,108,110,112,114,115,116,117]:
 info=json.loads((E/f'pr-{n}-info.json').read_text());head=info['headRefOid'];oldtree=tree(head);base=git('merge-base',info['baseRefOid'],head).decode().strip();raw=git('diff','--no-ext-diff','--no-renames','--unified=3',base,head).decode();(E/f'pr-{n}-local.patch').write_text(raw)
 files=[]
 for patch in re.split(r'(?=^diff --git )',raw,flags=re.M):
  if not patch:continue
  path=re.search(r'^diff --git a/(.*?) b/(.*)$',patch,re.M).group(2);old=content(oldtree.get(path));texts={k:content(t.get(path)) for k,t in TREES.items()}
  maps={}
  for k,txt in texts.items():
   mp={}
   for block in difflib.SequenceMatcher(None,old.splitlines(),txt.splitlines(),autojunk=False).get_matching_blocks():
    for i in range(block.size):mp[block.a+i+1]=block.b+i+1
   maps[k]=mp
  hunks=[]
  for i,h in enumerate(re.split(r'(?=^@@ )',patch,flags=re.M)[1:]):
   header,*lines=h.splitlines();m=re.match(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)',header);ol=int(m[1]);nl=int(m[3]);added=[];removed=[];post=[]
   for line in lines:
    if line.startswith('+'):added.append({'line':nl,'text':line[1:]});post.append(line[1:]);nl+=1
    elif line.startswith('-'):removed.append({'line':ol,'text':line[1:]});ol+=1
    elif line.startswith(' '):post.append(line[1:]);nl+=1;ol+=1
   positive='\n'.join(post)
   evidence={}
   for k,txt in texts.items():
    residual=[a for a in added if a['line'] not in maps[k]]
    evidence[k]={'same_path_exists':path in TREES[k],'whole_postimage_found':bool(positive) and positive in txt,'added_lines_exact_same_path':len(added)-len(residual),'unmapped_added_lines':residual}
   hunks.append({'index':i,'header':header,'sha256':hashlib.sha256(h.encode()).hexdigest(),'added':added,'removed':removed,'comparison':evidence})
  files.append({'path':path,'proposed_owners_v1':owners.get(path,[]),'source_blob':oldtree.get(path),'targets':{k:{'blob':t.get(path),'equals_old_head':t.get(path)==oldtree.get(path),'equals_donor':bool(t.get(path)) and t.get(path)==TREES['donor'].get(path),'differs_from_main':t.get(path)!=TREES['main'].get(path)} for k,t in TREES.items()},'hunks':hunks})
 threads=json.loads((E/f'pr-{n}-threads.json').read_text())['data']['repository']['pullRequest']['reviewThreads']
 allprs.append({'number':n,'url':info['url'],'state':info['state'],'head':head,'actual_base':info['baseRefOid'],'merge_base':base,'unique_commits':git('log','--format=%H %s',base+'..'+head).decode().splitlines(),'donor_contains_head_by_ancestry':subprocess.run(['git','-C',REPO,'merge-base','--is-ancestor',head,REFS['donor']]).returncode==0,'review_threads':threads,'comments':info['comments'],'reviews':info['reviews'],'files':files})
report={'captured_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'refs':REFS,'scope':'Current-head exact diff provenance. Mechanical exact-line/postimage matches do not independently establish semantic acceptance, deletion correctness, runtime behavior, or closure eligibility. Targets containing inherited main bytes are not accepted transfers. Each unmapped added line is preserved; removed lines remain explicit source obligations.','prs':allprs}
(ROOT/'old-pr-disposition-current.json').write_text(json.dumps(report,indent=2)+'\n')
summary=[]
for p in allprs:
 summary.append({'number':p['number'],'files':len(p['files']),'hunks':sum(len(f['hunks']) for f in p['files']),'exact_old_files_in_donor':[f['path'] for f in p['files'] if f['targets']['donor']['equals_old_head']],'changed_donor_files_identical_in_M1':[f['path'] for f in p['files'] if f['targets']['M1']['equals_donor'] and f['targets']['M1']['differs_from_main']],'changed_donor_files_identical_in_M4':[f['path'] for f in p['files'] if f['targets']['M4']['equals_donor'] and f['targets']['M4']['differs_from_main']]})
(E/'comparison-summary.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary,indent=2))
