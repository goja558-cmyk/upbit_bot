#!/usr/bin/env python3
"""
patch_indicators.py
───────────────────
sector_bot.py 에 두 가지 지표 추가:
1. 분봉 볼린저밴드 + 캔들 패턴 (FHKST03010200)
2. 투자자별 매매동향 외국인/기관 (FHKST01010900)
"""
import os, sys, shutil, py_compile
from datetime import datetime

BASE_DIR   = "/home/trade/upbit_bot"
BOT_FILE   = os.path.join(BASE_DIR, "sector_bot.py")
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
        print("  ✅ 문법 OK"); return True
    except py_compile.PyCompileError as e:
        print(f"  ❌ {e}"); return False

def restore(path):
    baks = sorted([x for x in os.listdir(BACKUP_DIR) if os.path.basename(path) in x])
    if baks:
        shutil.copy2(os.path.join(BACKUP_DIR, baks[-1]), path)
        print(f"  복원: {baks[-1]}")

INDICATOR_FUNCS = '''
# ============================================================
# [PATCH] 볼린저밴드 + 투자자 동향
# ============================================================
import numpy as _np_ind

_BB_PERIOD = 20
_BB_K      = 2.0
_BB_NEAR   = 0.15   # %B 이하면 하단 근처
_INV_FILTER = True
_INV_CACHE  = {}
_INV_TTL    = 300   # 5분 캐시


def get_minute_candles(code, count=30):
    """당일 분봉 조회 FHKST03010200"""
    from datetime import datetime as _dt
    h = kis_headers("FHKST03010200")
    if not h:
        return []
    now_str = _dt.now().strftime("%H%M%S")
    res = api_call(
        "get",
        f"{_prod_url()}/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice",
        headers=h,
        params={
            "FID_ETC_CLS_CODE":       "",
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD":         code,
            "FID_INPUT_HOUR_1":       now_str,
            "FID_PW_DATA_INCU_YN":    "N",
        }
    )
    try:
        return [
            {
                "open":  int(c.get("stck_oprc", 0)),
                "high":  int(c.get("stck_hgpr", 0)),
                "low":   int(c.get("stck_lwpr", 0)),
                "close": int(c.get("stck_prpr", 0)),
            }
            for c in (res.get("output2", []) or [])[:count]
        ]
    except Exception as e:
        cprint(f"[분봉 오류] {e}", Fore.YELLOW)
        return []


def calc_bollinger(candles, period=20, k=2.0):
    """볼린저밴드 계산. 반환: (upper, mid, lower, pct_b)"""
    if len(candles) < period:
        return None, None, None, None
    closes = [c["close"] for c in candles[-period:]]
    mid    = sum(closes) / period
    std    = float(_np_ind.std(closes))
    upper  = mid + k * std
    lower  = mid - k * std
    cur    = closes[-1]
    pct_b  = (cur - lower) / (upper - lower) if (upper - lower) > 0 else 0.5
    return upper, mid, lower, pct_b


def is_hammer(c):
    body = abs(c["close"] - c["open"])
    if body == 0: return False
    lw = min(c["open"], c["close"]) - c["low"]
    uw = c["high"] - max(c["open"], c["close"])
    return lw >= body * 2 and uw <= body * 0.3


def is_bullish_reversal(candles):
    if len(candles) < 2: return False
    return candles[-2]["close"] < candles[-2]["open"] and candles[-1]["close"] > candles[-1]["open"]


def check_bollinger_signal(code):
    """볼린저 신호. 반환: (통과여부, 메시지)"""
    candles = get_minute_candles(code, 30)
    if not candles:
        return True, "분봉없음(통과)"
    _, _, _, pct_b = calc_bollinger(candles, _BB_PERIOD, _BB_K)
    if pct_b is None:
        return True, "계산불가(통과)"
    msgs = [f"%B={pct_b:.2f}"]
    # 상단 근처면 차단
    if pct_b >= 0.85:
        msgs.append("상단⚠️")
        return False, " ".join(msgs)
    if pct_b <= _BB_NEAR:
        msgs.append("하단✅")
    if is_hammer(candles[-1]):
        msgs.append("망치✅")
    if is_bullish_reversal(candles):
        msgs.append("양봉전환✅")
    return True, " ".join(msgs)


def get_investor_flow(code):
    """투자자 동향. 반환: (외국인_순매수, 기관_순매수)"""
    import time as _t
    now = _t.time()
    cached = _INV_CACHE.get(code)
    if cached and now - cached[0] < _INV_TTL:
        return cached[1], cached[2]
    h = kis_headers("FHKST01010900")
    if not h:
        return None, None
    res = api_call(
        "get",
        f"{_prod_url()}/uapi/domestic-stock/v1/quotations/inquire-investor",
        headers=h,
        params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code}
    )
    try:
        out = res.get("output", [])
        row = out[0] if isinstance(out, list) and out else out
        frgn = int(row.get("frgn_ntby_qty", 0))
        inst = int(row.get("orgn_ntby_qty", 0))
        _INV_CACHE[code] = (now, frgn, inst)
        return frgn, inst
    except Exception as e:
        cprint(f"[투자자 동향 오류] {e}", Fore.YELLOW)
        return None, None


def check_investor_signal(code):
    """투자자 필터. 반환: (통과여부, 메시지)"""
    if not _INV_FILTER:
        return True, "필터OFF"
    frgn, inst = get_investor_flow(code)
    if frgn is None:
        return True, "조회실패(통과)"
    msg = f"외국인{frgn:+,} 기관{inst:+,}"
    return (frgn > 0 or inst > 0), msg


def get_bollinger_status(code):
    candles = get_minute_candles(code, 30)
    if not candles:
        return f"{code}: 분봉 없음"
    upper, mid, lower, pct_b = calc_bollinger(candles)
    cur = candles[-1]["close"]
    if pct_b is None:
        return f"{code}: 계산 불가"
    return (
        f"📊 {code} 볼린저\\n"
        f"현재가: {cur:,}원\\n"
        f"상단: {upper:,.0f}  중간: {mid:,.0f}  하단: {lower:,.0f}\\n"
        f"%B: {pct_b:.2f}  망치: {'✅' if candles and is_hammer(candles[-1]) else '❌'}"
        f"  양봉전환: {'✅' if is_bullish_reversal(candles) else '❌'}"
    )


def get_investor_status(code):
    frgn, inst = get_investor_flow(code)
    if frgn is None:
        return f"{code}: 조회 실패"
    return (
        f"👥 {code} 수급\\n"
        f"외국인: {frgn:+,}주 {'✅' if frgn > 0 else '❌'}\\n"
        f"기관:   {inst:+,}주 {'✅' if inst > 0 else '❌'}\\n"
        f"신호: {'✅통과' if (frgn > 0 or inst > 0) else '❌차단'}"
    )

'''

