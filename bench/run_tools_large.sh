#!/usr/bin/env bash
# B2 large tier: Graphify and codebase-memory-mcp on home-assistant, time and peak memory only
# (no truth). CodeGraphContext is left out: it hit the 35 GB cap on a 3.5k-file repo in B1.
# codebase-memory-mcp indexes in a daemon /usr/bin/time does not see, so all of its
# processes are sampled every 0.5 s and summed.
set -uo pipefail
cd "$(dirname "$0")"
B=$PWD; name=home-assistant; out=../reports/b2-tools
mkdir -p $out work/graphify work/cbm
for tool in graphify cbm; do [ -d work/$tool/$name ] || cp -al repos/$name work/$tool/$name; done
while [ "$(vmstat 3 2 | tail -1 | awk '{print 100-$15}')" -ge 90 ]; do sleep 30; done
(cd work/graphify/$name && /usr/bin/time -f "%e %M" -o $B/$out/graphify.time timeout 5400 \
   $B/tools/graphify/bin/graphify update . --no-cluster > $B/$out/graphify.log 2>&1) || echo "graphify failed"
python3 adapters/graphify_edges.py work/graphify/$name/graphify-out/graph.json $out/graphify.edges.jsonl > /dev/null 2>&1
rm -rf work/graphify/$name/graphify-out
echo "graphify $(cat $out/graphify.time)"
while [ "$(vmstat 3 2 | tail -1 | awk '{print 100-$15}')" -ge 90 ]; do sleep 30; done
peak=0
HOME=$B/tools/cbm/home /usr/bin/time -f "%e %M" -o $out/cbm.time timeout 5400 \
  python3 adapters/cbm_edges.py tools/cbm/codebase-memory-mcp work/cbm/$name $out/cbm.edges.jsonl > $out/cbm.log 2>&1 &
pid=$!
while kill -0 $pid 2>/dev/null; do
  t=$(for p in $(pgrep -f 'codebase-memory-mcp'); do awk '/VmRSS/{print $2}' /proc/$p/status 2>/dev/null; done | awk '{s+=$1} END{print s+0}')
  [ "$t" -gt "$peak" ] && peak=$t; sleep 0.5
done
echo "$peak" > $out/cbm.peak_kb
echo "cbm $(cat $out/cbm.time) peak_kb=$peak $(wc -l < $out/cbm.edges.jsonl) edges"
