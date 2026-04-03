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
LOOP_INTERVAL  = 10
WATCH_COUNT    = 30
WATCH_INTERVAL = 300     # 시세 일괄 조회 주기 (초) — 1시간봉은 5분마다면 충분
REAL_DATA_MIN  = 48      # 매수 허용 최소 데이터 수 (48시간치)
HISTORY_LEN    = 200
COOLDOWN_SEC   = 3600    # 1시간 쿨다운
CANDLE_INTERVAL = 60     # 1시간봉

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

def get_ohlcv(market, count=200, interval=None):
    if interval is None:
        interval = CANDLE_INTERVAL
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

def get_daily_ma(market, short=20, long=60):
    """일봉 MA20, MA60 조회 — 상위 타임프레임 필터용"""
    try:
        r = requests.get(
            f"{UPBIT_BASE}/candles/days",
            params={"market": market, "count": long + 5}, timeout=5
        )
        if r.status_code == 200:
            closes = [c["trade_price"] for c in reversed(r.json())]
            ma_s = sum(closes[-short:]) / short if len(closes) >= short else None
            ma_l = sum(closes[-long:]) / long if len(closes) >= long else None
            return ma_s, ma_l
    except Exception as e:
        cprint(f"[일봉MA 오류] {market}: {e}", Fore.YELLOW)
    return None, None

# 일봉 MA 캐시 (1시간마다 갱신)
_daily_ma_cache = {}
_daily_ma_ts    = {}
DAILY_MA_TTL    = 3600

def get_daily_ma_cached(market):
    import time as _t
    now = _t.time()
    if market in _daily_ma_cache and now - _daily_ma_ts.get(market, 0) < DAILY_MA_TTL:
        return _daily_ma_cache[market]
    ma20, ma60 = get_daily_ma(market)
    _daily_ma_cache[market] = (ma20, ma60)
    _daily_ma_ts[market]    = now
    return ma20, ma60


def calc_vol_pct(timed_prices):
    now = time.time()
    recent = [p for t, p in timed_prices if now - t <= 18000]  # 5시간
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

# 파라미터 프로파일 — 1시간봉 + 복합 점수제 기준
COIN_PROFILES = {
    "KRW-XRP":  dict(target=2.5, max_loss=-1.5, drop=2.0, trail_start=1.5, trail_gap=0.8, be_trigger=0.8, rsi_buy=50, vol_min=2.0, vol_max=8.0, cooldown=3600),
    "KRW-ETH":  dict(target=2.0, max_loss=-1.5, drop=2.0, trail_start=1.5, trail_gap=0.7, be_trigger=0.7, rsi_buy=50, vol_min=2.0, vol_max=8.0, cooldown=3600),
    "KRW-SOL":  dict(target=3.0, max_loss=-2.0, drop=2.0, trail_start=2.0, trail_gap=1.0, be_trigger=1.0, rsi_buy=50, vol_min=2.0, vol_max=8.0, cooldown=7200),
    "KRW-DOGE": dict(target=2.5, max_loss=-1.5, drop=2.0, trail_start=1.5, trail_gap=0.8, be_trigger=0.8, rsi_buy=50, vol_min=2.0, vol_max=8.0, cooldown=3600),
}
PROFILE_DEFAULT = dict(target=2.0, max_loss=-1.5, drop=2.0, trail_start=1.5, trail_gap=0.8, be_trigger=0.8, rsi_buy=50, vol_min=2.0, vol_max=8.0, cooldown=3600)

