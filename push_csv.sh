#!/bin/bash
# CSV 자동 깃 푸시 스크립트
# cron: 0 * * * * /home/trade/upbit_bot/push_csv.sh >> /home/trade/upbit_bot/push_csv.log 2>&1

REPO_DIR="/home/trade/upbit_bot"
BRANCH="main"

cd "$REPO_DIR" || { echo "[$(date)] ERROR: 디렉토리 없음 $REPO_DIR"; exit 1; }

# 변경된 CSV 확인
git add *.csv 2>/dev/null

# 변경 없으면 조용히 종료
if git diff --cached --quiet; then
    exit 0
fi

# 커밋 & 푸시
git commit -m "auto: csv update $(date '+%Y-%m-%d %H:%M')"

if git push origin "$BRANCH"; then
    echo "[$(date)] OK: push 완료"
else
    echo "[$(date)] ERROR: push 실패"
    exit 1
fi
