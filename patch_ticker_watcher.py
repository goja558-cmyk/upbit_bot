import re, sys, os, py_compile, shutil

BASE = "/home/trade/upbit_bot"
MGR  = os.path.join(BASE, "manager.py")
BOT  = os.path.join(BASE, "upbit_bot.py")

shutil.copy(MGR, MGR + ".bak")
shutil.copy(BOT, BOT + ".bak")
print("백업 완료")

with open(MGR, encoding="utf-8") as f:
    src = f.read()

# [1-1] MAX_SLOTS 전역변수
OLD1 = "# 알림 설정\nSUMMARY_INTERVAL"
NEW1 = """# ── 티커 워처 슬롯 관리 ──────────────────────────────────────
MIN_TRADE_KRW   = 20_000
MAX_SLOTS       = 1
_active_slots   = {}
_slots_lock     = threading.Lock()
_watcher_started = False

# 알림 설정
SUMMARY_INTERVAL"""
if OLD1 in src:
    src = src.replace(OLD1, NEW1, 1); print("[1-1] OK")
else:
    print("[1-1] FAIL"); sys.exit(1)

# [1-2] load_config 끝
OLD2 = '    cprint(f"✅ 매니저 설정 로드 완료 (예산:{TOTAL_BUDGET:,}원)", Fore.GREEN)'
NEW2 = '''    global MAX_SLOTS
    MAX_SLOTS = max(1, TOTAL_BUDGET // MIN_TRADE_KRW)
    cprint(f"✅ 매니저 설정 로드 완료 (예산:{TOTAL_BUDGET:,}원 / 슬롯:{MAX_SLOTS}개)", Fore.GREEN)'''
if OLD2 in src:
    src = src.replace(OLD2, NEW2, 1); print("[1-2] OK")
else:
    print("[1-2] FAIL"); sys.exit(1)

