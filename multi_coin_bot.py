"""
==============================================================
  멀티코인 자동매매 봇 v1.0
  - 프로세스 1개로 30개 종목 감시
  - 슬롯 수 = 예산 ÷ 20,000 (자동 계산)
  - 신호 강도 순으로 슬롯 할당 → 매수
  - 종목별 독립 포지션 관리
  - 기존 upbit_bot.py 매매 로직 (RSI V-Turn, 트레일링 등) 그대로 사용

  upbit_cfg.yaml 동일하게 사용
==============================================================
"""

BOT_VERSION = "1.0"
BOT_TAG     = "🪙 멀티코인"

import sys, os, time, json, csv, requests, yaml, shutil, traceback, threading
import numpy as np
from datetime import datetime, date
from collections import deque
from urllib.parse import urlencode

try:
    import jwt as pyjwt
    JWT_OK = True
except ImportError:
    JWT_OK = False

try:
    from colorama import init as colorama_init, Fore, Style
    colorama_init(autoreset=True)
    COLOR_OK = True
except ImportError:
    COLOR_OK = False
    class Fore:
        GREEN = YELLOW = RED = CYAN = MAGENTA = WHITE = RESET = ""
    class Style:
        BRIGHT = RESET_ALL = DIM = ""

def cprint(text, color="", bright=False, **_):
    ts = datetime.now().strftime("%H:%M:%S")
    prefix = (Style.BRIGHT if bright else "") + color if COLOR_OK else ""
    print(f"{prefix}[{ts}] {text}{Style.RESET_ALL if COLOR_OK else ''}")

# ============================================================
# [1] 경로 / 상수
# ============================================================
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
SHARED_DIR = os.path.join(BASE_DIR, "shared")
os.makedirs(SHARED_DIR, exist_ok=True)

import argparse as _ap
_p = _ap.ArgumentParser(add_help=False)
_p.add_argument("--config", default=None)
_args, _ = _p.parse_known_args()
CFG_FILE = os.path.abspath(_args.config) if _args.config else os.path.join(BASE_DIR, "upbit_cfg.yaml")

UPBIT_BASE    = "https://api.upbit.com/v1"
FEE_RATE      = 0.0005
MIN_ORDER_KRW = 5_000
MIN_TRADE_KRW = 20_000   # 슬롯당 최소 예산
LOOP_INTERVAL = 5
WATCH_COUNT   = 30
WATCH_INTERVAL = 10      # 시세 일괄 조회 주기 (초)
REAL_DATA_MIN  = 60      # 매수 허용 최소 데이터 수
HISTORY_LEN    = 300
COOLDOWN_SEC   = 120

# 스테이블/저변동 블랙리스트
BLACKLIST = {"KRW-USDT","KRW-USDC","KRW-DAI","KRW-BUSD","KRW-BTC"}

# ============================================================
# [2] 설정
# ============================================================
_cfg           = {}
TELEGRAM_TOKEN = ""
CHAT_ID        = ""
ACCESS_KEY     = ""
SECRET_KEY     = ""
TOTAL_BUDGET   = 50_000
MAX_SLOTS      = 2

