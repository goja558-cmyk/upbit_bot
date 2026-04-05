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

# 장세별 슬롯당 예산 비율 (TOTAL_BUDGET 대비)
# 상승장: 5슬롯 × 18% = 90% 투자, 10% 유보
# 중립장: 3슬롯 × 30% = 90% 투자, 10% 유보
# 하락장: 2슬롯 × 25% = 50% 투자, 50% 유보
SLOT_BUDGET_RATIO = {
    "bull":    0.18,
    "neutral": 0.28,
    "bear":    0.25,
}
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

def get_hourly_ma(market, short=20, long=60):
    """시간봉 MA20, MA60 API 직접 조회"""
    try:
        r = requests.get(
            f"{UPBIT_BASE}/candles/minutes/60",
            params={"market": market, "count": long + 5}, timeout=5
        )
        if r.status_code == 200:
            closes = [c["trade_price"] for c in reversed(r.json())]
            ma_s = sum(closes[-short:]) / short if len(closes) >= short else None
            ma_l = sum(closes[-long:]) / long if len(closes) >= long else None
            return ma_s, ma_l
    except Exception as e:
        cprint(f"[시간봉MA 오류] {market}: {e}", Fore.YELLOW)
    return None, None

# 시간봉 MA 캐시 (5분마다 갱신)
_hourly_ma_cache = {}
_hourly_ma_ts    = {}
HOURLY_MA_TTL    = 300

def get_hourly_ma_cached(market):
    import time as _t
    now = _t.time()
    if market in _hourly_ma_cache and now - _hourly_ma_ts.get(market, 0) < HOURLY_MA_TTL:
        return _hourly_ma_cache[market]
    ma20, ma60 = get_hourly_ma(market)
    _hourly_ma_cache[market] = (ma20, ma60)
    _hourly_ma_ts[market]    = now
    return ma20, ma60

def get_daily_ma_cached(market):
    import time as _t
    now = _t.time()
    if market in _daily_ma_cache and now - _daily_ma_ts.get(market, 0) < DAILY_MA_TTL:
        return _daily_ma_cache[market]
    ma20, ma60 = get_daily_ma(market)
    _daily_ma_cache[market] = (ma20, ma60)
    _daily_ma_ts[market]    = now
    return ma20, ma60


# 장세 캐시 (1시간마다 갱신)
_market_regime       = "neutral"   # "bull" / "neutral" / "bear"
_market_regime_ts    = 0.0
_market_regime_ttl   = 3600
_BASE_MAX_SLOTS      = 5   # 상승장 기준 슬롯

def detect_market_regime():
    """일봉 MA20/MA60 이격률로 장세 구분.
    상승: 이격률 ≥ +2%  /  중립: -2%~+2%  /  하락: ≤ -2%
    대표 종목 BTC 기준 (시장 전체 대리 지표)
    """
    global _market_regime, _market_regime_ts, MAX_SLOTS
    import time as _t
    now = _t.time()
    if now - _market_regime_ts < _market_regime_ttl:
        return _market_regime
    try:
        ma20, ma60 = get_daily_ma("KRW-BTC", short=20, long=60)
        if ma20 and ma60 and ma60 > 0:
            gap = (ma20 - ma60) / ma60 * 100
            if gap >= 2.0:
                regime = "bull"
                slots  = _BASE_MAX_SLOTS        # 5슬롯
            elif gap <= -2.0:
                regime = "bear"
                slots  = max(2, _BASE_MAX_SLOTS - 3)  # 2슬롯
            else:
                regime = "neutral"
                slots  = max(3, _BASE_MAX_SLOTS - 2)  # 3슬롯
            _market_regime    = regime
            MAX_SLOTS         = slots
            _market_regime_ts = now
            cprint(f"[장세] {regime.upper()} | MA이격 {gap:+.1f}% | 슬롯 {slots}개", Fore.CYAN)
        else:
            cprint("[장세] MA조회 실패 — 중립 유지", Fore.YELLOW)
    except Exception as e:
        cprint(f"[장세 감지 오류] {e}", Fore.YELLOW)
    return _market_regime


def get_regime_conditions():
    """장세별 진입 조건 반환.
    상승장: (기본 트리거, 보조 트리거) 튜플
    중립/하락장: (기본 트리거, None)
    """
    regime = _market_regime
    if regime == "bull":
        primary   = dict(rsi_max=30, drop_min=1.0, vol_ratio_min=1.0, vol_min=1.0, vol_max=8.0, is_secondary=False)
        secondary = dict(rsi_max=35, drop_min=1.5, vol_ratio_min=1.1, vol_min=1.0, vol_max=8.0, is_secondary=True)
        return primary, secondary
    elif regime == "neutral":
        primary = dict(rsi_max=28, drop_min=2.0, vol_ratio_min=1.3, vol_min=2.0, vol_max=8.0, is_secondary=False)
        return primary, None
    else:  # bear
        primary = dict(rsi_max=25, drop_min=3.0, vol_ratio_min=1.5, vol_min=2.0, vol_max=8.0, is_secondary=False)
        return primary, None


