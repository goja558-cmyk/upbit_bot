#!/usr/bin/env python3
"""patch_multiticker.py — 멀티 종목 시세 공유 패치"""
import os, sys, shutil, py_compile
from datetime import datetime

BASE_DIR     = "/home/trade/upbit_bot"
MANAGER_FILE = os.path.join(BASE_DIR, "manager.py")
BOT_FILE     = os.path.join(BASE_DIR, "upbit_bot.py")
BACKUP_DIR   = os.path.join(BASE_DIR, "backups")
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
        print(f"  복원 완료: {baks[-1]}")

# ============================================================
# [1] manager.py 패치
# ============================================================
TICKER_FEED_FUNC = (
    "\n"
    "# ============================================================\n"
    "# [PATCH] ticker_feed\n"
    "# ============================================================\n"
    "import requests as _ticker_req\n"
    "\n"
    "def _ticker_feed_loop():\n"
    '    """전체 코인 종목 시세를 1회 API 호출로 수집 → shared/ticker_{mkt}.json"""\n'
    "    import json as _tj, time as _tt, os as _to\n"
    "    while True:\n"
    "        try:\n"
    "            markets = [\n"
    "                w.market for w in list(_workers)\n"
    "                if isinstance(w, CoinWorker)\n"
    "            ]\n"
    "            if markets:\n"
    "                res = _ticker_req.get(\n"
    '                    "https://api.upbit.com/v1/ticker",\n'
    '                    params={"markets": ",".join(markets)},\n'
    "                    timeout=5\n"
    "                )\n"
    "                if res.status_code == 200:\n"
    "                    now_ts = _tt.time()\n"
    "                    for item in res.json():\n"
    '                        mkt  = item.get("market", "")\n'
    '                        code = mkt.replace("KRW-", "").lower()\n'
    '                        path = _to.path.join(SHARED_DIR, f"ticker_{code}.json")\n'
    "                        tmp  = path + \".tmp\"\n"
    "                        with open(tmp, \"w\") as f:\n"
    "                            _tj.dump({\n"
    '                                "market": mkt,\n'
    '                                "price":  float(item.get("trade_price", 0)),\n'
    '                                "volume": float(item.get("acc_trade_volume_24h", 0)),\n'
    '                                "ts":     now_ts,\n'
    "                            }, f)\n"
    "                        _to.replace(tmp, path)\n"
    "        except Exception:\n"
    "            pass\n"
    "        _tt.sleep(1.0)\n"
    "\n"
)

THREAD_START = (
    "\n"
    "    # ── [PATCH] ticker_feed 스레드 ──────────────────────\n"
    "    threading.Thread(target=_ticker_feed_loop, daemon=True, name=\"ticker-feed\").start()\n"
    "    cprint(\"✅ [ticker_feed] 멀티 종목 시세 공유 시작\", Fore.CYAN)\n"
)

def patch_manager(src):
    # 1) ticker_feed 함수 삽입 (run_manager 직전)
    anchor1 = "def run_manager():"
    if "_ticker_feed_loop" in src:
        print("  ⏭ ticker_feed 이미 존재")
    elif anchor1 in src:
        src = src.replace(anchor1, TICKER_FEED_FUNC + anchor1)
        print("  ✅ ticker_feed 함수 삽입")
    else:
        print("  ❌ run_manager() 못 찾음"); return src

    # 2) 스레드 시작 — "{'='*50}" 포함 cprint 라인 뒤에 삽입
    #    실제 라인: cprint(f"\n{'='*50}", Fore.CYAN, bright=True)
    if "ticker-feed" in src:
        print("  ⏭ 스레드 이미 존재")
    else:
        # 라인 단위로 찾기
        lines = src.split("\n")
        inserted = False
        for i, line in enumerate(lines):
            if "cprint" in line and "='*50}" in line and "bright=True" in line:
                lines.insert(i + 1, THREAD_START)
                inserted = True
                print(f"  ✅ 스레드 시작 삽입 (라인 {i+1})")
                break
        if inserted:
            src = "\n".join(lines)
        else:
            print("  ❌ 스레드 삽입 위치 못 찾음")
    return src

# ============================================================
# [2] upbit_bot.py 패치
# ============================================================
OLD_GPV = (
    "def get_price_and_volume(market=None):\n"
    '    """현재가 + 거래량 조회. 반환: (price, volume) 또는 (None, None)"""\n'
    "    if market is None:\n"
    "        market = MARKET_CODE\n"
    "    try:\n"
    "        _api_throttle()"
)

NEW_GPV = (
    "def get_price_and_volume(market=None):\n"
    '    """현재가 + 거래량 조회. 반환: (price, volume) 또는 (None, None)\n'
    "    [PATCH] 매니저 ticker 파일 우선 → 없거나 오래되면 직접 API fallback\"\"\"\n"
    "    import json as _gj, time as _gt\n"
    "    if market is None:\n"
    "        market = MARKET_CODE\n"
    "    try:\n"
    '        code = market.replace("KRW-", "").lower()\n'
    '        tick_path = os.path.join(SHARED_DIR, f"ticker_{code}.json")\n'
    "        if os.path.exists(tick_path):\n"
    "            with open(tick_path) as _f:\n"
    "                _td = _gj.load(_f)\n"
    '            if _gt.time() - _td.get("ts", 0) < 2.0:\n'
    '                return float(_td["price"]), float(_td["volume"])\n'
    "    except Exception:\n"
    "        pass\n"
    "    try:\n"
    "        _api_throttle()"
)

def patch_bot(src):
    if 'ticker_{code}.json' in src:
        print("  ⏭ 이미 패치됨"); return src
    if OLD_GPV in src:
        src = src.replace(OLD_GPV, NEW_GPV)
        print("  ✅ get_price_and_volume() 패치 완료")
    else:
        print("  ❌ 타겟 문자열 불일치")
        idx = src.find("def get_price_and_volume")
        if idx != -1:
            print("  실제 코드 확인:")
            print(repr(src[idx:idx+300]))
    return src

# ============================================================
# MAIN
# ============================================================
print("=" * 50)
print("patch_multiticker.py")
print("=" * 50)

print("\n[1] manager.py")
backup(MANAGER_FILE)
with open(MANAGER_FILE, encoding="utf-8") as f:
    msrc = f.read()
msrc = patch_manager(msrc)
with open(MANAGER_FILE, "w", encoding="utf-8") as f:
    f.write(msrc)
if not check(MANAGER_FILE):
    restore(MANAGER_FILE); sys.exit(1)

print("\n[2] upbit_bot.py")
backup(BOT_FILE)
with open(BOT_FILE, encoding="utf-8") as f:
    bsrc = f.read()
bsrc = patch_bot(bsrc)
with open(BOT_FILE, "w", encoding="utf-8") as f:
    f.write(bsrc)
if not check(BOT_FILE):
    restore(BOT_FILE); sys.exit(1)

print("\n" + "=" * 50)
print("완료! 적용:")
print("  git add manager.py upbit_bot.py patch_multiticker.py")
print("  git commit -m 'patch: multiticker feed'")
print("  git push && 텔레그램 /update")
print("=" * 50)
