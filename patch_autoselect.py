#!/usr/bin/env python3
"""
patch_autoselect.py
───────────────────
manager.py 에 거래대금 상위 종목 자동 선별 기능 추가

동작:
  매일 08:55 업비트 전체 KRW 시세 조회
  → 스테이블/유의/100원 미만/거래대금 기준 미달 필터링
  → 상위 N개 자동 등록, 탈락 종목 자동 제거
  → 예산 균등 분배

텔레그램 명령:
  /autoselect on 20       — 자동 선별 켜기 (상위 20개)
  /autoselect off         — 자동 선별 끄기
  /autoselect now         — 즉시 실행
  /autoselect status      — 현재 설정 확인
"""
import os, sys, shutil, py_compile
from datetime import datetime

BASE_DIR     = "/home/trade/upbit_bot"
MANAGER_FILE = os.path.join(BASE_DIR, "manager.py")
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
        print(f"  복원: {baks[-1]}")

# ============================================================
# 삽입할 코드
# ============================================================
AUTOSELECT_CODE = r'''
# ============================================================
# [PATCH] 거래대금 상위 종목 자동 선별
# ============================================================
import requests as _as_req

# 자동 선별 제외 목록
_AS_STABLECOINS = {
    "USDT","USDC","BUSD","DAI","TUSD","USDP","GUSD","FRAX",
    "LUSD","USDN","FDUSD","PYUSD","USDD","AEUR","UST",
}
# 자동 선별 설정 (yaml에 저장됨)
# autoselect:
#   enabled: true
#   count: 20
#   min_volume_krw: 5000000000   # 거래대금 최소 50억
#   min_price: 100               # 최소 가격 100원

_autoselect_last_run = 0.0

def _autoselect_fetch_top(count, min_volume_krw, min_price):
    """업비트 전체 KRW 종목 조회 → 필터링 → 거래대금 상위 N개 반환"""
    try:
        # 1) 전체 마켓 목록
        r = _as_req.get(
            "https://api.upbit.com/v1/market/all",
            params={"isDetails": "true"},
            timeout=10
        )
        if r.status_code != 200:
            return None, "마켓 목록 조회 실패"

        all_markets = r.json()
        krw_markets = [
            x for x in all_markets
            if x["market"].startswith("KRW-")
            and x["market"].replace("KRW-", "") not in _AS_STABLECOINS
            and x.get("market_warning") != "CAUTION"
        ]
        market_codes = [x["market"] for x in krw_markets]

        # 2) 시세 일괄 조회 (100개씩 나눠서)
        tickers = []
        for i in range(0, len(market_codes), 100):
            chunk = market_codes[i:i+100]
            r2 = _as_req.get(
                "https://api.upbit.com/v1/ticker",
                params={"markets": ",".join(chunk)},
                timeout=10
            )
            if r2.status_code == 200:
                tickers.extend(r2.json())
            import time as _t; _t.sleep(0.2)

        # 3) 필터링 + 정렬
        filtered = [
            t for t in tickers
            if float(t.get("trade_price", 0)) >= min_price
            and float(t.get("acc_trade_price_24h", 0)) >= min_volume_krw
        ]
        filtered.sort(key=lambda x: float(x.get("acc_trade_price_24h", 0)), reverse=True)

        top = [t["market"] for t in filtered[:count]]
        return top, None

    except Exception as e:
        return None, str(e)


def _autoselect_apply(markets, notify=True):
    """선별된 종목 목록을 현재 coins 설정에 반영"""
    global _workers

    if not markets:
        return

    coins     = _cfg.get("coins", [])
    current   = {c["market"] for c in coins}
    new_set   = set(markets)

    to_add    = new_set - current
    to_remove = current - new_set
    kept      = current & new_set

    ratio = round(1.0 / len(markets), 4)

    lines = [
        f"🔄 자동 선별 결과 ({len(markets)}종목)",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"유지: {len(kept)}개  추가: {len(to_add)}개  제거: {len(to_remove)}개",
        f"종목당 예산: {ratio*100:.1f}% = {calc_budget(ratio):,}원",
        f"─────────────────",
    ]

    # 제거
    for mkt in to_remove:
        _coin_remove(mkt)
        lines.append(f"➖ {mkt}")

    # 비율 업데이트 (기존 종목)
    for c in _cfg.get("coins", []):
        if c["market"] in new_set:
            c["budget_ratio"] = ratio
    _save_cfg()

    # 추가
    for mkt in sorted(to_add):
        coins2 = _cfg.get("coins", [])
        coins2.append({"market": mkt, "budget_ratio": ratio, "enabled": True})
        _cfg["coins"] = coins2
        _save_cfg()
        new_w = CoinWorker(market=mkt, budget_ratio=ratio)
        new_w.start()
        with _workers_lock:
            _workers.append(new_w)
        lines.append(f"➕ {mkt}")

    if notify:
        send_msg("\n".join(lines), level="critical", source="매니저", force=True)


def _autoselect_run(notify=True):
    """자동 선별 즉시 실행"""
    global _autoselect_last_run
    cfg_as  = _cfg.get("autoselect", {})
    count   = int(cfg_as.get("count", 20))
    min_vol = int(cfg_as.get("min_volume_krw", 5_000_000_000))
    min_prc = int(cfg_as.get("min_price", 100))

    send_msg(
        f"🔍 자동 선별 중...\n상위 {count}개 / 최소거래대금 {min_vol//100_000_000}억 / 최소가격 {min_prc}원",
        level="normal", source="매니저", force=True
    )

    markets, err = _autoselect_fetch_top(count, min_vol, min_prc)
    if err:
        send_msg(f"❌ 자동 선별 실패: {err}", level="critical", source="매니저", force=True)
        return

    _autoselect_apply(markets, notify=notify)
    _autoselect_last_run = __import__("time").time()


def _autoselect_loop():
    """매일 08:55 자동 실행"""
    import time as _t
    from datetime import datetime as _dt
    while True:
        try:
            cfg_as = _cfg.get("autoselect", {})
            if cfg_as.get("enabled"):
                now = _dt.now()
                if now.hour == 8 and now.minute == 55:
                    _autoselect_run(notify=True)
                    _t.sleep(70)   # 중복 실행 방지
        except Exception:
            pass
        _t.sleep(30)

'''

