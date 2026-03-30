#!/usr/bin/env python3
"""
patch_no_tg_poll.py
───────────────────
upbit_bot.py — 매니저 하위 프로세스로 실행될 때 텔레그램 폴링 비활성화
매니저가 --config 인자로 실행하면 IPC 모드 → 폴링 스킵
직접 실행(단독 모드)이면 기존대로 폴링
"""
import os, sys, shutil, py_compile
from datetime import datetime

BASE_DIR = "/home/trade/upbit_bot"
BOT_FILE = os.path.join(BASE_DIR, "upbit_bot.py")
BACKUP_DIR = os.path.join(BASE_DIR, "backups")
os.makedirs(BACKUP_DIR, exist_ok=True)

def backup(path):
    ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = os.path.join(BACKUP_DIR, os.path.basename(path) + f".bak_{ts}")
    shutil.copy2(path, dst)
    print(f"  백업: {dst}")

def check(path):
    try:
        py_compile.compile(path, doraise=True)
        print(f"  ✅ 문법 OK")
        return True
    except py_compile.PyCompileError as e:
        print(f"  ❌ 문법 오류: {e}")
        return False

def restore(path):
    baks = sorted([x for x in os.listdir(BACKUP_DIR) if os.path.basename(path) in x])
    if baks:
        shutil.copy2(os.path.join(BACKUP_DIR, baks[-1]), path)
        print(f"  복원: {baks[-1]}")

# poll_telegram() 맨 앞에 IPC 모드면 즉시 리턴하는 가드 삽입
OLD_POLL = "def poll_telegram():"

NEW_POLL = (
    "def poll_telegram():\n"
    "    # [PATCH] 매니저 하위 프로세스(IPC 모드)면 폴링 스킵\n"
    "    # 매니저가 텔레그램 폴링을 전담하고 IPC로 명령 전달\n"
    "    if _ap_args.config is not None:\n"
    "        return"
)

def patch_bot(src):
    if "IPC 모드면 폴링 스킵" in src:
        print("  ⏭ 이미 패치됨"); return src

    if OLD_POLL in src:
        src = src.replace(OLD_POLL, NEW_POLL, 1)
        print("  ✅ poll_telegram() 가드 삽입")
    else:
        print("  ❌ poll_telegram() 못 찾음")
    return src

print("=" * 50)
print("patch_no_tg_poll.py")
print("=" * 50)

backup(BOT_FILE)
with open(BOT_FILE, encoding="utf-8") as f:
    src = f.read()
src = patch_bot(src)
with open(BOT_FILE, "w", encoding="utf-8") as f:
    f.write(src)
if not check(BOT_FILE):
    restore(BOT_FILE); sys.exit(1)

print("\n완료! 적용:")
print("  git add upbit_bot.py patch_no_tg_poll.py")
print("  git commit -m 'patch: disable tg poll in IPC mode'")
print("  git push && 텔레그램 /update")
print("=" * 50)