# buy_etf 내 가격 확인 직후에 필터 삽입
OLD_FILTER = (
    "    info = get_price_info(code)\n"
    "    if not info or info[\"price\"] <= 0:\n"
    "        cprint(f\"[매수 실패] {code} 가격 조회 실패\", Fore.YELLOW)\n"
    "        return 0\n"
    "    price = info.get(\"ask\") or info[\"price\"]"
)

NEW_FILTER = (
    "    info = get_price_info(code)\n"
    "    if not info or info[\"price\"] <= 0:\n"
    "        cprint(f\"[매수 실패] {code} 가격 조회 실패\", Fore.YELLOW)\n"
    "        return 0\n"
    "    # ── [PATCH] 볼린저 + 투자자 필터 ──────────────────\n"
    "    bb_ok,  bb_msg  = check_bollinger_signal(code)\n"
    "    inv_ok, inv_msg = check_investor_signal(code)\n"
    "    cprint(f\"[매수필터] {code} BB:{bb_msg} 수급:{inv_msg}\", Fore.CYAN)\n"
    "    if not bb_ok:\n"
    "        send_msg(f\"⚠️ {code} 매수차단 — {bb_msg}\", force=True)\n"
    "        return 0\n"
    "    if not inv_ok:\n"
    "        send_msg(f\"⚠️ {code} 수급차단 — {inv_msg}\", force=True)\n"
    "        return 0\n"
    "    # ────────────────────────────────────────────────────\n"
    "    price = info.get(\"ask\") or info[\"price\"]"
)