# /autoselect 명령 핸들러
AUTOSELECT_CMD = r'''
    # ── /autoselect ──────────────────────────────────────────
    elif cmd[0] == "/autoselect":
        sub = cmd[1].lower() if len(cmd) > 1 else "status"

        if sub == "on":
            count = int(cmd[2]) if len(cmd) > 2 else 20
            if not _cfg.get("autoselect"):
                _cfg["autoselect"] = {}
            _cfg["autoselect"]["enabled"]        = True
            _cfg["autoselect"]["count"]          = count
            _cfg["autoselect"].setdefault("min_volume_krw", 5_000_000_000)
            _cfg["autoselect"].setdefault("min_price",      100)
            _save_cfg()
            send_msg(
                f"✅ 자동 선별 ON\n"
                f"상위 {count}개 / 매일 08:55 자동 갱신\n"
                f"→ /autoselect now 로 즉시 실행",
                level="critical", source="매니저", force=True
            )

        elif sub == "off":
            if _cfg.get("autoselect"):
                _cfg["autoselect"]["enabled"] = False
                _save_cfg()
            send_msg("✅ 자동 선별 OFF", level="normal", source="매니저", force=True)

        elif sub == "now":
            threading.Thread(target=_autoselect_run, daemon=True).start()

        elif sub == "status":
            cfg_as = _cfg.get("autoselect", {})
            enabled = cfg_as.get("enabled", False)
            count   = cfg_as.get("count", 20)
            min_vol = cfg_as.get("min_volume_krw", 5_000_000_000)
            min_prc = cfg_as.get("min_price", 100)
            coins   = _cfg.get("coins", [])
            send_msg(
                f"🔍 자동 선별 설정\n"
                f"상태: {'🟢 ON' if enabled else '🔴 OFF'}\n"
                f"상위: {count}개\n"
                f"최소 거래대금: {min_vol//100_000_000}억원\n"
                f"최소 가격: {min_prc}원\n"
                f"현재 종목: {len(coins)}개\n"
                f"─────────────────\n"
                f"/autoselect on 20  — 켜기 (상위 20개)\n"
                f"/autoselect off    — 끄기\n"
                f"/autoselect now    — 즉시 실행",
                level="normal", source="매니저", force=True
            )
        else:
            send_msg(
                "사용법:\n"
                "/autoselect on 20  — 상위 20개 자동 선별\n"
                "/autoselect off    — 끄기\n"
                "/autoselect now    — 즉시 실행\n"
                "/autoselect status — 설정 확인",
                level="normal", source="매니저", force=True
            )

'''

# autoselect 루프 스레드 시작 (ticker_feed 스레드 시작 코드 뒤에 삽입)
AUTOSELECT_THREAD = (
    "    threading.Thread(target=_autoselect_loop, daemon=True, name=\"autoselect\").start()\n"
    "    cprint(\"✅ [autoselect] 종목 자동 선별 루프 시작\", Fore.CYAN)\n"
)

# ============================================================
# PATCH
# ============================================================
def patch_manager(src):
    # 1) autoselect 함수 삽입 (run_manager 직전)
    anchor1 = "def run_manager():"
    if "_autoselect_loop" in src:
        print("  ⏭ autoselect 이미 존재")
    elif anchor1 in src:
        src = src.replace(anchor1, AUTOSELECT_CODE + anchor1)
        print("  ✅ autoselect 함수 삽입")
    else:
        print("  ❌ run_manager() 못 찾음"); return src

    # 2) autoselect 스레드 시작 — ticker-feed 시작 라인 바로 뒤
    if "autoselect" in src and 'name="autoselect"' in src:
        print("  ⏭ autoselect 스레드 이미 존재")
    else:
        lines = src.split("\n")
        inserted = False
        for i, line in enumerate(lines):
            if "ticker-feed" in line and "Thread" in line:
                lines.insert(i + 1, AUTOSELECT_THREAD)
                inserted = True
                print(f"  ✅ autoselect 스레드 삽입 (라인 {i+1})")
                break
        if inserted:
            src = "\n".join(lines)
        else:
            print("  ❌ ticker-feed 라인 못 찾음")

    # 3) /autoselect 명령 핸들러 삽입 (/budget 핸들러 앞에)
    anchor3 = '    elif cmd[0] == "/budget":'
    if '"/autoselect"' in src:
        print("  ⏭ /autoselect 핸들러 이미 존재")
    elif anchor3 in src:
        src = src.replace(anchor3, AUTOSELECT_CMD + anchor3)
        print("  ✅ /autoselect 핸들러 삽입")
    else:
        print("  ❌ /budget 핸들러 못 찾음")

    return src

# ============================================================
# MAIN
# ============================================================
print("=" * 50)
print("patch_autoselect.py")
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

print("\n" + "=" * 50)
print("완료! 적용:")
print("  git add manager.py patch_autoselect.py")
print("  git commit -m 'patch: autoselect top markets'")
print("  git push && 텔레그램 /update")
print()
print("사용:")
print("  /autoselect on 20   — 상위 20개 자동 선별 켜기")
print("  /autoselect now     — 즉시 실행")
print("=" * 50)