def _primary_slots():
    """기본 트리거 슬롯 수: 상승장 3개, 나머지 전체."""
    if _market_regime == "bull":
        return max(1, MAX_SLOTS - 2)
    return MAX_SLOTS


def _secondary_slots():
    """보조 트리거 슬롯 수: 상승장 2개, 나머지 0."""
    if _market_regime == "bull":
        return 2
    return 0


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

    regime = _market_regime or "neutral"
    ratio  = SLOT_BUDGET_RATIO.get(regime, 0.28)
    per_slot = max(MIN_ORDER_KRW, int(TOTAL_BUDGET * ratio))
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
    vol  = calc_vol_pct(c["timed"])

    # 시간봉 MA는 API에서 직접 가져옴 (history 혼재 문제 방지)
    ma20, ma60 = get_hourly_ma_cached(market)

    if rsi is None or ma20 is None or ma60 is None: return False, 0

    c["prev_rsi2"] = c["prev_rsi"]
    c["prev_rsi"]  = rsi
    p1 = c["prev_rsi2"]  # 이전 RSI

    # ── 공통 전처리 ──────────────────────────────────────────
    drop_pct = (ma20 - price) / ma20 * 100 if ma20 > 0 else 0

    # 거래량 배율
    vol_h = list(c["vol_history"])
    vol_ratio = 1.0
    if len(vol_h) >= 6 and vol_h[-1] > 0:
        avg5 = sum(vol_h[-6:-1]) / 5
        vol_ratio = vol_h[-1] / avg5 if avg5 > 0 else 1.0

    # 일봉 MA
    d_ma20, d_ma60 = get_daily_ma_cached(market)

    # 공통 필수 조건
    if ma20 <= ma60: return False, 0                          # 시간봉 추세
    if vol is None or not (1.0 <= vol <= 8.0): return False, 0  # 변동성 하한 1%로 완화, 양봉 조건 제거
    # RSI 반등 공통 필수에서 제거

    # ── 장세별 트리거 체크 ────────────────────────────────────
    primary, secondary = get_regime_conditions()
    matched_cond = None

    # 기본 트리거 체크
    if (rsi <= primary["rsi_max"] and
        drop_pct >= primary["drop_min"] and
        vol_ratio >= primary["vol_ratio_min"]):
        # 상승장: 일봉 필터 추가
        if _market_regime == "bull":
            if d_ma20 is None or d_ma60 is None or d_ma20 > d_ma60:
                matched_cond = primary
        else:
            matched_cond = primary

    # 보조 트리거 체크 (상승장 전용, 기본 트리거 미충족 시)
    if matched_cond is None and secondary is not None:
        if (rsi <= secondary["rsi_max"] and
            drop_pct >= secondary["drop_min"] and
            vol_ratio >= secondary["vol_ratio_min"]):
            if d_ma20 is None or d_ma60 is None or d_ma20 > d_ma60:
                matched_cond = secondary

    if matched_cond is None: return False, 0

    # ── 복합 점수 계산 ───────────────────────────────────────
    rsi_score  = max(0, min(40, (30 - rsi) * 2)) if rsi <= 30 else 0
    drop_score = 30 if drop_pct >= 4.0 else 20 if drop_pct >= 3.0 else 10
    vol_score  = 30 if vol_ratio >= 2.0 else 20 if vol_ratio >= 1.5 else 10 if vol_ratio >= 1.2 else 0
    total_score = rsi_score + drop_score + vol_score
    if total_score < 10: return False, 0

    # 보조 트리거면 점수에 is_secondary 태그
    is_sec = matched_cond.get("is_secondary", False)
    return True, total_score + (0.01 if is_sec else 0)  # 소수점으로 구분


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
        regime_kor = {"bull": "📈상승장", "neutral": "➖중립장", "bear": "📉하락장"}.get(_market_regime, "?")
        pri_slots = _primary_slots()
        sec_slots = _secondary_slots()
        slot_str = f"{len(holding)}/{MAX_SLOTS}" + (f" (기본{pri_slots}+보조{sec_slots})" if sec_slots > 0 else "")
        lines = [f"📊 멀티코인봇 상태", f"━━━━━━━━━━━━━━━━━━━━",
                 f"캔들: {CANDLE_INTERVAL}분봉  장세: {regime_kor}",
                 f"슬롯: {slot_str}  손익: {daily_pnl:+,.0f}원"]
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
        regime_kor = {"bull": "📈상승장", "neutral": "➖중립장", "bear": "📉하락장"}.get(_market_regime, "?")
        primary_c, secondary_c = get_regime_conditions()
        cond_str = f"기본 RSI≤{primary_c['rsi_max']} 눌림≥{primary_c['drop_min']}% 거래량≥{primary_c['vol_ratio_min']}배"
        if secondary_c:
            cond_str += f"\n보조 RSI≤{secondary_c['rsi_max']} 눌림≥{secondary_c['drop_min']}% 거래량≥{secondary_c['vol_ratio_min']}배"
        lines = ["🔍 매수 조건 요약", f"━━━━━━━━━━━━━━━━━━━━",
                 f"장세: {regime_kor}  슬롯: {_primary_slots()}+{_secondary_slots()}개",
                 cond_str]
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
        prob_list = []
        with coins_lock:
            snap = dict(coins)
        now = _t.time()
        for m, c in snap.items():
            if m in holding: continue
            if checked >= 30: break
            h = list(c.get("history", []))
            cnt = c.get("real_data_count", 0)
            if cnt < REAL_DATA_MIN:
                # 프리필 즉시 실행
                prefill(m)
                cnt = c.get("real_data_count", 0)
            if cnt < REAL_DATA_MIN:
                prob_list.append((m, 0, f"데이터 수집중 {cnt}/{REAL_DATA_MIN}"))
                checked += 1
                continue
            rsi = calc_rsi(h)
            vol = calc_vol_pct(c.get("timed", []))
            # 시간봉 MA API 직접 조회
            ma20, ma60 = get_hourly_ma_cached(m)
            price = h[-1] if h else 0
            p1 = c.get("prev_rsi")
            p2 = c.get("prev_rsi2")
            cooldown_left = max(0, c.get("cooldown", 0) - (now - c.get("last_sell_time", 0)))
            # 이유 판단 (이중 트리거 기준)
            drop_pct_w = (ma20 - price) / ma20 * 100 if ma20 and ma20 > 0 else 0
            d_ma20_w, d_ma60_w = get_daily_ma_cached(m)
            primary_w, secondary_w = get_regime_conditions()
            vol_h_w = list(c.get("vol_history", []))
            vol_ratio_w = 1.0
            if len(vol_h_w) >= 6 and vol_h_w[-1] > 0:
                avg5_w = sum(vol_h_w[-6:-1]) / 5
                vol_ratio_w = vol_h_w[-1] / avg5_w if avg5_w > 0 else 1.0

            def _check_trigger(cond_t):
                if rsi > cond_t["rsi_max"]: return f"RSI과열 {rsi:.1f}(≤{cond_t['rsi_max']})"

                if ma20 is None or ma20 <= (ma60 or 0): return f"MA하락"
                if drop_pct_w < cond_t["drop_min"]: return f"눌림부족 {drop_pct_w:.1f}%(≥{cond_t['drop_min']}%)"
                if vol is None or not (1.0 <= vol <= 8.0): return f"변동성부족 {vol:.1f}%" if vol else "변동성없음"
                if vol_ratio_w < cond_t["vol_ratio_min"]: return f"거래량부족 {vol_ratio_w:.1f}배(≥{cond_t['vol_ratio_min']}배)"
                if _market_regime == "bull" and d_ma20_w is not None and d_ma60_w is not None and d_ma20_w <= d_ma60_w:
                    return "일봉하락추세"
                return None  # 통과

            def _calc_prob(cond_t):
                scores = []
                rsi_target = cond_t["rsi_max"]
                scores.append(1.0 if rsi <= rsi_target else max(0, 1.0 - (rsi - rsi_target) / (70 - rsi_target)))
                scores.append(1.0 if (p1 is not None and rsi > p1) else 0.3)
                if ma20 and ma60:
                    scores.append(1.0 if ma20 > ma60 else 0.0)
                else:
                    scores.append(0.5)
                drop_target = cond_t["drop_min"]
                scores.append(1.0 if drop_pct_w >= drop_target else max(0, drop_pct_w / drop_target))
                if vol is None:
                    scores.append(0.2)
                elif 1.0 <= vol <= 8.0:
                    scores.append(1.0)
                elif vol < 2.0:
                    scores.append(max(0, vol / 2.0))
                else:
                    scores.append(max(0, 1.0 - (vol - 8.0) / 8.0))
                vr_target = cond_t["vol_ratio_min"]
                scores.append(1.0 if vol_ratio_w >= vr_target else max(0, vol_ratio_w / vr_target))
                if _market_regime == "bull":
                    if d_ma20_w and d_ma60_w:
                        scores.append(1.0 if d_ma20_w > d_ma60_w else 0.0)
                    else:
                        scores.append(0.5)
                return int(sum(scores) / len(scores) * 100)

            if cooldown_left > 0:
                prob_list.append((m, 0, f"쿨다운 {cooldown_left/3600:.1f}h 후"))
            elif rsi is None:
                prob_list.append((m, 0, "데이터 없음"))
            else:
                # 공통 필수 탈락 여부 먼저 체크
                common_fail = []
                if ma20 is None or ma60 is None or ma20 <= ma60:
                    common_fail.append("MA하락")
                if vol is None or not (1.0 <= vol <= 8.0):
                    common_fail.append(f"변동성{vol:.1f}%" if vol else "변동성없음")
                # 양봉은 open_price 없이 판단 불가 → 생략

                if common_fail:
                    prob_list.append((m, 0, ", ".join(common_fail)))
                else:
                    prob_pri = _calc_prob(primary_w)
                    prob_sec = _calc_prob(secondary_w) if secondary_w else 0
                    prob = max(prob_pri, prob_sec)
                    best_cond = primary_w if prob_pri >= prob_sec else (secondary_w or primary_w)
                    hints = []
                    if rsi > best_cond["rsi_max"]:
                        hints.append(f"RSI {rsi:.0f}→{best_cond['rsi_max']}")
                    if drop_pct_w < best_cond["drop_min"]:
                        hints.append(f"눌림 {drop_pct_w:.1f}→{best_cond['drop_min']}")
                    if vol_ratio_w < best_cond["vol_ratio_min"]:
                        hints.append(f"거래량 {vol_ratio_w:.1f}→{best_cond['vol_ratio_min']}")
                    hint_str = ", ".join(hints[:2])
                    prob_list.append((m, prob, hint_str))
            checked += 1
        hot  = sorted([(m,p,h) for m,p,h in prob_list if p >= 70], key=lambda x: x[1], reverse=True)
        mid  = sorted([(m,p,h) for m,p,h in prob_list if 40 <= p < 70], key=lambda x: x[1], reverse=True)
        cold = sorted([(m,p,h) for m,p,h in prob_list if p < 40], key=lambda x: x[1], reverse=True)
        if hot:
            lines.append("🔥 곧 살 수도 있음")
            for m, p, h in hot:
                lines.append(f"  {m.replace('KRW-','')}: {p}점" + (f" ({h})" if h else ""))
        if mid:
            lines.append("📊 중간 정도")
            for m, p, h in mid:
                lines.append(f"  {m.replace('KRW-','')}: {p}점" + (f" ({h})" if h else ""))
        if cold:
            lines.append("⏳ 아직 멀었음")
            for m, p, h in cold:
                lines.append(f"  {m.replace('KRW-','')}: {p}점" + (f" ({h})" if h else ""))
        if not prob_list and not holding:
            lines.append("감시 종목 없음")
        _write_ipc_result("\n".join(lines), req_id)