# [1-3] TickerWatcher 코드
TICKER_CODE = '''
# ============================================================
# [6-B] 티커 워처
# ============================================================
_TICKER_BLACKLIST = {"KRW-USDT","KRW-USDC","KRW-DAI","KRW-BUSD","KRW-BTC"}
_WATCH_COUNT = 30
_ticker_lock = threading.Lock()
_ticker_cache_ts = 0.0

def _fetch_top_markets(n=30):
    try:
        res = requests.get("https://api.upbit.com/v1/market/all?isDetails=false", timeout=5)
        if res.status_code != 200: return []
        krw = [m["market"] for m in res.json()
               if m["market"].startswith("KRW-") and m["market"] not in _TICKER_BLACKLIST]
        r2 = requests.get("https://api.upbit.com/v1/ticker",
                          params={"markets": ",".join(krw[:100])}, timeout=5)
        if r2.status_code != 200: return krw[:n]
        tickers = r2.json()
        tickers.sort(key=lambda x: x.get("acc_trade_price_24h", 0), reverse=True)
        return [t["market"] for t in tickers if t.get("trade_price", 0) >= 100][:n]
    except Exception as e:
        cprint(f"[TickerWatcher] 마켓 조회 오류: {e}", Fore.YELLOW)
        return []

def _quick_signal(td) -> bool:
    change = td.get("signed_change_rate", 0) * 100
    high = td.get("high_price", 0)
    low  = td.get("low_price", 0)
    price = td.get("trade_price", 0)
    if price <= 0 or high <= 0: return False
    vol_pct = (high - low) / high * 100 if high > 0 else 0
    return (-8.0 <= change <= -1.0) and (vol_pct >= 1.0)

def _slot_count():
    with _slots_lock: return len(_active_slots)

def _slot_add(market, worker):
    with _slots_lock: _active_slots[market] = worker

def _slot_remove(market):
    with _slots_lock: _active_slots.pop(market, None)

def _slot_has(market):
    with _slots_lock: return market in _active_slots

def _handle_slot_request(market):
    mkt = market.replace("KRW-", "").lower()
    res_file = os.path.join(SHARED_DIR, f"slot_res_{mkt}.json")
    try:
        with _slots_lock:
            granted = len(_active_slots) < MAX_SLOTS or market in _active_slots
        tmp = res_file + ".tmp"
        with open(tmp, "w") as f:
            import json as _j
            _j.dump({"granted": granted, "ts": time.time()}, f)
        os.replace(tmp, res_file)
        os.chmod(res_file, 0o664)
    except Exception as e:
        cprint(f"[슬롯 응답 오류] {e}", Fore.YELLOW)

def _poll_slot_requests():
    try:
        for fname in os.listdir(SHARED_DIR):
            if not fname.startswith("slot_req_"): continue
            req_file = os.path.join(SHARED_DIR, fname)
            try:
                tmp = req_file + ".read"
                os.rename(req_file, tmp)
                with open(tmp) as f:
                    import json as _j; data = _j.load(f)
                os.remove(tmp)
                market = data.get("market", "")
                if market: _handle_slot_request(market)
            except Exception: pass
    except Exception: pass

def _poll_slot_release():
    global _workers
    try:
        for fname in os.listdir(SHARED_DIR):
            if not fname.startswith("slot_release_"): continue
            rel_file = os.path.join(SHARED_DIR, fname)
            try:
                tmp = rel_file + ".read"
                os.rename(rel_file, tmp)
                with open(tmp) as f:
                    import json as _j; data = _j.load(f)
                os.remove(tmp)
                market = data.get("market", "")
                if market and _slot_has(market):
                    _slot_remove(market)
                    with _workers_lock:
                        to_stop = [w for w in _workers
                                   if isinstance(w, CoinWorker) and w.market == market]
                        for w in to_stop: w.stop()
                        _workers = [w for w in _workers if w not in to_stop]
                    cprint(f"[슬롯] {market} 반납 + 워커 종료", Fore.CYAN)
            except Exception: pass
    except Exception: pass

class TickerWatcher:
    def __init__(self):
        self.thread = None
        self._stop  = threading.Event()

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True, name="ticker-watcher")
        self.thread.start()
        cprint(f"✅ TickerWatcher 시작 (슬롯 최대 {MAX_SLOTS}개)", Fore.GREEN)

    def stop(self):
        self._stop.set()

    def _run(self):
        markets = []
        markets_ts = 0.0
        while not self._stop.is_set():
            try:
                now = time.time()
                if now - markets_ts >= 60:
                    markets = _fetch_top_markets(_WATCH_COUNT)
                    markets_ts = now
                    cprint(f"[TickerWatcher] 감시 종목 {len(markets)}개 갱신", Fore.CYAN)
                if not markets:
                    time.sleep(10); continue
                res = requests.get("https://api.upbit.com/v1/ticker",
                                   params={"markets": ",".join(markets)}, timeout=5)
                if res.status_code != 200:
                    time.sleep(10); continue
                tickers = {t["market"]: t for t in res.json()}
                for market, td in tickers.items():
                    if _slot_has(market): continue
                    if _slot_count() >= MAX_SLOTS: break
                    if not _quick_signal(td): continue
                    per_slot = max(20_000, TOTAL_BUDGET // max(MAX_SLOTS, 1))
                    ratio = per_slot / TOTAL_BUDGET if TOTAL_BUDGET > 0 else 0.5
                    cprint(f"[TickerWatcher] 신호 → {market} 워커 생성 (예산:{per_slot:,}원)", Fore.GREEN)
                    new_w = CoinWorker(market=market, budget_ratio=ratio)
                    new_w.start()
                    with _workers_lock:
                        _workers.append(new_w)
                    _slot_add(market, new_w)
                    send_msg(
                        f"🔍 신규 종목 진입 시도\\n종목: {market}\\n예산: {per_slot:,}원\\n슬롯: {_slot_count()}/{MAX_SLOTS}",
                        level="normal", source="워처"
                    )
            except Exception as e:
                cprint(f"[TickerWatcher] 오류: {e}", Fore.YELLOW)
            time.sleep(10)

'''

OLD3 = "class StockWorker:"
if OLD3 in src:
    src = src.replace(OLD3, TICKER_CODE + OLD3, 1); print("[1-3] OK")
else:
    print("[1-3] FAIL"); sys.exit(1)

# [1-4] TickerWatcher 시작
OLD4 = "    init_mgr_pinned_message()"
NEW4 = """    global _watcher_started
    if not _watcher_started:
        _tw = TickerWatcher()
        _tw.start()
        _watcher_started = True

    init_mgr_pinned_message()"""
if OLD4 in src:
    src = src.replace(OLD4, NEW4, 1); print("[1-4] OK")
else:
    print("[1-4] FAIL"); sys.exit(1)

# [1-5] 메인 루프 슬롯 폴링
OLD5 = "            _poll_ipc_results()\n            _check_bot_health()"
NEW5 = "            _poll_ipc_results()\n            _check_bot_health()\n            _poll_slot_requests()\n            _poll_slot_release()"
if OLD5 in src:
    src = src.replace(OLD5, NEW5, 1); print("[1-5] OK")
