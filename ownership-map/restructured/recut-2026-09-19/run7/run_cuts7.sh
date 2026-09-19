#!/bin/zsh
set -e
set -o pipefail
S=/private/tmp/claude-503/-Users-kodezart-Projects-kodezart/f2770f28-277d-4202-b936-349664afe907/scratchpad/recut
REPO=/Users/kodezart/Projects/kodezart
PY=/Users/kodezart/.local/bin/python3.12
DONOR=44bacf63c6f475eda29bedf2109ae03f71901ba2
MAIN=e1544ed749b994b2863b16b6318c61863fa4aa3e
BRSUF=recut7

cut_one () {
  M=$1; PARENT_SHA=$2; VIEW=$3
  WT=$S/wt3-view-$(echo $M | tr 'A-Z' 'a-z')
  echo "##### $M parent=$PARENT_SHA view=$VIEW wt=$WT"
  git -C $REPO worktree add --detach $WT $PARENT_SHA
  git -C $WT checkout -b ${VIEW}-${BRSUF}
  CUT_DONOR=$DONOR CUT_MAIN=$MAIN CUT_SUFFIX=.r $PY $S/cut_views_r.py $M $WT 2>&1 | tee $S/cutlog_$M.txt
  git -C $WT add -A
  git -C $WT commit -q -m "$M review view re-cut from union 44bacf63 on restructured base" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
  HEAD_SHA=$(git -C $WT rev-parse HEAD)
  echo "PARENT_$M=$PARENT_SHA" >> $S/heads.txt
  echo "HEAD_$M=$HEAD_SHA" | tee -a $S/heads.txt
}

rm -f $S/heads.txt
cut_one M1 e1544ed749b994b2863b16b6318c61863fa4aa3e v03/m1-scope-ports
H_M1=$(git -C $S/wt3-view-m1 rev-parse HEAD)
cut_one M4 $H_M1 v03/m4-criterion-lifecycle
H_M4=$(git -C $S/wt3-view-m4 rev-parse HEAD)
cut_one M3 $H_M4 v03/m3-plan-walk
H_M3=$(git -C $S/wt3-view-m3 rev-parse HEAD)
cut_one M2 $H_M3 v03/m2-organize
cut_one M5 $H_M3 v03/m5-deliver-terminate
H_M5=$(git -C $S/wt3-view-m5 rev-parse HEAD)
cut_one M6 $H_M5 v03/m6-audit
cut_one M7 $H_M5 v03/m7-run-supervisor
echo "ALL DONE"
cat $S/heads.txt