# ============================================================
# [13] 데이터 프리필
# ============================================================
def prefill(market):
    c = get_or_create_coin(market)
    prices = get_ohlcv(market, count=REAL_DATA_MIN + 10, interval=CANDLE_INTERVAL)
    if prices:
        now_ts = time.time()
        candle_sec = CANDLE_INTERVAL * 60  # 캔들 간격(초)
        for i, p in enumerate(prices):
            c["history"].append(p)
            # timed에 과거 타임스탬프로 역산해서 삽입 (변동성 계산용)
            past_ts = now_ts - (len(prices) - 1 - i) * candle_sec
            c["timed"].append((past_ts, p))
        c["real_data_count"] = max(REAL_DATA_MIN, len(prices))
        cprint(f"[프리필] {market} {len(prices)}개 로드 (timed 포함)", Fore.CYAN)

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

            # 장세 감지 (1시간마다 자동 갱신)
            detect_market_regime()

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

            # 점수 순 정렬 → 슬롯 분리 매수
            signals.sort(key=lambda x: x[2], reverse=True)
            pri_used = sum(1 for m in slots if not coins.get(m, {}).get("is_secondary", False))
            sec_used = sum(1 for m in slots if coins.get(m, {}).get("is_secondary", False))
            for market, price, score in signals:
                with slots_lock:
                    if len(slots) >= MAX_SLOTS: break
                is_sec = (score % 1) > 0.005  # 소수점으로 보조 트리거 구분
                real_score = int(score)
                if is_sec:
                    if sec_used >= _secondary_slots(): continue
                    sec_used += 1
                    tag = f"보조:{real_score}점"
                else:
                    if pri_used >= _primary_slots(): continue
                    pri_used += 1
                    tag = f"기본:{real_score}점"
                coins.get(market, {})["is_secondary"] = is_sec
                do_buy(market, price, tag)

        except Exception as e:
            cprint(f"[메인 루프 오류] {e}\n{traceback.format_exc()}", Fore.RED)
            time.sleep(5)

if __name__ == "__main__":
    run_bot()