def load_config():
    global _cfg, TELEGRAM_TOKEN, CHAT_ID, ACCESS_KEY, SECRET_KEY
    global TOTAL_BUDGET, MAX_SLOTS
    if not os.path.exists(CFG_FILE):
        print(f"❌ {CFG_FILE} 없음"); sys.exit(1)
    with open(CFG_FILE, encoding="utf-8") as f:
        _cfg = yaml.safe_load(f) or {}
    TELEGRAM_TOKEN = _cfg.get("telegram_token", "")
    CHAT_ID        = str(_cfg.get("chat_id", ""))
    ACCESS_KEY     = _cfg.get("access_key", "")
    SECRET_KEY     = _cfg.get("secret_key", "")
    # manager_cfg.yaml의 total_budget 우선, 없으면 upbit_cfg.yaml의 budget
    mgr_cfg_file = os.path.join(os.path.dirname(CFG_FILE), "manager_cfg.yaml")
    mgr_budget = 0
    if os.path.exists(mgr_cfg_file):
        import yaml as _yaml
        with open(mgr_cfg_file, encoding="utf-8") as _f:
            _mcfg = _yaml.safe_load(_f) or {}
        mgr_budget = int(_mcfg.get("total_budget", 0))
    TOTAL_BUDGET   = mgr_budget or int(_cfg.get("budget", 50_000))
    MAX_SLOTS      = max(1, TOTAL_BUDGET // MIN_TRADE_KRW)
    cprint(f"✅ 설정 로드 (예산:{TOTAL_BUDGET:,}원 / 슬롯:{MAX_SLOTS}개)", Fore.GREEN)

# ============================================================
# [3] 업비트 API
# ============================================================
def _upbit_headers(body=None):
    import uuid, hmac, hashlib
    payload = {"access_key": ACCESS_KEY, "nonce": str(uuid.uuid4())}
    if body:
        import urllib.parse
        query = urllib.parse.urlencode(body)
        m = hashlib.sha512(); m.update(query.encode())
        payload["query_hash"] = m.hexdigest()
        payload["query_hash_alg"] = "SHA512"
    token = pyjwt.encode(payload, SECRET_KEY, algorithm="HS256")
    if isinstance(token, bytes): token = token.decode()
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

def get_price_and_volume(market):
    try:
        r = requests.get(f"{UPBIT_BASE}/ticker", params={"markets": market}, timeout=5)
        if r.status_code == 200:
            d = r.json()[0]
            return d.get("trade_price", 0), d.get("acc_trade_volume_24h", 0)
    except Exception as e:
        cprint(f"[API] {market} 시세 오류: {e}", Fore.YELLOW)
    return 0, 0

def get_balance_krw():
    try:
        h = _upbit_headers()
        r = requests.get(f"{UPBIT_BASE}/accounts", headers=h, timeout=5)
        if r.status_code == 200:
            for a in r.json():
                if a.get("currency") == "KRW":
                    return float(a.get("balance", 0))
    except Exception as e:
        cprint(f"[잔고 조회 오류] {e}", Fore.YELLOW)
    return 0

def send_order(side, market, qty_or_price):
    try:
        if side == "BUY":
            body = {"market": market, "side": "bid",
                    "price": str(int(qty_or_price)), "ord_type": "price"}
        else:
            body = {"market": market, "side": "ask",
                    "volume": str(qty_or_price), "ord_type": "market"}
        h = _upbit_headers(body)
        r = requests.post(f"{UPBIT_BASE}/orders", headers=h, json=body, timeout=10)
        if r.status_code in (200, 201):
            return confirm_order(r.json().get("uuid", ""))
    except Exception as e:
        cprint(f"[주문 오류] {e}", Fore.RED)
    return 0, 0

def confirm_order(uuid, retry=8):
    for _ in range(retry):
        try:
            h = _upbit_headers()
            r = requests.get(f"{UPBIT_BASE}/order", headers=h,
                             params={"uuid": uuid}, timeout=5)
            if r.status_code == 200:
                d = r.json()
                if d.get("state") in ("done", "cancel"):
                    filled = float(d.get("executed_volume", 0))
                    funds  = float(d.get("executed_funds", 0))
                    avg_p  = funds / filled if filled > 0 else 0
                    return filled, avg_p
        except Exception:
            pass
        time.sleep(0.5)
    return 0, 0

def get_ohlcv(market, count=200, interval=1):
    try:
        r = requests.get(
            f"{UPBIT_BASE}/candles/minutes/{interval}",
            params={"market": market, "count": count}, timeout=5
        )
        if r.status_code == 200:
            return [c["trade_price"] for c in reversed(r.json())]
    except Exception as e:
        cprint(f"[OHLCV 오류] {market}: {e}", Fore.YELLOW)
    return []

# ============================================================
# [4] 지표 계산
# ============================================================
def calc_rsi(prices, period=14):
    arr = list(prices)
    if len(arr) < period + 1: return None
    deltas = [arr[i] - arr[i-1] for i in range(1, len(arr))]
    gains  = [max(d, 0) for d in deltas]
    losses = [abs(min(d, 0)) for d in deltas]
    avg_g = sum(gains[-period:]) / period
    avg_l = sum(losses[-period:]) / period
    for i in range(period, len(gains)):
        avg_g = (avg_g * (period-1) + gains[i]) / period
        avg_l = (avg_l * (period-1) + losses[i]) / period
    if avg_l == 0: return 100.0
    rs = avg_g / avg_l
    return round(100 - 100 / (1 + rs), 2)

def calc_ma(prices, n):
    arr = list(prices)
    if len(arr) < n: return None
    return sum(arr[-n:]) / n

def calc_vol_pct(timed_prices):
    now = time.time()
    recent = [p for t, p in timed_prices if now - t <= 300]
    if len(recent) < 2: return None
    return (max(recent) - min(recent)) / min(recent) * 100

# ============================================================
# [5] 종목별 상태 관리
# ============================================================
# coins[market] = {
#   "history": deque, "vol_history": deque, "timed": deque,
#   "has_stock": bool, "buy_price": float, "filled_qty": float,
#   "be_active": bool, "prev_rsi": None, "prev_rsi2": None,
#   "target": float, "max_loss": float, "drop": float,
#   "trail_start": float, "trail_gap": float, "be_trigger": float,
#   "rsi_buy": int, "vol_min": float, "vol_max": float,
#   "cooldown": float (last_sell_time), "buy_time": float,
#   "highest_profit": float, "trade_count": int,
#   "daily_pnl": float, "real_data_count": int,
# }
coins       = {}
coins_lock  = threading.Lock()

# 슬롯: 현재 보유 중인 종목 집합
slots       = set()
slots_lock  = threading.Lock()

# 전체 일간/주간 손익
daily_pnl   = 0.0
weekly_pnl  = 0.0
_last_reset_day  = date.today()

# 파라미터 프로파일 (기존 upbit_bot.py와 동일)
COIN_PROFILES = {
    "KRW-XRP":  dict(target=1.2, max_loss=-0.9, drop=0.6, trail_start=0.6, trail_gap=0.35, be_trigger=0.4, rsi_buy=38, vol_min=0.4, vol_max=6.0, cooldown=90),
    "KRW-ETH":  dict(target=1.0, max_loss=-0.7, drop=0.5, trail_start=0.5, trail_gap=0.3,  be_trigger=0.35, rsi_buy=38, vol_min=0.3, vol_max=5.0, cooldown=90),
    "KRW-SOL":  dict(target=1.3, max_loss=-1.0, drop=0.7, trail_start=0.7, trail_gap=0.4,  be_trigger=0.4,  rsi_buy=38, vol_min=0.5, vol_max=7.0, cooldown=120),
    "KRW-DOGE": dict(target=1.2, max_loss=-0.9, drop=0.6, trail_start=0.6, trail_gap=0.35, be_trigger=0.4,  rsi_buy=40, vol_min=0.4, vol_max=7.0, cooldown=90),
}
PROFILE_DEFAULT = dict(target=1.0, max_loss=-0.8, drop=0.5, trail_start=0.5, trail_gap=0.3, be_trigger=0.35, rsi_buy=38, vol_min=0.3, vol_max=6.0, cooldown=120)

def _make_coin_state(market):
    p = {**PROFILE_DEFAULT, **COIN_PROFILES.get(market, {})}
    return {
        "history":        deque(maxlen=HISTORY_LEN),
        "vol_history":    deque(maxlen=HISTORY_LEN),
        "timed":          deque(maxlen=3600),
        "has_stock":      False,
        "buy_price":      0.0,
        "filled_qty":     0.0,
        "be_active":      False,
        "prev_rsi":       None,
        "prev_rsi2":      None,
        "highest_profit": 0.0,
        "buy_time":       0.0,
        "last_sell_time": 0.0,
        "trade_count":    0,
        "daily_pnl":      0.0,
        "real_data_count": 0,
        **p,
    }

def get_or_create_coin(market):
    with coins_lock:
        if market not in coins:
            coins[market] = _make_coin_state(market)
        return coins[market]

# ============================================================
# [6] 텔레그램
# ============================================================
_tg_lock = threading.Lock()

def send_msg(text, market=None, level="normal"):
    tag = f"[{market}] " if market else ""
    full = f"{BOT_TAG} {tag}\n{text}"
    try:
        with _tg_lock:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id": CHAT_ID, "text": full[:4000]},
                timeout=5
            )
    except Exception as e:
        cprint(f"[텔레그램 오류] {e}", Fore.YELLOW)

