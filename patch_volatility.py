#!/usr/bin/env python3
"""변동성 돌파 전략 추가 — 코인봇 OR 조건, 인버스봇 돌파 확인"""
import os, sys, shutil, py_compile
from datetime import datetime

BASE_DIR   = "/home/trade/upbit_bot"
COIN_FILE  = os.path.join(BASE_DIR, "upbit_bot.py")
INV_FILE   = os.path.join(BASE_DIR, "inverse_bot.py")
BACKUP_DIR = os.path.join(BASE_DIR, "backups")

def backup(path):
    ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = os.path.join(BACKUP_DIR, os.path.basename(path) + f".bak_{ts}")
    shutil.copy2(path, dst)
    print(f"  백업: {dst}")

def check(path):
    try:
        py_compile.compile(path, doraise=True)
        print("  ✅ 문법 OK"); return True
    except py_compile.PyCompileError as e:
        print(f"  ❌ {e}"); return False

def restore(path):
    baks = sorted([x for x in os.listdir(BACKUP_DIR) if os.path.basename(path) in x])
    if baks:
        shutil.copy2(os.path.join(BACKUP_DIR, baks[-1]), path)
        print(f"  복원: {baks[-1]}")

COIN_VBREAK_FUNC = (
    "\ndef get_day_ohlcv(market=None, count=2):\n"
    '    """일봉 OHLCV 조회 — 변동성 돌파용"""\n'
    "    if market is None:\n"
    "        market = MARKET_CODE\n"
    "    try:\n"
    "        _api_throttle()\n"
    "        res = requests.get(\n"
    '            f"{UPBIT_BASE}/candles/days",\n'
    '            params={"market": market, "count": count},\n'
    "            timeout=5\n"
    "        )\n"
    "        if res.status_code == 200:\n"
    '            data = sorted(res.json(), key=lambda x: x["candle_date_time_utc"])\n'
    "            return data\n"
    "    except Exception as e:\n"
    '        cprint(f"[일봉 조회 오류] {e}", Fore.YELLOW)\n'
    "    return []\n"
    "\n"
    "_vbreak_k     = 0.5\n"
    "_vbreak_cache = {}\n"
    "\n"
    "def calc_vbreak_target(market=None):\n"
    '    """변동성 돌파 목표가. 반환: 목표가 또는 None"""\n'
    "    from datetime import date as _date\n"
    "    if market is None:\n"
    "        market = MARKET_CODE\n"
    "    today = str(_date.today())\n"
    "    if market in _vbreak_cache:\n"
    "        cached_date, cached_target = _vbreak_cache[market]\n"
    "        if cached_date == today:\n"
    "            return cached_target\n"
    "    candles = get_day_ohlcv(market, count=3)\n"
    "    if len(candles) < 2:\n"
    "        return None\n"
    '    prev       = candles[-2]\n'
    '    today_c    = candles[-1]\n'
    '    prev_range = float(prev["high_price"]) - float(prev["low_price"])\n'
    '    today_open = float(today_c["opening_price"])\n'
    "    target     = today_open + prev_range * _vbreak_k\n"
    "    _vbreak_cache[market] = (today, target)\n"
    '    cprint(f"[변동성돌파] {market} 목표가:{target:,.2f}", Fore.CYAN)\n'
    "    return target\n"
    "\n"
    "def check_vbreak_signal(price, market=None):\n"
    '    """변동성 돌파 신호. 반환: (신호여부, 목표가)"""\n'
    "    target = calc_vbreak_target(market)\n"
    "    if target is None:\n"
    "        return False, 0\n"
    "    return price >= target, target\n"
    "\n"
)

OLD_BUY_COND = (
    '            rsi_ok   = rsi is not None and rsi <= bot["rsi_buy"]\n'
    '            ma_ok    = bool(ma20 and ma60 and ma20 > ma60)\n'
    '            drop_ok  = drop_pct is not None and drop_pct >= bot["drop"]\n'
    '            vol_ok   = vol is not None and VOL_MIN_PCT <= vol <= VOL_MAX_PCT\n'
    '            vwap_ok  = (not VWAP_FILTER) or (vwap is None) or (price <= vwap)\n'
    '            data_ok  = _real_data_count >= REAL_DATA_MIN\n'
    '            can_buy  = rsi_ok and ma_ok and drop_ok and vol_ok and vwap_ok and data_ok'
)
NEW_BUY_COND = (
    '            rsi_ok   = rsi is not None and rsi <= bot["rsi_buy"]\n'
    '            ma_ok    = bool(ma20 and ma60 and ma20 > ma60)\n'
    '            drop_ok  = drop_pct is not None and drop_pct >= bot["drop"]\n'
    '            vol_ok   = vol is not None and VOL_MIN_PCT <= vol <= VOL_MAX_PCT\n'
    '            vwap_ok  = (not VWAP_FILTER) or (vwap is None) or (price <= vwap)\n'
    '            data_ok  = _real_data_count >= REAL_DATA_MIN\n'
    '            vb_ok, vb_target = check_vbreak_signal(price)\n'
    '            signal_ok = (rsi_ok and drop_ok) or (vb_ok and ma_ok)\n'
    '            can_buy  = signal_ok and vol_ok and vwap_ok and data_ok'
)