# IPC 핸들러
OLD_IPC = '    elif c in ("/scores", "/score", "/스코어"):\n        _ipc_send_scores()'
NEW_IPC = (
    '    elif c in ("/scores", "/score", "/스코어"):\n'
    '        _ipc_send_scores()\n'
    '    elif c in ("/bollinger", "/bb"):\n'
    '        holdings = list(portfolio.keys())\n'
    '        msg = "\\n\\n".join(get_bollinger_status(c) for c in holdings) if holdings else "보유없음"\n'
    '        _write_ipc_result("[normal] " + msg)\n'
    '    elif c in ("/investor", "/수급"):\n'
    '        holdings = list(portfolio.keys())\n'
    '        msg = "\\n\\n".join(get_investor_status(c) for c in holdings) if holdings else "보유없음"\n'
    '        _write_ipc_result("[normal] " + msg)'
)

# TG 핸들러
OLD_TG = '    elif c in ("/scores", "/score", "/스코어"):\n        _send_scores()'
NEW_TG = (
    '    elif c in ("/scores", "/score", "/스코어"):\n'
    '        _send_scores()\n'
    '    elif c in ("/bollinger", "/bb"):\n'
    '        for code in (list(portfolio.keys()) or ["보유없음"]):\n'
    '            send_msg(get_bollinger_status(code) if code != "보유없음" else "보유없음", force=True)\n'
    '    elif c in ("/investor", "/수급"):\n'
    '        for code in (list(portfolio.keys()) or ["보유없음"]):\n'
    '            send_msg(get_investor_status(code) if code != "보유없음" else "보유없음", force=True)'
)

def patch(src):
    if "check_bollinger_signal" in src:
        print("  ⏭ 이미 패치됨"); return src

    # 1) 함수 삽입
    anchor = "def buy_etf(code, budget_krw):"
    if anchor in src:
        src = src.replace(anchor, INDICATOR_FUNCS + anchor, 1)
        print("  ✅ 지표 함수 삽입")
    else:
        print("  ❌ buy_etf() 못 찾음"); return src

    # 2) 매수 필터
    if OLD_FILTER in src:
        src = src.replace(OLD_FILTER, NEW_FILTER, 1)
        print("  ✅ 매수 필터 삽입")
    else:
        print("  ⚠️  매수 필터 위치 못 찾음")
        idx = src.find("def buy_etf")
        if idx != -1: print(repr(src[idx:idx+400]))

    # 3) IPC 핸들러
    if OLD_IPC in src:
        src = src.replace(OLD_IPC, NEW_IPC, 1)
        print("  ✅ IPC 핸들러 추가")
    else:
        print("  ⚠️  IPC 핸들러 위치 못 찾음")

    # 4) TG 핸들러
    if OLD_TG in src:
        src = src.replace(OLD_TG, NEW_TG, 1)
        print("  ✅ TG 핸들러 추가")
    else:
        print("  ⚠️  TG 핸들러 위치 못 찾음")

    return src

print("=" * 50)
print("patch_indicators.py")
print("=" * 50)

backup(BOT_FILE)
with open(BOT_FILE, encoding="utf-8") as f:
    src = f.read()
src = patch(src)
with open(BOT_FILE, "w", encoding="utf-8") as f:
    f.write(src)
if not check(BOT_FILE):
    restore(BOT_FILE); sys.exit(1)

print("\n완료! 적용:")
print("  git add sector_bot.py patch_indicators.py")
print("  git commit -m 'patch: bollinger + investor filter'")
print("  git push && 텔레그램 /update")
print()
print("새 명령어: /s bollinger  /s investor")
print("=" * 50)