# IPC
_MANAGER_PID_FILE = os.path.join(SHARED_DIR, "manager.pid")
_IPC_CMD_FILE     = os.path.join(SHARED_DIR, "cmd_multicoin.json")
_IPC_RES_FILE     = os.path.join(SHARED_DIR, "result_multicoin.json")
_IPC_REQ_ID       = ""

def _is_manager_running():
    try:
        if not os.path.exists(_MANAGER_PID_FILE): return False
        with open(_MANAGER_PID_FILE) as f:
            pid = int(f.read().strip())
        return os.path.exists(f"/proc/{pid}")
    except Exception:
        return False

def _write_ipc_result(text, req_id=""):
    try:
        rid = req_id or _IPC_REQ_ID
        fname = f"result_multicoin{'_'+rid if rid else ''}.json"
        path  = os.path.join(SHARED_DIR, fname)
        tmp   = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"result": text, "req_id": rid, "ts": time.time()}, f)
        os.replace(tmp, path)
        os.chmod(path, 0o664)
    except Exception as e:
        cprint(f"[IPC 결과 오류] {e}", Fore.YELLOW)

# ============================================================
# [7] 매수 / 매도
# ============================================================
_order_lock = threading.Lock()

def do_buy(market, price, reason):
    global daily_pnl
    c = get_or_create_coin(market)

    with _order_lock:
        if c["has_stock"]: return False

    # 슬롯 체크
    with slots_lock:
        if market not in slots and len(slots) >= MAX_SLOTS:
            cprint(f"[슬롯 부족] {market} 매수 불가 ({len(slots)}/{MAX_SLOTS})", Fore.YELLOW)
            return False

    per_slot = max(MIN_TRADE_KRW, TOTAL_BUDGET // max(MAX_SLOTS, 1))
    balance  = get_balance_krw()
    order_krw = int(min(per_slot, balance) * 0.98)
    if order_krw < MIN_ORDER_KRW:
        cprint(f"[잔고 부족] {market} 잔고:{balance:,.0f}원", Fore.YELLOW)
        return False

    filled, avg_p = send_order("BUY", market, order_krw)
    if filled <= 0 or avg_p <= 0:
        return False

    c.update({
        "has_stock": True, "buy_price": avg_p,
        "filled_qty": filled, "be_active": False,
        "highest_profit": 0.0, "buy_time": time.time(),
    })
    with slots_lock:
        slots.add(market)

    target_p = avg_p * (1 + c["target"] / 100)
    stop_p   = avg_p * (1 + c["max_loss"] / 100)
    send_msg(
        f"🛒 매수 완료!\n"
        f"매수가: {avg_p:,.2f}원  수량: {filled:.6f}\n"
        f"목표가: {target_p:,.2f}원  손절가: {stop_p:,.2f}원\n"
        f"이유: {reason}", market=market, level="critical"
    )
    _write_status()
    return True

def do_sell(market, price, reason):
    global daily_pnl
    c = get_or_create_coin(market)
    if not c["has_stock"]: return False

    filled, avg_p = send_order("SELL", market, c["filled_qty"])
    if filled <= 0:
        send_msg(f"🚨 매도 실패! 직접 매도하세요.\n이유: {reason}", market=market, level="critical")
        return False

    actual = avg_p if avg_p > 0 else price
    fee    = (c["buy_price"] * c["filled_qty"] + actual * filled) * FEE_RATE
    pnl    = (actual - c["buy_price"]) * filled - fee

    daily_pnl       += pnl
    c["daily_pnl"]  += pnl
    c["trade_count"] += 1
    c["last_sell_time"] = time.time()
    c.update({"has_stock": False, "buy_price": 0.0, "filled_qty": 0.0,
               "be_active": False, "highest_profit": 0.0, "buy_time": 0.0})

    with slots_lock:
        slots.discard(market)

    send_msg(
        f"{'🟢 익절' if pnl >= 0 else '🔴 손절'} 매도 완료!\n"
        f"매도가: {actual:,.2f}원\n"
        f"이번 손익: {pnl:+,.0f}원\n"
        f"이유: {reason}", market=market, level="critical"
    )
    _write_status()
    return True

# ============================================================
# [8] 매도 조건 체크 (보유 중 루프)
# ============================================================
def check_sell(market, price):
    c = coins.get(market)
    if not c or not c["has_stock"]: return

    buy_p  = c["buy_price"]
    qty    = c["filled_qty"]
    pnl_pct = (price - buy_p) / buy_p * 100

    # 트레일링 스탑
    if pnl_pct > c["highest_profit"]:
        c["highest_profit"] = pnl_pct
    trail_trigger = c["highest_profit"] >= c["trail_start"]
    trail_stop    = c["highest_profit"] - c["trail_gap"]

    # 본절 보호
    if pnl_pct >= c["be_trigger"]:
        c["be_active"] = True
    be_stop = pnl_pct <= 0 and c["be_active"]

    # 익절
    if pnl_pct >= c["target"]:
        do_sell(market, price, f"익절 {pnl_pct:+.2f}%")
    # 트레일링
    elif trail_trigger and pnl_pct <= trail_stop:
        do_sell(market, price, f"트레일링 {pnl_pct:+.2f}% (고점:{c['highest_profit']:.2f}%)")
    # 본절 보호
    elif be_stop:
        do_sell(market, price, f"본절 보호 {pnl_pct:+.2f}%")
    # 손절
    elif pnl_pct <= c["max_loss"]:
        do_sell(market, price, f"손절 {pnl_pct:+.2f}%")

# ============================================================
# [9] 매수 신호 체크
# ============================================================
def check_buy_signal(market, price, volume):
    c = get_or_create_coin(market)
    if c["has_stock"]: return False, 0

    h = c["history"]
    h.append(price)
    if volume: c["vol_history"].append(volume)
    c["timed"].append((time.time(), price))
    c["real_data_count"] += 1

    if c["real_data_count"] < REAL_DATA_MIN: return False, 0

    # 쿨다운
    if time.time() - c["last_sell_time"] < c["cooldown"]: return False, 0

    rsi  = calc_rsi(h)
    ma20 = calc_ma(h, 20)
    ma60 = calc_ma(h, 60)
    vol  = calc_vol_pct(c["timed"])

    if rsi is None or ma20 is None or ma60 is None: return False, 0

    c["prev_rsi2"] = c["prev_rsi"]
    c["prev_rsi"]  = rsi

    # RSI V-Turn
    p1, p2 = c["prev_rsi"], c["prev_rsi2"]
    rsi_vturn = (p2 is not None and p1 is not None and
                 p2 <= c["rsi_buy"] and p1 > p2 and rsi > p1)

    if not rsi_vturn: return False, 0

    # 추가 조건
    ma_ok   = ma20 > ma60
    drop_ok = ma20 > 0 and (ma20 - price) / ma20 * 100 >= c["drop"]
    vol_ok  = vol is not None and c["vol_min"] <= vol <= c["vol_max"]

    if not (ma_ok and drop_ok and vol_ok): return False, 0

    # 신호 강도 (RSI가 낮을수록 강함)
    signal_strength = c["rsi_buy"] - p2
    return True, signal_strength

# ============================================================
# [10] 감시 종목 목록
# ============================================================
_watch_markets    = []
_watch_markets_ts = 0.0

def get_watch_markets():
    global _watch_markets, _watch_markets_ts
    now = time.time()
    if now - _watch_markets_ts < 60 and _watch_markets:
        return _watch_markets
    try:
        r = requests.get("https://api.upbit.com/v1/market/all?isDetails=false", timeout=5)
        if r.status_code != 200: return _watch_markets
        krw = [m["market"] for m in r.json()
               if m["market"].startswith("KRW-") and m["market"] not in BLACKLIST]
        r2 = requests.get("https://api.upbit.com/v1/ticker",
                          params={"markets": ",".join(krw[:100])}, timeout=5)
        if r2.status_code != 200: return _watch_markets
        tickers = sorted(r2.json(), key=lambda x: x.get("acc_trade_price_24h", 0), reverse=True)
        _watch_markets = [t["market"] for t in tickers if t.get("trade_price", 0) >= 100][:WATCH_COUNT]
        _watch_markets_ts = now
        cprint(f"[감시] {len(_watch_markets)}개 종목 갱신", Fore.CYAN)
    except Exception as e:
        cprint(f"[감시 목록 오류] {e}", Fore.YELLOW)
    return _watch_markets

# ============================================================
# [11] 상태 IPC 전송
# ============================================================
def _write_status():
    try:
        with slots_lock:
            holding = list(slots)
        data = {
            "holding":   len(holding) > 0,
            "pnl_today": daily_pnl,
            "trades":    sum(coins.get(m, {}).get("trade_count", 0) for m in coins),
            "slots":     holding,
            "ts":        time.time(),
        }
        path = os.path.join(SHARED_DIR, "status_multicoin.json")
        tmp  = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, path)
        os.chmod(path, 0o664)
    except Exception as e:
        cprint(f"[상태 기록 오류] {e}", Fore.YELLOW)