else:
    print("[1-5] FAIL"); sys.exit(1)

with open(MGR, "w", encoding="utf-8") as f:
    f.write(src)
print("manager.py 저장")

# ============================================================
# upbit_bot.py 패치
# ============================================================
with open(BOT, encoding="utf-8") as f:
    bsrc = f.read()

SLOT_FUNCS = '''
def _request_slot() -> bool:
    mkt = MARKET_CODE.replace("KRW-", "").lower()
    req_file = os.path.join(SHARED_DIR, f"slot_req_{mkt}.json")
    res_file = os.path.join(SHARED_DIR, f"slot_res_{mkt}.json")
    try:
        if os.path.exists(res_file): os.remove(res_file)
        tmp = req_file + ".tmp"
        with open(tmp, "w") as f:
            import json as _j
            _j.dump({"market": MARKET_CODE, "ts": time.time()}, f)
        os.replace(tmp, req_file)
        os.chmod(req_file, 0o664)
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if os.path.exists(res_file):
                with open(res_file) as f:
                    import json as _j; data = _j.load(f)
                os.remove(res_file)
                return bool(data.get("granted", False))
            time.sleep(0.1)
        return not _is_manager_running()
    except Exception as e:
        cprint(f"[슬롯 요청 오류] {e}", Fore.YELLOW)
        return True

def _release_slot():
    if not _is_manager_running(): return
    mkt = MARKET_CODE.replace("KRW-", "").lower()
    rel_file = os.path.join(SHARED_DIR, f"slot_release_{mkt}.json")
    try:
        tmp = rel_file + ".tmp"
        with open(tmp, "w") as f:
            import json as _j
            _j.dump({"market": MARKET_CODE, "ts": time.time()}, f)
        os.replace(tmp, rel_file)
        os.chmod(rel_file, 0o664)
        cprint(f"[슬롯] {MARKET_CODE} 반납 신호 전송", Fore.CYAN)
    except Exception as e:
        cprint(f"[슬롯 반납 오류] {e}", Fore.YELLOW)

'''

OLD_B1 = "def fetch_coin_stats(market):"
if OLD_B1 in bsrc:
    bsrc = bsrc.replace(OLD_B1, SLOT_FUNCS + OLD_B1, 1); print("[2-1] OK")
else:
    print("[2-1] FAIL"); sys.exit(1)

OLD_B2 = """    if _order_pending:
        cprint("[중복 주문 방지] 이미 주문 진행 중입니다.", Fore.YELLOW)
        return False
    if bot["has_stock"]:
        cprint("[중복 주문 방지] 이미 보유 중입니다.", Fore.YELLOW)
        return False
    _order_pending = True"""
NEW_B2 = """    if _order_pending:
        cprint("[중복 주문 방지] 이미 주문 진행 중입니다.", Fore.YELLOW)
        return False
    if bot["has_stock"]:
        cprint("[중복 주문 방지] 이미 보유 중입니다.", Fore.YELLOW)
        return False
    if _is_manager_running() and not _request_slot():
        cprint(f"[슬롯] {MARKET_CODE} 슬롯 거절 — 매수 건너뜀", Fore.YELLOW)
        return False
    _order_pending = True"""
if OLD_B2 in bsrc:
    bsrc = bsrc.replace(OLD_B2, NEW_B2, 1); print("[2-2] OK")
else:
    print("[2-2] FAIL"); sys.exit(1)

OLD_B3 = '            log_trade("SELL"'
NEW_B3 = '            _release_slot()\n            log_trade("SELL"'
if OLD_B3 in bsrc:
    bsrc = bsrc.replace(OLD_B3, NEW_B3, 1); print("[2-3] OK")
else:
    print("[2-3] FAIL"); sys.exit(1)

with open(BOT, "w", encoding="utf-8") as f:
    f.write(bsrc)
print("upbit_bot.py 저장")

print("\n문법 검증...")
ok = True
for fpath in [MGR, BOT]:
    try:
        py_compile.compile(fpath, doraise=True)
        print(f"  ✅ {os.path.basename(fpath)}")
    except py_compile.PyCompileError as e:
        print(f"  ❌ {os.path.basename(fpath)}: {e}")
        shutil.copy(fpath + ".bak", fpath)
        print(f"  → 백업 복원")
        ok = False
if ok:
    print("\n✅ 패치 완료! git push 후 /update 하세요.")