def _make_coin_state(market):
    p = {**PROFILE_DEFAULT, **COIN_PROFILES.get(market, {})}
    return {
        "history":        deque(maxlen=HISTORY_LEN),
        "vol_history":    deque(maxlen=HISTORY_LEN),
        "timed":          deque(maxlen=200),
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
# [9] 매수 신호 체크 — 복합 점수제
# ============================================================
def check_buy_score(market, price, volume, open_price=0):
    """복합 점수제 매수 신호 체크.
    반환: (신호여부, 점수 0~100)
    점수 구성: RSI 40점 + MA이격 30점 + 거래량 30점
    """
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
    p1 = c["prev_rsi2"]  # 이전 RSI

    # ── 필수 조건 (통과 못 하면 즉시 탈락) ──────────────────
    # 1. RSI 50 이하 + 1봉 반등
    if rsi > 50: return False, 0
    if p1 is None or rsi <= p1: return False, 0   # 반등 없음

    # 2. 시간봉 MA20 > MA60 (상승추세)
    if ma20 <= ma60: return False, 0

    # 3. 눌림 -2% 이상
    drop_pct = (ma20 - price) / ma20 * 100 if ma20 > 0 else 0
    if drop_pct < 2.0: return False, 0

    # 4. 변동성 2~8%
    if vol is None or not (2.0 <= vol <= 8.0): return False, 0

    # 5. 양봉 확인 (현재봉 종가 > 시가)
    if open_price > 0 and price <= open_price: return False, 0

    # 6. 일봉 MA20 > MA60 (상위 타임프레임 필터)
    d_ma20, d_ma60 = get_daily_ma_cached(market)
    if d_ma20 is not None and d_ma60 is not None:
        if d_ma20 <= d_ma60: return False, 0

    # ── 복합 점수 계산 ───────────────────────────────────────
    # RSI 점수 (최대 40점): RSI 낮을수록 고점수
    # RSI 30→0점, 20→20점, 10→40점
    rsi_score = max(0, min(40, (30 - rsi) * 2)) if rsi <= 30 else 0

    # MA이격 점수 (최대 30점): 눌림 깊을수록 고점수
    # -2%→10점, -3%→20점, -4% 이하→30점
    if drop_pct >= 4.0:
        drop_score = 30
    elif drop_pct >= 3.0:
        drop_score = 20
    elif drop_pct >= 2.0:
        drop_score = 10
    else:
        drop_score = 0

    # 거래량 점수 (최대 30점): 직전 5봉 평균 대비
    vol_h = list(c["vol_history"])
    if len(vol_h) >= 6 and vol_h[-1] > 0:
        avg5 = sum(vol_h[-6:-1]) / 5
        ratio = vol_h[-1] / avg5 if avg5 > 0 else 1.0
        if ratio >= 2.0:
            vol_score = 30
        elif ratio >= 1.5:
            vol_score = 20
        elif ratio >= 1.2:
            vol_score = 10
        else:
            vol_score = 0
    else:
        vol_score = 0

    total_score = rsi_score + drop_score + vol_score
    # 최소 점수 10점 이상이어야 신호
    if total_score < 10: return False, 0

    return True, total_score


# 하위 호환용
def check_buy_signal(market, price, volume):
    ok, score = check_buy_score(market, price, volume)
    return ok, score

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
                 f"캔들: {CANDLE_INTERVAL}분봉  슬롯: {len(holding)}/{MAX_SLOTS}",
                 f"오늘 손익: {daily_pnl:+,.0f}원"]
        import time as _t
        for m in holding:
            c = coins.get(m, {})
            buy_p = c.get("buy_price", 0)
            h = list(c.get("history", [0]))
            cur = h[-1] if h else 0
            pnl_pct = (cur - buy_p) / buy_p * 100 if buy_p else 0
            hold_h = (_t.time() - c.get("buy_time", _t.time())) / 3600
            lines.append(f"📦 {m.replace('KRW-','')}: {pnl_pct:+.2f}% ({hold_h:.1f}h보유)")
        if not holding:
            lines.append("⏳ 대기중")
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
        lines = ["🔍 매수 조건 요약", f"━━━━━━━━━━━━━━━━━━━━",
                 f"캔들: {CANDLE_INTERVAL}분봉  감시: {WATCH_COUNT}종목"]
        with slots_lock:
            holding = set(slots)
        # 보유 중 종목
        for m in holding:
            c = coins.get(m, {})
            buy_p = c.get("buy_price", 0)
            price = list(c.get("history", [0]))[-1] if c.get("history") else 0
            pnl = (price - buy_p) / buy_p * 100 if buy_p else 0
            hold_h = (_t.time() - c.get("buy_time", _t.time())) / 3600
            lines.append(f"📦 {m.replace('KRW-','')}: 보유중 {pnl:+.2f}% ({hold_h:.1f}h)")
        # 감시 중 종목 (최대 10개)
        checked = 0
        with coins_lock:
            snap = dict(coins)
        now = _t.time()
        for m, c in snap.items():
            if m in holding: continue
            if checked >= 10: break
            h = list(c.get("history", []))
            cnt = c.get("real_data_count", 0)
            if cnt < REAL_DATA_MIN:
                lines.append(f"⏳ {m.replace('KRW-','')}: 데이터 수집중 ({cnt}/{REAL_DATA_MIN}h)")
                checked += 1
                continue
            rsi = calc_rsi(h)
            ma20 = calc_ma(h, 20)
            ma60 = calc_ma(h, 60)
            vol = calc_vol_pct(c.get("timed", []))
            price = h[-1] if h else 0
            p1 = c.get("prev_rsi")
            p2 = c.get("prev_rsi2")
            cooldown_left = max(0, c.get("cooldown", 0) - (now - c.get("last_sell_time", 0)))
            # 이유 판단 (복합 점수제 기준)
            drop_pct = (ma20 - price) / ma20 * 100 if ma20 and ma20 > 0 else 0
            d_ma20, d_ma60 = get_daily_ma_cached(m)
            if cooldown_left > 0:
                cd_h = cooldown_left / 3600
                reason = f"쿨다운 {cd_h:.1f}h"
            elif rsi is None:
                reason = "RSI 계산불가"
            elif rsi > 50:
                reason = f"RSI과열 {rsi:.1f}"
            elif p1 is None or rsi <= p1:
                reason = f"RSI반등없음 {rsi:.1f}"
            elif ma20 is None or ma20 <= (ma60 or 0):
                reason = f"MA하락 {rsi:.1f}"
            elif drop_pct < 2.0:
                reason = f"눌림부족 {drop_pct:.1f}%"
            elif vol is None or not (2.0 <= vol <= 8.0):
                reason = f"변동성부족 {vol:.1f}%" if vol else "변동성없음"
            elif d_ma20 is not None and d_ma60 is not None and d_ma20 <= d_ma60:
                reason = f"일봉하락추세"
            else:
                # 점수 계산
                rsi_score  = max(0, min(40, (30 - rsi) * 2)) if rsi <= 30 else 0
                drop_score = 30 if drop_pct >= 4 else 20 if drop_pct >= 3 else 10
                vol_h = list(c.get("vol_history", []))
                vol_score = 0
                if len(vol_h) >= 6 and vol_h[-1] > 0:
                    avg5 = sum(vol_h[-6:-1]) / 5
                    ratio = vol_h[-1] / avg5 if avg5 > 0 else 1.0
                    vol_score = 30 if ratio >= 2.0 else 20 if ratio >= 1.5 else 10 if ratio >= 1.2 else 0
                score = rsi_score + drop_score + vol_score
                reason = f"점수부족 {score}점" if score < 10 else f"✅신호 {score}점"
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
    prices = get_ohlcv(market, count=REAL_DATA_MIN + 10, interval=CANDLE_INTERVAL)
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

                open_price = td.get("opening_price", 0)
                ok, score = check_buy_score(market, price, volume, open_price)
                if ok:
                    signals.append((market, price, score))

            # 복합 점수 순 정렬 → 슬롯 여유만큼 매수
            signals.sort(key=lambda x: x[2], reverse=True)
            for market, price, score in signals:
                with slots_lock:
                    if len(slots) >= MAX_SLOTS: break
                do_buy(market, price, f"복합점수:{score:.0f}점")

        except Exception as e:
            cprint(f"[메인 루프 오류] {e}\n{traceback.format_exc()}", Fore.RED)
            time.sleep(5)

if __name__ == "__main__":
    run_bot()