# ============================================================
# [12] IPC 명령 처리
# ============================================================
def handle_ipc():
    if not os.path.exists(_IPC_CMD_FILE): return
    try:
        tmp = _IPC_CMD_FILE + ".read"
        os.rename(_IPC_CMD_FILE, tmp)
        with open(tmp) as f:
            data = json.load(f)
        os.remove(tmp)
        cmd    = data.get("cmd", "").strip()
        req_id = data.get("req_id", "")
        if cmd:
            handle_command(cmd, req_id)
    except Exception as e:
        cprint(f"[IPC 처리 오류] {e}", Fore.YELLOW)

def handle_command(text, req_id=""):
    global _IPC_REQ_ID
    _IPC_REQ_ID = req_id
    cmd = text.strip().split()
    if not cmd: return

    if cmd[0] in ("/status", "/s", "/상태"):
        with slots_lock:
            holding = list(slots)
        lines = [f"📊 멀티코인봇 상태", f"━━━━━━━━━━━━━━━━━━━━",
                 f"슬롯: {len(holding)}/{MAX_SLOTS}",
                 f"오늘 손익: {daily_pnl:+,.0f}원"]
        for m in holding:
            c = coins.get(m, {})
            lines.append(f"📦 {m}: 매수가 {c.get('buy_price',0):,.2f}원")
        _write_ipc_result("\n".join(lines), req_id)

    elif cmd[0] in ("/start", "/시작"):
        for m in list(coins.keys()):
            coins[m]["running"] = True
        _write_ipc_result("✅ 매매 시작", req_id)

    elif cmd[0] in ("/stop", "/정지"):
        for m in list(coins.keys()):
            coins[m]["running"] = False
        _write_ipc_result("⏹ 매매 정지", req_id)

    elif cmd[0] == "/slots":
        with slots_lock:
            s = list(slots)
        _write_ipc_result(f"슬롯 {len(s)}/{MAX_SLOTS}: {', '.join(s) or '없음'}", req_id)

    elif cmd[0] in ("/why", "/왜"):
        import time as _t
        lines = ["🔍 매수 조건 요약", "━━━━━━━━━━━━━━━━━━━━"]
        with slots_lock:
            holding = set(slots)
        # 보유 중 종목
        for m in holding:
            c = coins.get(m, {})
            buy_p = c.get("buy_price", 0)
            price = list(c.get("history", [0]))[-1] if c.get("history") else 0
            pnl = (price - buy_p) / buy_p * 100 if buy_p else 0
            lines.append(f"📦 {m.replace('KRW-','')}: 보유중 {pnl:+.2f}%")
        # 감시 중 종목 (최대 10개)
        checked = 0
        with coins_lock:
            snap = dict(coins)
        now = _t.time()
        for m, c in snap.items():
            if m in holding: continue
            if checked >= 10: break
            h = list(c.get("history", []))
            if len(h) < 3:
                lines.append(f"⏳ {m.replace('KRW-','')}: 데이터 수집중")
                checked += 1
                continue
            rsi = calc_rsi(h)
            ma20 = calc_ma(h, 20)
            ma60 = calc_ma(h, 60)
            vol = calc_vol_pct(c.get("timed", []))
            price = h[-1]
            p1 = c.get("prev_rsi")
            p2 = c.get("prev_rsi2")
            cooldown_left = max(0, c.get("cooldown", 0) - (now - c.get("last_sell_time", 0)))
            # 이유 판단
            if cooldown_left > 0:
                reason = f"쿨다운 {cooldown_left:.0f}초"
            elif rsi is None:
                reason = "RSI 계산불가"
            elif p2 is None or not (p2 <= c.get("rsi_buy", 38) and p1 > p2):
                reason = f"RSI V턴없음 {rsi:.1f}"
            elif ma20 is None or ma20 <= (ma60 or 0):
                reason = f"MA하락 {rsi:.1f}"
            elif ma20 > 0 and (ma20 - price) / ma20 * 100 < c.get("drop", 0.5):
                reason = f"눌림부족 {rsi:.1f}"
            elif vol is None or not (c.get("vol_min", 0.3) <= vol <= c.get("vol_max", 6.0)):
                reason = f"변동성부족 {vol:.2f}%" if vol else "변동성없음"
            else:
                reason = "신호대기"
            lines.append(f"⏳ {m.replace('KRW-','')}: {reason}")
            checked += 1
        if checked == 0 and not holding:
            lines.append("감시 종목 없음")
        _write_ipc_result("\n".join(lines), req_id)

