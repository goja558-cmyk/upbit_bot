#!/bin/bash
# ============================================================
#  섹터로테이션 봇 배포 스크립트
#  사용법: bash deploy_sector.sh
# ============================================================

BOT_DIR="/home/trade/upbit_bot"

echo "📂 봇 디렉토리: $BOT_DIR"

# 파일 복사
cp sector_bot.py "$BOT_DIR/sector_bot.py"
echo "✅ sector_bot.py 복사 완료"

# sector_cfg.yaml 이 없을 때만 복사 (기존 키 보호)
if [ ! -f "$BOT_DIR/sector_cfg.yaml" ]; then
    cp sector_cfg.yaml "$BOT_DIR/sector_cfg.yaml"
    echo "✅ sector_cfg.yaml 기본 양식 생성"
    echo ""
    echo "⚠️  $BOT_DIR/sector_cfg.yaml 에 KIS API 키와 텔레그램 정보를 입력하세요!"
else
    echo "ℹ️  sector_cfg.yaml 이미 존재 — 덮어쓰지 않음"
fi

echo ""
echo "▶ 실행 방법:"
echo "   cd $BOT_DIR"
echo "   python sector_bot.py"
echo ""
echo "▶ 백그라운드 실행:"
echo "   nohup python sector_bot.py > sector_bot.log 2>&1 &"
