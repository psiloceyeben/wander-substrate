#!/usr/bin/env bash
set -u
cd /opt/wander
LOG=/opt/wander/data/refresh.log
echo "[$(date -Iseconds)] refresh start cards=$(python3 -c "import sqlite3; print(sqlite3.connect(\"data/wander.db\").execute(\"SELECT COUNT(*) FROM cards WHERE is_useful_content=1\").fetchone()[0])")" | tee -a $LOG
python3 scripts/09_classify.py >> $LOG 2>&1
python3 scripts/11_filter_pass.py --apply >> $LOG 2>&1
python3 scripts/10_layout_ring.py >> $LOG 2>&1
# /rendered: compact pinwheel of cards with real WebP thumbs only.
# Grows as more thumbs render — needs to refresh on the same 30-min cadence.
python3 scripts/10_layout_ring.py --rendered-only --max-cards 50000 >> $LOG 2>&1
# /game's "categories" mode: flat 12-sector x 6-ring layout (fixed bounds).
# Independent of /world's variable-arm pinwheel.
python3 scripts/10_layout_ring.py --categories-flat >> $LOG 2>&1
# Wikipedia layout (~6.8M article corpus). Same shape JSON as the /world layout.
[ -s data/wiki/wiki.db ] && python3 scripts/example_wiki/04_layout_wiki.py >> $LOG 2>&1
# NOTE: scripts/16_compute_category_edges.py is on its OWN daily timer
# (wander-category-edges.timer) — too heavy for the 30-min refresh cadence.
echo "[$(date -Iseconds)] refresh done useful=$(python3 -c "import sqlite3; print(sqlite3.connect(\"data/wander.db\").execute(\"SELECT COUNT(*) FROM cards WHERE is_useful_content=1\").fetchone()[0])")" | tee -a $LOG
