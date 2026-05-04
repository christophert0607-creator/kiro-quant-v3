#!/bin/bash
# Kiro Quant V3 - Deep Graph Rebuild Skill
# 目的：解決 .openclaw 隱藏目錄導致的掃描失效問題，強制重生圖譜並更新快照。

PROJECT_ROOT="/home/tsukii0607/.openclaw/workspace-quant/kiro-quant-v3"
cd "$PROJECT_ROOT" || { echo "Error: Cannot cd to $PROJECT_ROOT"; exit 1; }

echo "🔍 [1/3] Clearing stale graphify cache..."
rm -f .graphify_* 2>/dev/null

echo "🚀 [2/3] Performing deep AST extraction (relative path mode)..."
python3 -c "from graphify.watch import _rebuild_code; from pathlib import Path; _rebuild_code(Path('.'))"

echo "📝 [3/3] Synchronizing AGENTS.md snapshot..."
# 確保目錄存在
if [ ! -f "graphify-out/GRAPH_REPORT.md" ]; then
    echo "❌ Error: graphify-out/GRAPH_REPORT.md not found!"
    exit 1
fi

NODES=$(grep -oE "[0-9]+ nodes" graphify-out/GRAPH_REPORT.md | head -1 | awk '{print $1}')
EDGES=$(grep -oE "[0-9]+ edges" graphify-out/GRAPH_REPORT.md | head -1 | awk '{print $1}')
COMMUNITIES=$(grep -oE "[0-9]+ communities" graphify-out/GRAPH_REPORT.md | head -1 | awk '{print $1}')
DATE=$(date +%Y-%m-%d)

# 更新 AGENTS.md
cat <<AGENTS_EOF > AGENTS.md
## graphify

This project has a graphify knowledge graph at graphify-out/.

### Knowledge Graph Snapshot ($DATE)
- **Status**: HEALTHY (Deep Rebuilt)
- **Stats**: $NODES nodes · $EDGES edges · $COMMUNITIES communities
- **God Nodes**: $(grep -A 5 "God Nodes" graphify-out/GRAPH_REPORT.md | tail -n 5 | sed 's/^[0-9]\. \`//g; s/\`//g' | tr '\n' ',' | sed 's/, $//')

Rules:
- Before answering architecture or codebase questions, read graphify-out/GRAPH_REPORT.md for god nodes and community structure
- If graphify-out/wiki/index.md exists, navigate it instead of reading raw files
- **IMPORTANT**: If new code is not showing up, run \`bash scripts/rebuild_graph.sh\` to force a deep refresh.
AGENTS_EOF

echo "✅ Rebuild complete! Stats: $NODES nodes, $EDGES edges."