# ============================================================
# [13] 데이터 프리필
# ============================================================
def prefill(market):
    c = get_or_create_coin(market)
    prices = get_ohlcv(market, count=REAL_DATA_MIN + 10, interval=1)
    if prices:
        for p in prices:
            c["history"].append(p)
        c["real_data_count"] = len(prices)
        cprint(f"[프리필] {market} {len(prices)}개 로드", Fore.CYAN)

# ============================================================
# [14] 메인 루프
# ============================================================
_running = True

def run_bot():
    global _running, daily_pnl, _last_reset_day

    load_config()

    if not JWT_OK:
        print("❌ PyJWT 없음. pip install PyJWT"); sys.exit(1)

    # IPC 스레드
    def _ipc_loop():
        while _running:
            try: handle_ipc()
            except Exception as e: cprint(f"[IPC 루프] {e}", Fore.YELLOW)
            time.sleep(0.3)
    threading.Thread(target=_ipc_loop, daemon=True, name="ipc").start()

    send_msg(
        f"🚀 멀티코인봇 v{BOT_VERSION} 시작!\n"
        f"예산: {TOTAL_BUDGET:,}원 / 슬롯: {MAX_SLOTS}개\n"
        f"감시 종목: {WATCH_COUNT}개"
    )

    last_ticker_ts = 0.0

    while _running:
        try:
            now = time.time()

            # 일간 초기화
            today = date.today()
            if _last_reset_day != today:
                _last_reset_day = today
                daily_pnl = 0.0
                for c in coins.values(): c["daily_pnl"] = 0.0

            # IPC 상태 주기적 전송
            _write_status()

            # 감시 종목 시세 일괄 조회 (WATCH_INTERVAL초 간격)
            if now - last_ticker_ts < WATCH_INTERVAL:
                time.sleep(1)
                continue
            last_ticker_ts = now

            markets = get_watch_markets()
            if not markets:
                time.sleep(WATCH_INTERVAL)
                continue

            # 시세 일괄 조회
            try:
                r = requests.get("https://api.upbit.com/v1/ticker",
                                 params={"markets": ",".join(markets)}, timeout=5)
                if r.status_code != 200:
                    time.sleep(WATCH_INTERVAL); continue
                tickers = {t["market"]: t for t in r.json()}
            except Exception as e:
                cprint(f"[시세 조회 오류] {e}", Fore.YELLOW)
                time.sleep(WATCH_INTERVAL); continue

            # ── 보유 중 종목 매도 체크 ──────────────────────
            with slots_lock:
                holding = list(slots)
            for market in holding:
                td = tickers.get(market)
                if not td: continue
                price = td.get("trade_price", 0)
                if price > 0:
                    check_sell(market, price)

            # ── 미보유 종목 매수 신호 수집 ───────────────────
            with slots_lock:
                slot_cnt = len(slots)
            if slot_cnt >= MAX_SLOTS:
                continue

            signals = []
            for market, td in tickers.items():
                with slots_lock:
                    if market in slots: continue
                price  = td.get("trade_price", 0)
                volume = td.get("acc_trade_volume_24h", 0)
                if price <= 0: continue

                # 미등록 종목 프리필
                c = get_or_create_coin(market)
                if c["real_data_count"] == 0:
                    prefill(market)

                ok, strength = check_buy_signal(market, price, volume)
                if ok:
                    signals.append((market, price, strength))

            # 신호 강도 순 정렬 → 슬롯 여유만큼 매수
            signals.sort(key=lambda x: x[2], reverse=True)
            for market, price, strength in signals:
                with slots_lock:
                    if len(slots) >= MAX_SLOTS: break
                do_buy(market, price, f"RSI V-Turn (강도:{strength:.1f})")

        except Exception as e:
            cprint(f"[메인 루프 오류] {e}\n{traceback.format_exc()}", Fore.RED)
            time.sleep(5)

if __name__ == "__main__":
    run_bot()
