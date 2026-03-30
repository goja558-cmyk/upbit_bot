#!/bin/bash
REPO_DIR="/home/trade/upbit_bot"
BRANCH="main"

cd "$REPO_DIR" || { echo "[$(date)] ERROR: 디렉토리 없음 $REPO_DIR"; exit 1; }

git add -f logs/*/indicator_log.csv 2>/dev/null

if git diff --cached --quiet; then
    exit 0
fi

git commit -m "auto: indicator log update $(date '+%Y-%m-%d %H:%M')"

git push origin "$BRANCH" 2>/dev/null || {
    git stash
    git pull --rebase origin "$BRANCH" 2>/dev/null
    git stash pop 2>/dev/null
    git push origin "$BRANCH"
}

if [ $? -eq 0 ]; then
    echo "[$(date)] OK: push 완료"
else
    echo "[$(date)] ERROR: push 실패"
    exit 1
fi