OLD_INV_BUY = (
    "            # ── 09:00~09:05 매수 ──────────────────────────────\n"
    "            if h == 9 and m < 5:\n"
    "                if _pending_signal > 0 and not bot[\"has_stock\"]:\n"
    "                    cprint(f\"[매수 시도] 신호:{_pending_signal} {_pending_signal_msg}\", Fore.CYAN)\n"
    "                    do_buy(_pending_signal)\n"
    "                    _pending_signal = 0  # 오늘 신호 소진"
)
NEW_INV_BUY = (
    "            # ── 09:00~09:50 변동성 돌파 매수 ─────────────────\n"
    "            if h == 9 and _pending_signal > 0 and not bot[\"has_stock\"]:\n"
    "                price_now = get_price(STOCK_CODE)\n"
    "                if price_now > 0:\n"
    "                    if not hasattr(trading_loop, '_open_price') or \\\n"
    "                       not hasattr(trading_loop, '_open_date') or \\\n"
    "                       trading_loop._open_date != date.today():\n"
    "                        trading_loop._open_price = price_now\n"
    "                        trading_loop._open_date  = date.today()\n"
    "                        cprint(f'[인버스 시가] {price_now:,}원', Fore.CYAN)\n"
    "                    open_p    = trading_loop._open_price\n"
    "                    vb_target = open_p * 1.003\n"
    "                    if price_now >= vb_target or m < 3:\n"
    "                        cprint(f'[매수] 신호:{_pending_signal} 현재:{price_now:,} 목표:{vb_target:,.0f}', Fore.CYAN)\n"
    "                        do_buy(_pending_signal)\n"
    "                        _pending_signal = 0\n"
    "                    elif m >= 50:\n"
    "                        cprint(f'[매수 포기] 돌파 실패', Fore.YELLOW)\n"
    "                        send_msg(f'⚠️ 돌파 미확인 — 오늘 매수 포기\\n목표: {vb_target:,.0f}원 / 현재: {price_now:,}원')\n"
    "                        _pending_signal = 0"
)

def patch_coin(src):
    if "check_vbreak_signal" in src:
        print("  ⏭ 이미 패치됨"); return src
    anchor = "# ============================================================\n# [7] 지표 계산"
    if anchor in src:
        src = src.replace(anchor, COIN_VBREAK_FUNC + anchor, 1)
        print("  ✅ 변동성 돌파 함수 삽입")
    else:
        print("  ❌ 함수 삽입 위치 못 찾음"); return src
    if OLD_BUY_COND in src:
        src = src.replace(OLD_BUY_COND, NEW_BUY_COND, 1)
        print("  ✅ 매수 조건 OR 추가")
    else:
        print("  ⚠️  매수 조건 위치 못 찾음")
    return src

def patch_inv(src):
    if "vb_target = open_p" in src:
        print("  ⏭ 이미 패치됨"); return src
    if OLD_INV_BUY in src:
        src = src.replace(OLD_INV_BUY, NEW_INV_BUY, 1)
        print("  ✅ 인버스봇 돌파 확인 추가")
    else:
        print("  ⚠️  위치 못 찾음")
        idx = src.find("09:00~09:05")
        if idx != -1:
            print(repr(src[idx-10:idx+200]))
    return src

print("=" * 50)
print("patch_volatility.py")
print("=" * 50)

print("\n[1] upbit_bot.py")
backup(COIN_FILE)
with open(COIN_FILE, encoding="utf-8") as f:
    src = f.read()
src = patch_coin(src)
with open(COIN_FILE, "w", encoding="utf-8") as f:
    f.write(src)
if not check(COIN_FILE):
    restore(COIN_FILE); sys.exit(1)

print("\n[2] inverse_bot.py")
backup(INV_FILE)
with open(INV_FILE, encoding="utf-8") as f:
    src = f.read()
src = patch_inv(src)
with open(INV_FILE, "w", encoding="utf-8") as f:
    f.write(src)
if not check(INV_FILE):
    restore(INV_FILE); sys.exit(1)

print("\n완료!")
print("  git add upbit_bot.py inverse_bot.py patch_volatility.py")
print("  git commit -m 'feat: volatility breakout'")
print("  git push && 텔레그램 /update")
