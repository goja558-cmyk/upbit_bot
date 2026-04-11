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

# ── vol_ratio 자동 조정 시스템 ────────────────────────────────
VOL_RATIO_DEFAULT  = 1.3   # 중립장 기본값
VOL_RATIO_MIN_CAP  = 1.1   # 최저 한도
VOL_RATIO_STEP     = 0.1   # 조정 단위
_vol_ratio_current = VOL_RATIO_DEFAULT   # 현재 적용 중인 값
_vol_ratio_pending = False               # 사용자 승인 대기 중
_last_trade_ts     = 0.0                 # 마지막 거래 시각 (시작 시 파일에서 복원)
_last_adjust_ts    = 0.0                 # 마지막 조정 시각
_adjust_cooldown   = 6 * 3600           # 조정 후 쿨다운 (6시간)
_recent_trades_ts  = []                  # 최근 거래 시각 목록 (복구 판단용)
VOL_RATIO_LOG_FILE = os.path.join(BASE_DIR, "vol_ratio_log.csv")

def _log_trade(side, market, price, qty, pnl=0.0, reason="", buy_price=0.0):
    """매수/매도 이벤트를 인디케이터 날짜별 파일에 기록."""
    try:
        path = _get_indicator_log_path()
        row  = (
            f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')},"
            f"{market},{price:.4f},{datetime.now().hour},"
            f",,,,,,,,,,"           # rsi~near_trigger 빈칸
            f","                    # signal_score
            f","                    # entry_possible
            f"trade_{side.lower()},"  # log_type
            f","                    # regime
            f",,,0,"               # fwd_return_5m,10m,fwd_ts,fwd_filled
            f"{qty:.6f},{int(pnl)},{buy_price:.4f},{reason}"
        )
        _write_indicator_row(path, row)
    except Exception as e:
        cprint(f"[매매로그 오류] {e}", Fore.YELLOW)
_TRADE_STATE_FILE  = os.path.join(BASE_DIR, "vol_trade_state.json")


_PNL_STATE_FILE = os.path.join(BASE_DIR, "pnl_state.json")

def _save_pnl_state():
    """주간/월간/누적 손익 파일 저장."""
    try:
        tmp = _PNL_STATE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump({
                "daily_pnl":         daily_pnl,
                "weekly_pnl":        weekly_pnl,
                "monthly_pnl":       monthly_pnl,
                "total_pnl":         total_pnl,
                "last_reset_day":    str(_last_reset_day),
                "last_reset_week":   list(_last_reset_week),
                "last_reset_month":  list(_last_reset_month),
            }, f)
        os.replace(tmp, _PNL_STATE_FILE)
    except Exception as e:
        cprint(f"[pnl_state 저장 오류] {e}", Fore.YELLOW)


def _load_pnl_state():
    """재시작 시 손익 통계 복원."""
    global daily_pnl, weekly_pnl, monthly_pnl, total_pnl
    global _last_reset_day, _last_reset_week, _last_reset_month
    if not os.path.exists(_PNL_STATE_FILE):
        return
    try:
        with open(_PNL_STATE_FILE) as f:
            data = json.load(f)
        today = date.today()
        saved_day = date.fromisoformat(data.get("last_reset_day", str(today)))
        # 날짜가 바뀌었으면 일간만 0으로
        daily_pnl    = float(data.get("daily_pnl",   0)) if saved_day == today else 0.0
        weekly_pnl   = float(data.get("weekly_pnl",  0))
        monthly_pnl  = float(data.get("monthly_pnl", 0))
        total_pnl    = float(data.get("total_pnl",   0))
        _last_reset_day   = saved_day if saved_day == today else today
        _last_reset_week  = tuple(data.get("last_reset_week",  list(_last_reset_week)))
        _last_reset_month = tuple(data.get("last_reset_month", list(_last_reset_month)))
        cprint(f"✅ 손익 복원 — 일:{daily_pnl:+,.0f} 주:{weekly_pnl:+,.0f} 월:{monthly_pnl:+,.0f} 누적:{total_pnl:+,.0f}", Fore.CYAN)
    except Exception as e:
        cprint(f"[pnl_state 복원 오류] {e}", Fore.YELLOW)


def _save_trade_state():
    """마지막 거래 시각을 파일에 저장 — 재시작 후 복원용."""
    try:
        with open(_TRADE_STATE_FILE, "w") as f:
            json.dump({
                "last_trade_ts":     _last_trade_ts,
                "vol_ratio_current": _vol_ratio_current,
                "last_adjust_ts":    _last_adjust_ts,
            }, f)
    except Exception as e:
        cprint(f"[trade_state 저장 오류] {e}", Fore.YELLOW)


def _load_trade_state():
    """재시작 시 마지막 거래 시각 / vol_ratio 복원."""
    global _last_trade_ts, _vol_ratio_current, _last_adjust_ts
    if not os.path.exists(_TRADE_STATE_FILE):
        # 파일 없으면 충분히 오래된 시각으로 초기화 (즉시 체크 방지용 1시간 전)
        _last_trade_ts = time.time() - 3600
        return
    try:
        with open(_TRADE_STATE_FILE) as f:
            data = json.load(f)
        _last_trade_ts    = float(data.get("last_trade_ts",    time.time() - 3600))
        _vol_ratio_current = float(data.get("vol_ratio_current", VOL_RATIO_DEFAULT))
        _last_adjust_ts   = float(data.get("last_adjust_ts",   0.0))
        elapsed = (time.time() - _last_trade_ts) / 3600
        cprint(
            f"[vol_state 복원] vol_ratio={_vol_ratio_current} / "
            f"마지막 거래 {elapsed:.1f}시간 전", Fore.CYAN
        )
    except Exception as e:
        cprint(f"[trade_state 복원 오류] {e}", Fore.YELLOW)
        _last_trade_ts = time.time() - 3600

# ── 인디케이터 로그 시스템 ────────────────────────────────────
INDICATOR_LOG_INTERVAL = 300   # 종목당 기록 인터벌 (5분)
_indicator_last_ts     = {}    # {market: last_log_ts}
# fwd_return 추적: {market: [(log_ts, price, row_path, row_lineno), ...]}
# 간단하게 {market: (ts, price)} 로 직전 기록만 유지
_indicator_pending_fwd = {}    # {market: {"ts": float, "price": float, "file": str}}


def _get_indicator_log_path(market=None):
    """logs/indicators/YYYY-MM-DD.csv — 모든 코인 하나의 파일"""
    log_dir = os.path.join(BASE_DIR, "logs", "indicators")
    os.makedirs(log_dir, exist_ok=True)
    return os.path.join(log_dir, f"{date.today().strftime('%Y-%m-%d')}.csv")


def _write_indicator_row(path, row):
    header = not os.path.exists(path)
    try:
        with open(path, "a", encoding="utf-8") as f:
            if header:
                f.write(
                    "datetime,market,price,hour,rsi,drop_pct,vol_ratio,vol_pct,"
                    "h_ma20,h_ma60,d_ma20,d_ma60,near_trigger,signal_score,"
                    "entry_possible,log_type,regime,"
                    "fwd_return_5m,fwd_return_10m,fwd_ts,fwd_filled,"
                    "qty,pnl_krw,buy_price,reason\n"
                )
            f.write(row + "\n")
    except Exception as e:
        cprint(f"[인디케이터 로그 오류] {e}", Fore.YELLOW)


def _update_fwd_returns(market, current_price):
    """직전 pending 행의 fwd_return을 현재가로 채워 덮어씀."""
    pend = _indicator_pending_fwd.get(market)
    if not pend:
        return
    elapsed = time.time() - pend["ts"]
    # 5분(300s) 이상 지났으면 fwd 채우기
    if elapsed < INDICATOR_LOG_INTERVAL:
        return
    try:
        fwd = round((current_price - pend["price"]) / pend["price"] * 100, 4)
        fpath = pend["file"]
        if not os.path.exists(fpath):
            return
        with open(fpath, "r", encoding="utf-8") as f:
            lines = f.readlines()
        # 마지막 실제 데이터 행 중 fwd가 비어있는 행 수정
        for i in range(len(lines) - 1, 0, -1):
            parts = lines[i].rstrip("\n").split(",")
            if len(parts) >= 21 and parts[17] == "" and parts[0].startswith(pend["dt"]):
                parts[17] = str(fwd)   # fwd_return_5m
                parts[18] = str(fwd)   # fwd_return_10m
                parts[20] = "1"        # fwd_filled
                lines[i] = ",".join(parts) + "\n"
                break
        with open(fpath, "w", encoding="utf-8") as f:
            f.writelines(lines)
    except Exception as e:
        cprint(f"[fwd 업데이트 오류] {market}: {e}", Fore.YELLOW)
    finally:
        _indicator_pending_fwd.pop(market, None)


def log_indicator(market, price, rsi, drop_pct, vol_ratio, vol_pct,
                  h_ma20, h_ma60, d_ma20, d_ma60,
                  signal_score, entry_possible, log_type):
    """5분 인터벌로 종목별 지표를 날짜별 CSV에 기록."""
    now = time.time()
    last = _indicator_last_ts.get(market, 0)
    if now - last < INDICATOR_LOG_INTERVAL:
        return

    # 직전 pending fwd 채우기 시도
    _update_fwd_returns(market, price)

    _indicator_last_ts[market] = now
    dt_str  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    hour    = datetime.now().hour
    primary, _ = get_regime_conditions()
    near    = round(primary["vol_ratio_min"] - vol_ratio, 4) if vol_ratio < primary["vol_ratio_min"] else 0.0

    row = (
        f"{dt_str},{market},{price},{hour},"
        f"{round(rsi,2) if rsi is not None else ''},"
        f"{round(drop_pct,4) if drop_pct is not None else ''},"
        f"{round(vol_ratio,4) if vol_ratio is not None else ''},"
        f"{round(vol_pct,4) if vol_pct is not None else ''},"
        f"{round(h_ma20,2) if h_ma20 is not None else ''},"
        f"{round(h_ma60,2) if h_ma60 is not None else ''},"
        f"{round(d_ma20,2) if d_ma20 is not None else ''},"
        f"{round(d_ma60,2) if d_ma60 is not None else ''},"
        f"{round(near,4)},"
        f"{signal_score},{entry_possible},{log_type},{_market_regime},"
        f",,{now},0"   # fwd_return_5m, fwd_return_10m 나중에 채움 / fwd_filled=0
    )
    path = _get_indicator_log_path(market)
    _write_indicator_row(path, row)

    # fwd pending 등록
    _indicator_pending_fwd[market] = {
        "ts":    now,
        "price": price,
        "file":  path,
        "dt":    dt_str[:16],   # YYYY-MM-DD HH:MM 으로 행 매칭
    }

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
            order_uuid = r.json().get("uuid", "")
            cprint(f"[주문] {side} {market} uuid={order_uuid}", Fore.CYAN)
            return confirm_order(order_uuid), order_uuid
    except Exception as e:
        cprint(f"[주문 오류] {e}", Fore.RED)
    return (0, 0), ""

def confirm_order(uuid, retry=30):
    """주문 체결 확인. done/cancel이면 반환, wait이면 재시도.
    시장가 매수는 보통 즉시 체결되나 API 응답 지연 대비 30회(30초) 대기."""
    for i in range(retry):
        try:
            h = _upbit_headers()
            r = requests.get(f"{UPBIT_BASE}/order", headers=h,
                             params={"uuid": uuid}, timeout=5)
            if r.status_code == 200:
                d = r.json()
                state  = d.get("state")
                filled = float(d.get("executed_volume", 0))
                funds  = float(d.get("executed_funds", 0))
                avg_p  = funds / filled if filled > 0 else 0
                if state in ("done", "cancel"):
                    return filled, avg_p
                # wait 상태지만 일부 체결 — 마지막 5회 안에서 반환
                if state == "wait" and filled > 0 and i >= retry - 5:
                    cprint(f"[주문 부분체결] {uuid} filled={filled:.6f}", Fore.YELLOW)
                    return filled, avg_p
        except Exception as e:
            cprint(f"[체결 확인 오류] {e}", Fore.YELLOW)
        time.sleep(1)
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
        primary = dict(rsi_max=28, drop_min=2.0, vol_ratio_min=_vol_ratio_current, vol_min=2.0, vol_max=8.0, is_secondary=False)
        return primary, None
    else:  # bear
        primary = dict(rsi_max=25, drop_min=3.0, vol_ratio_min=1.5, vol_min=2.0, vol_max=8.0, is_secondary=False)
        return primary, None


TREND_SLOTS = 1        # 추세추종 전용 슬롯 (항상 1개)
TREND_BUDGET_RATIO = 0.20   # 추세추종 예산 비율 (전체의 20%)

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


def _trend_slots():
    """추세추종 슬롯: 항상 1개."""
    return TREND_SLOTS


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
daily_pnl        = 0.0
weekly_pnl       = 0.0
monthly_pnl      = 0.0
total_pnl        = 0.0
_last_reset_day  = date.today()
_last_reset_week = date.today().isocalendar()[:2]
_last_reset_month= (date.today().year, date.today().month)

# 미체결 주문 재확인 큐 {uuid: {market, order_krw, reason, ts, side}}
_pending_orders: dict = {}
_pending_sells:  dict = {}   # {uuid: {market, qty, buy_price, reason, ts}}

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
        "trend_prev_rsi": None,
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
    # 추세추종 여부 판단 (reason에 "추세" 포함)
    if "추세" in reason:
        ratio = TREND_BUDGET_RATIO
    else:
        ratio = SLOT_BUDGET_RATIO.get(regime, 0.28)
    per_slot = max(MIN_ORDER_KRW, int(TOTAL_BUDGET * ratio))
    balance  = get_balance_krw()
    order_krw = int(min(per_slot, balance) * 0.98)
    if order_krw < MIN_ORDER_KRW:
        cprint(f"[잔고 부족] {market} 잔고:{balance:,.0f}원", Fore.YELLOW)
        return False

    (filled, avg_p), order_uuid = send_order("BUY", market, order_krw)
    if filled <= 0 or avg_p <= 0:
        cprint(f"[매수 실패] {market} (filled={filled:.6f}, avg={avg_p:.2f})", Fore.RED)
        if order_uuid:
            _pending_orders[order_uuid] = {
                "market":    market,
                "order_krw": order_krw,
                "reason":    reason,
                "ts":        time.time(),
            }
            send_msg(
                f"⚠️ {market} 체결 미확인 — 자동 재확인 중\n(10분 내 체결되면 자동 등록)",
                market=market, level="normal"
            )
        else:
            send_msg(
                f"⚠️ {market} 매수 실패 — 업비트 앱 확인 필요",
                market=market, level="normal"
            )
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
    _log_trade("BUY", market, avg_p, filled, reason=reason)
    send_msg(
        f"🛒 매수 완료!\n"
        f"매수가: {avg_p:,.2f}원  수량: {filled:.6f}\n"
        f"목표가: {target_p:,.2f}원  손절가: {stop_p:,.2f}원\n"
        f"이유: {reason}", market=market, level="critical"
    )
    _write_status()
    return True

def do_sell(market, price, reason):
    global daily_pnl, weekly_pnl, monthly_pnl, total_pnl
    c = get_or_create_coin(market)
    if not c["has_stock"]: return False

    buy_price_snapshot = c["buy_price"]
    filled_qty_snapshot = c["filled_qty"]

    (filled, avg_p), order_uuid = send_order("SELL", market, filled_qty_snapshot)
    if filled <= 0:
        if order_uuid:
            # uuid 있으면 재확인 큐에 등록
            _pending_sells[order_uuid] = {
                "market":    market,
                "qty":       filled_qty_snapshot,
                "buy_price": buy_price_snapshot,
                "reason":    reason,
                "ts":        time.time(),
            }
            send_msg(
                f"⚠️ {market} 매도 체결 미확인 — 자동 재확인 중\n"
                f"(10분 내 체결되면 자동 처리)",
                market=market, level="critical"
            )
        else:
            send_msg(f"🚨 매도 실패! 직접 매도하세요.\n이유: {reason}", market=market, level="critical")
        return False

    actual = avg_p if avg_p > 0 else price
    fee    = (c["buy_price"] * c["filled_qty"] + actual * filled) * FEE_RATE
    pnl    = (actual - c["buy_price"]) * filled - fee

    daily_pnl        += pnl
    weekly_pnl       += pnl
    monthly_pnl      += pnl
    total_pnl        += pnl
    c["daily_pnl"]   += pnl
    c["trade_count"]  += 1
    c["last_sell_time"] = time.time()
    _save_pnl_state()

    # vol_ratio 복구 판단용 거래 시각 기록
    global _last_trade_ts, _recent_trades_ts
    _last_trade_ts = time.time()
    _recent_trades_ts.append(time.time())
    _recent_trades_ts = [t for t in _recent_trades_ts if time.time() - t <= 6 * 3600]
    _save_trade_state()   # 재시작 후 복원용
    _log_trade("SELL", market, actual, filled, pnl=pnl, reason=reason, buy_price=c["buy_price"])
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

    buy_p = c["buy_price"]
    qty   = c["filled_qty"]

    # 매수가 0이면 매도 판단 불가 — 자동 싱크가 복구할 때까지 대기
    if buy_p <= 0:
        cprint(f"[check_sell] {market} buy_price=0 — 싱크 대기", Fore.YELLOW)
        return

    pnl_pct = (price - buy_p) / buy_p * 100

    # 트레일링 스탑
    if pnl_pct > c["highest_profit"]:
        c["highest_profit"] = pnl_pct
    trail_trigger = c["highest_profit"] >= c["trail_start"]
    trail_stop    = c["highest_profit"] - c["trail_gap"]

    # 본절 보호 — 수수료(매수0.05%+매도0.05%) 감안해서 +0.15% 이상일 때 활성화
    FEE_BUFFER = FEE_RATE * 2 * 100 + 0.05   # 약 0.15%
    if pnl_pct >= c["be_trigger"]:
        c["be_active"] = True
    be_stop = pnl_pct <= FEE_BUFFER and c["be_active"]

    # 익절
    if pnl_pct >= c["target"]:
        do_sell(market, price, f"익절 {pnl_pct:+.2f}%")
    # 트레일링
    elif trail_trigger and pnl_pct <= trail_stop:
        do_sell(market, price, f"트레일링 {pnl_pct:+.2f}% (고점:{c['highest_profit']:.2f}%)")
    # 본절 보호
    elif be_stop:
        do_sell(market, price, f"본절 보호 {pnl_pct:+.2f}%")
    # 손절 (추세추종은 -0.7%, 역추세는 기본 max_loss)
    else:
        trend_stop = -0.7 if c.get("is_trend", False) else c["max_loss"]
        if pnl_pct <= trend_stop:
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

    # 캔들 실체 비율 (시가 대비 종가 움직임)
    candle_body_pct = abs(price - open_price) / open_price * 100 if open_price > 0 else 0.0

    # 일봉 MA
    d_ma20, d_ma60 = get_daily_ma_cached(market)

    # near_vol: vol_ratio가 기준 -0.3 이내면 near로 분류
    primary_c, _ = get_regime_conditions()
    near_vol = (primary_c["vol_ratio_min"] - 0.3) <= vol_ratio < primary_c["vol_ratio_min"]

    # ── 공통 필수 조건 ───────────────────────────────────────
    # 1단계: 최소 변동성 필수 (죽은 코인 제거)
    if vol is None or vol < 0.8:
        log_indicator(
            market, price, rsi, drop_pct, vol_ratio, vol or 0,
            ma20, ma60, d_ma20, d_ma60,
            signal_score=0, entry_possible=0, log_type="watch",
        )
        return False, 0

    # 변동성 상한 (급등락 중인 코인 제외)
    if vol > 8.0:
        log_indicator(
            market, price, rsi, drop_pct, vol_ratio, vol,
            ma20, ma60, d_ma20, d_ma60,
            signal_score=0, entry_possible=0, log_type="watch",
        )
        return False, 0

    # 시간봉 MA 추세 확인
    if ma20 <= ma60:
        log_indicator(
            market, price, rsi, drop_pct, vol_ratio, vol,
            ma20, ma60, d_ma20, d_ma60,
            signal_score=0, entry_possible=0, log_type="watch",
        )
        return False, 0

    # 2단계: 보조 조건 (vol_ratio OR 캔들 실체) — 진짜 움직임 확인
    body_ok = candle_body_pct >= 0.6
    vol_ok  = vol_ratio >= 1.05
    if not vol_ok and not body_ok:
        log_indicator(
            market, price, rsi, drop_pct, vol_ratio, vol,
            ma20, ma60, d_ma20, d_ma60,
            signal_score=0, entry_possible=0, log_type="near",
        )
        return False, 0

    # ── 장세별 트리거 체크 ────────────────────────────────────
    primary, secondary = get_regime_conditions()
    matched_cond = None

    # 기본 트리거 체크
    if (rsi <= primary["rsi_max"] and
        drop_pct >= primary["drop_min"]):
        if _market_regime == "bull":
            if d_ma20 is None or d_ma60 is None or d_ma20 > d_ma60:
                matched_cond = primary
        else:
            matched_cond = primary

    # 보조 트리거 체크 (상승장 전용)
    if matched_cond is None and secondary is not None:
        if (rsi <= secondary["rsi_max"] and
            drop_pct >= secondary["drop_min"]):
            if d_ma20 is None or d_ma60 is None or d_ma20 > d_ma60:
                matched_cond = secondary

    if matched_cond is None:
        _log_type = "near" if near_vol else "watch"
        log_indicator(
            market, price, rsi, drop_pct, vol_ratio, vol,
            ma20, ma60, d_ma20, d_ma60,
            signal_score=0, entry_possible=0, log_type=_log_type,
        )
        return False, 0

    # ── 복합 점수 계산 ───────────────────────────────────────
    rsi_score  = max(0, min(40, (30 - rsi) * 2)) if rsi <= 30 else 0
    drop_score = 30 if drop_pct >= 4.0 else 20 if drop_pct >= 3.0 else 10

    # vol_score: 거래량 + 변동성 구간별 점수 (컷이 아닌 가중치)
    vol_score = (
        30 if vol_ratio >= 2.0 else
        20 if vol_ratio >= 1.5 else
        15 if vol_ratio >= 1.1 else
        10 if vol_ratio >= 1.05 else
        0
    )
    vola_score = (
        10 if vol >= 1.2 else
        5  if vol >= 0.8 else
        0
    )
    # 캔들 실체 보너스 (거래량 부족 시 보완)
    body_score = 5 if body_ok and vol_ratio < 1.05 else 0
    vol_score  = min(30, vol_score + vola_score + body_score)
    total_score = rsi_score + drop_score + vol_score
    if total_score < 10:
        log_indicator(
            market, price, rsi, drop_pct, vol_ratio, vol,
            ma20, ma60, d_ma20, d_ma60,
            signal_score=total_score, entry_possible=0, log_type="near",
        )
        return False, 0

    # 보조 트리거면 점수에 is_secondary 태그
    is_sec = matched_cond.get("is_secondary", False)

    # 신호 발생 로그
    log_indicator(
        market, price, rsi, drop_pct, vol_ratio, vol,
        ma20, ma60, d_ma20, d_ma60,
        signal_score=total_score, entry_possible=1, log_type="signal",
    )

    return True, total_score + (0.01 if is_sec else 0)  # 소수점으로 구분


# 하위 호환용
def check_buy_signal(market, price, volume):
    ok, score = check_buy_score(market, price, volume)
    return ok, score


def check_trend_signal(market, price, volume):
    """추세추종 매수 신호 체크.
    조건: 가격>MA20 + MA20상승중 + 최근5봉내MA20이탈이력 + MA20재돌파 + 이격≤1.5% + RSI50~65 + 거래량≥1.2배
    반환: (신호여부, 점수)  점수는 소수점 0.5 태그로 추세추종 구분
    """
    c = get_or_create_coin(market)
    if c["has_stock"]: return False, 0

    h = list(c["history"])
    if c["real_data_count"] < REAL_DATA_MIN: return False, 0
    if time.time() - c["last_sell_time"] < c["cooldown"]: return False, 0
    if len(h) < 10: return False, 0

    rsi    = calc_rsi(h)
    vol    = calc_vol_pct(c["timed"])
    ma20, ma60 = get_hourly_ma_cached(market)

    # trend_prev_rsi 항상 갱신 (조건 탈락 여부와 무관하게 RSI 추이 추적)
    prev_rsi = c.get("trend_prev_rsi")
    if rsi is None or ma20 is None:
        c["trend_prev_rsi"] = rsi
        return False, 0
    c["trend_prev_rsi"] = rsi

    # RSI 50~65 구간 (과열 전 추세 초입)
    if not (50 <= rsi <= 65): return False, 0

    # 가격 > MA20
    if price <= ma20: return False, 0

    # MA20 이격 ≤ 1.5% (막 돌파한 초입만)
    gap_pct = (price - ma20) / ma20 * 100
    if gap_pct > 1.5: return False, 0

    # 최근 5봉 내 MA20 이탈 이력 확인 (price_history 기준)
    # history는 루프마다 현재가가 쌓이므로 최근 5개 봉 확인
    recent5 = h[-6:-1] if len(h) >= 6 else h[:-1]
    had_below = any(p < ma20 for p in recent5)
    if not had_below: return False, 0   # 이탈 이력 없으면 탈락

    # MA20 상승 중 (RSI 상승으로 대리 판단) — trend_prev_rsi 기준
    if prev_rsi is None or rsi <= prev_rsi: return False, 0

    # 변동성 필터
    if vol is None or not (1.0 <= vol <= 8.0): return False, 0

    # 거래량 ≥ 1.2배
    vol_h = list(c["vol_history"])
    vol_ratio = 1.0
    if len(vol_h) >= 6 and vol_h[-1] > 0:
        avg5 = sum(vol_h[-6:-1]) / 5
        vol_ratio = vol_h[-1] / avg5 if avg5 > 0 else 1.0
    if vol_ratio < 1.2: return False, 0

    # 점수 계산
    rsi_score  = max(0, int((65 - rsi) / 15 * 40))   # RSI 낮을수록 고점수
    gap_score  = 20 if gap_pct <= 0.5 else 15 if gap_pct <= 1.0 else 10  # 이격 작을수록 고점수
    vol_score  = 30 if vol_ratio >= 2.0 else 20 if vol_ratio >= 1.5 else 10
    total = rsi_score + gap_score + vol_score
    if total < 10: return False, 0

    return True, total + 0.5   # 0.5 소수점으로 추세추종 구분

# ============================================================
# [10-B] vol_ratio 자동 조정 시스템
# ============================================================
def _get_recommend_log_path():
    """날짜별 추천 로그 파일 경로 반환."""
    log_dir = os.path.join(BASE_DIR, "logs", "recommend")
    os.makedirs(log_dir, exist_ok=True)
    return os.path.join(log_dir, f"{date.today().strftime('%Y-%m-%d')}.csv")


def _snapshot_market():
    """추천 당시 시장 상황 스냅샷 — 나중에 '그때 상황'을 재현할 수 있도록."""
    with slots_lock:
        holding = list(slots)
    # 감시 종목 중 vol_ratio 분포 (최근 수집된 값 기준)
    vol_ratios = []
    near_trigger_count = 0
    with coins_lock:
        snap = dict(coins)
    for m, c in snap.items():
        vol_h = list(c.get("vol_history", []))
        if len(vol_h) >= 6 and vol_h[-1] > 0:
            avg5 = sum(vol_h[-6:-1]) / 5
            vr = vol_h[-1] / avg5 if avg5 > 0 else 1.0
            vol_ratios.append(round(vr, 2))
            # 현재 기준 - 0.2 이내면 "near_trigger"로 간주
            if _vol_ratio_current - 0.2 <= vr < _vol_ratio_current:
                near_trigger_count += 1
    avg_vol_ratio = round(sum(vol_ratios) / len(vol_ratios), 2) if vol_ratios else 0.0
    return {
        "regime":            _market_regime,
        "slots_used":        len(holding),
        "slots_max":         MAX_SLOTS,
        "avg_vol_ratio":     avg_vol_ratio,
        "near_trigger":      near_trigger_count,
        "no_trade_h":        round((time.time() - _last_trade_ts) / 3600, 1),
        "recent_trades_6h":  len(_recent_trades_ts),
    }


def _log_recommend(current_val, recommended_val, reason_type, reason_detail, applied, snap=None):
    """추천 발생 시 상황과 함께 CSV에 기록.

    reason_type  : no_trade / restore
    reason_detail: 12h_no_trade / 24h_auto / manual / auto_restore
    applied      : Y / N / pending
    """
    if snap is None:
        snap = _snapshot_market()
    path   = _get_recommend_log_path()
    header = not os.path.exists(path)
    row = (
        f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')},"
        f"{current_val},{recommended_val},"
        f"{reason_type},{reason_detail},"
        f"{snap['regime']},"
        f"{snap['slots_used']}/{snap['slots_max']},"
        f"{snap['avg_vol_ratio']},"
        f"{snap['near_trigger']},"
        f"{snap['no_trade_h']},"
        f"{snap['recent_trades_6h']},"
        f"{applied}\n"
    )
    try:
        with open(path, "a", encoding="utf-8") as f:
            if header:
                f.write(
                    "datetime,"
                    "current_vol_ratio,recommended_vol_ratio,"
                    "reason_type,reason_detail,"
                    "regime,"
                    "slots_used,"
                    "avg_vol_ratio_market,"
                    "near_trigger_count,"
                    "no_trade_h,"
                    "recent_trades_6h,"
                    "applied\n"
                )
            f.write(row)
        cprint(f"[추천로그] {reason_detail} | {current_val}→{recommended_val} | applied={applied}", Fore.CYAN)
    except Exception as e:
        cprint(f"[추천로그 오류] {e}", Fore.YELLOW)


def _set_vol_ratio(new_val, reason):
    """vol_ratio 값 변경 + 추천 로그 기록."""
    global _vol_ratio_current, _last_adjust_ts
    old_val            = _vol_ratio_current
    _vol_ratio_current = round(new_val, 2)
    _last_adjust_ts    = time.time()
    # applied=Y 로 기록
    _log_recommend(
        current_val      = old_val,
        recommended_val  = _vol_ratio_current,
        reason_type      = "restore" if new_val > old_val else "no_trade",
        reason_detail    = reason,
        applied          = "Y",
    )
    cprint(f"[vol_ratio] {old_val} → {_vol_ratio_current} ({reason})", Fore.CYAN)
    _save_trade_state()   # vol_ratio 변경도 저장


def check_vol_ratio_adjust():
    """무거래 감지 → 완화 제안 / 거래 회복 → 복구. 메인루프에서 주기적으로 호출."""
    global _vol_ratio_pending, _last_adjust_ts

    now      = time.time()
    no_trade = now - _last_trade_ts

    # ── 복구 체크: 6시간 내 거래 2건 이상 ────────────────────
    recent_count = len(_recent_trades_ts)
    if _vol_ratio_current < VOL_RATIO_DEFAULT and recent_count >= 2:
        new_val = min(VOL_RATIO_DEFAULT, _vol_ratio_current + VOL_RATIO_STEP)
        _set_vol_ratio(new_val, "auto_restore")
        send_msg(
            f"📈 거래 회복 감지 (6h내 {recent_count}건)\n"
            f"거래량 기준 복구: {_vol_ratio_current - VOL_RATIO_STEP:.1f} → {_vol_ratio_current:.1f}"
        )
        _vol_ratio_pending = False
        return

    # 쿨다운 중이면 이하 로직 스킵
    if now - _last_adjust_ts < _adjust_cooldown:
        return

    # ── 12시간 무거래 → 사용자에게 완화 제안 ─────────────────
    if no_trade >= 12 * 3600 and not _vol_ratio_pending:
        if _vol_ratio_current <= VOL_RATIO_MIN_CAP:
            send_msg(
                f"⚠️ 12시간 무거래 — 거래량 기준이 최저({VOL_RATIO_MIN_CAP})입니다.\n"
                f"다른 조건(RSI·눌림)을 확인해주세요."
            )
            _vol_ratio_pending = True   # 중복 알림 방지
            _last_adjust_ts = now       # 쿨다운 시작
            return
        proposed = round(_vol_ratio_current - VOL_RATIO_STEP, 2)
        _vol_ratio_pending = True
        snap = _snapshot_market()
        _log_recommend(
            current_val     = _vol_ratio_current,
            recommended_val = proposed,
            reason_type     = "no_trade",
            reason_detail   = "12h_no_trade",
            applied         = "pending",
            snap            = snap,
        )
        send_msg(
            f"⚠️ 12시간 무거래 감지\n"
            f"거래량 기준 완화 제안: {_vol_ratio_current:.1f} → {proposed:.1f}\n"
            f"적용하려면 /voldown 입력\n"
            f"유지하려면 /volkeep 입력"
        )
        return

    # ── 24시간 무거래 → 자동으로 한 단계 더 완화 ────────────
    if no_trade >= 24 * 3600:
        if _vol_ratio_current <= VOL_RATIO_MIN_CAP:
            return
        new_val = round(_vol_ratio_current - VOL_RATIO_STEP, 2)
        _set_vol_ratio(new_val, "auto_24h")
        _vol_ratio_pending = False
        send_msg(
            f"🔧 24시간 무거래 — 거래량 기준 자동 완화\n"
            f"{_vol_ratio_current + VOL_RATIO_STEP:.1f} → {_vol_ratio_current:.1f}\n"
            f"(최저 한도: {VOL_RATIO_MIN_CAP})"
        )


# ============================================================
# [10-C] 감시 종목 목록
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
def _check_pending_orders():
    """미체결 매수/매도 주문을 주기적으로 재확인해서 자동 복구."""
    global daily_pnl, weekly_pnl, monthly_pnl, total_pnl
    now = time.time()

    # ── 매수 재확인 ─────────────────────────────────────────────
    to_del = []
    for order_uuid, info in list(_pending_orders.items()):
        if now - info["ts"] > 600:
            cprint(f"[매수 미체결 포기] {info['market']} uuid={order_uuid}", Fore.YELLOW)
            to_del.append(order_uuid)
            continue
        try:
            h = _upbit_headers()
            r = requests.get(f"{UPBIT_BASE}/order", headers=h,
                             params={"uuid": order_uuid}, timeout=5)
            if r.status_code != 200:
                continue
            d      = r.json()
            state  = d.get("state")
            filled = float(d.get("executed_volume", 0))
            funds  = float(d.get("executed_funds", 0))
            avg_p  = funds / filled if filled > 0 else 0
            if state in ("done", "cancel") and filled > 0 and avg_p > 0:
                market = info["market"]
                c = get_or_create_coin(market)
                prev_price = c.get("buy_price", 0)
                c.update({
                    "has_stock":      True,
                    "buy_price":      avg_p,
                    "filled_qty":     filled,
                    "be_active":      False,
                    "highest_profit": 0.0,
                    "buy_time":       c.get("buy_time") or now,
                })
                with slots_lock:
                    slots.add(market)
                target_p = avg_p * (1 + c["target"] / 100)
                stop_p   = avg_p * (1 + c["max_loss"] / 100)
                cprint(f"[매수복구] {market} {filled:.6f}개 @ {avg_p:,.2f}원", Fore.GREEN)
                send_msg(
                    f"✅ {market} 매수 체결 확인!\n"
                    f"매수가: {avg_p:,.2f}원  수량: {filled:.6f}\n"
                    f"목표가: {target_p:,.2f}원  손절가: {stop_p:,.2f}원",
                    market=market, level="critical"
                )
                _log_trade("BUY", market, avg_p, filled, reason=info.get("reason", "auto"))
                _write_status()
                to_del.append(order_uuid)
            elif state == "cancel" and filled == 0:
                to_del.append(order_uuid)
        except Exception as e:
            cprint(f"[매수 재확인 오류] {e}", Fore.YELLOW)
    for uid in to_del:
        _pending_orders.pop(uid, None)

    # ── 매도 재확인 ─────────────────────────────────────────────
    to_del_s = []
    for order_uuid, info in list(_pending_sells.items()):
        if now - info["ts"] > 600:
            cprint(f"[매도 미체결 포기] {info['market']} uuid={order_uuid}", Fore.YELLOW)
            send_msg(
                f"🚨 {info['market']} 매도 10분 초과 — 직접 확인 필요",
                market=info["market"], level="critical"
            )
            to_del_s.append(order_uuid)
            continue
        try:
            h = _upbit_headers()
            r = requests.get(f"{UPBIT_BASE}/order", headers=h,
                             params={"uuid": order_uuid}, timeout=5)
            if r.status_code != 200:
                continue
            d      = r.json()
            state  = d.get("state")
            filled = float(d.get("executed_volume", 0))
            funds  = float(d.get("executed_funds", 0))
            avg_p  = funds / filled if filled > 0 else 0
            if state in ("done", "cancel") and filled > 0:
                market    = info["market"]
                buy_price = info["buy_price"]
                qty       = info["qty"]
                actual    = avg_p if avg_p > 0 else 0
                fee       = (buy_price * qty + actual * filled) * FEE_RATE if actual > 0 else 0
                pnl       = (actual - buy_price) * filled - fee if actual > 0 else 0
                daily_pnl   += pnl
                weekly_pnl  += pnl
                monthly_pnl += pnl
                total_pnl   += pnl
                c = coins.get(market, {})
                if c:
                    c["daily_pnl"]      = c.get("daily_pnl", 0) + pnl
                    c["trade_count"]    = c.get("trade_count", 0) + 1
                    c["last_sell_time"] = now
                    c.update({"has_stock": False, "buy_price": 0.0, "filled_qty": 0.0,
                               "be_active": False, "highest_profit": 0.0, "buy_time": 0.0})
                with slots_lock:
                    slots.discard(market)
                _save_pnl_state()
                _log_trade("SELL", market, actual, filled, pnl=pnl,
                           reason=info.get("reason", "auto"), buy_price=buy_price)
                cprint(f"[매도복구] {market} {filled:.6f}개 @ {actual:,.2f}원 pnl={pnl:+,.0f}", Fore.GREEN)
                send_msg(
                    f"{'🟢 익절' if pnl >= 0 else '🔴 손절'} {market} 매도 체결 확인!\n"
                    f"매도가: {actual:,.2f}원\n"
                    f"이번 손익: {pnl:+,.0f}원\n"
                    f"이유: {info.get('reason', '')}",
                    market=market, level="critical"
                )
                _write_status()
                to_del_s.append(order_uuid)
        except Exception as e:
            cprint(f"[매도 재확인 오류] {e}", Fore.YELLOW)
    for uid in to_del_s:
        _pending_sells.pop(uid, None)


_last_balance_check_ts = 0.0
_BALANCE_CHECK_INTERVAL = 300   # 5분마다 잔고 폴링

def _auto_sync_holdings():
    """5분마다 업비트 실제 잔고를 폴링해서 봇이 모르는 보유 코인 자동 등록.
    uuid 추적 실패, confirm_order 타임아웃 등 모든 케이스를 커버."""
    global _last_balance_check_ts
    now = time.time()
    if now - _last_balance_check_ts < _BALANCE_CHECK_INTERVAL:
        return
    _last_balance_check_ts = now
    try:
        h = _upbit_headers()
        r = requests.get(f"{UPBIT_BASE}/accounts", headers=h, timeout=5)
        if r.status_code != 200:
            return
        accounts = r.json()
        with slots_lock:
            current_slots = set(slots)

        for a in accounts:
            currency = a.get("currency", "")
            if currency == "KRW":
                continue
            market  = f"KRW-{currency}"
            balance = float(a.get("balance", 0))
            avg_buy = float(a.get("avg_buy_price", 0))

            # 먼지 수량 필터
            if avg_buy <= 0 or balance * avg_buy < MIN_ORDER_KRW / 4:
                continue

            # pending_orders에 있는 종목은 uuid 조회가 더 정확하므로 건너뜀
            pending_markets = {info["market"] for info in _pending_orders.values()}
            if market in pending_markets:
                continue

            # 봇이 모르거나 매수가가 0인 보유 코인 발견 시 등록/수정
            c = get_or_create_coin(market)
            # has_stock 여부, buy_price 값과 무관하게 항상 최신 잔고로 동기화
            prev_price = c.get("buy_price", 0)
            c["has_stock"]      = True
            c["buy_price"]      = avg_buy
            c["filled_qty"]     = balance
            c["be_active"]      = c.get("be_active", False)
            c["highest_profit"] = c.get("highest_profit", 0.0)
            c["buy_time"]       = c.get("buy_time") or now
            with slots_lock:
                slots.add(market)

            # 매수가가 새로 등록되거나 0에서 정상화된 경우만 알림
            if prev_price <= 0 and avg_buy > 0:
                target_p = avg_buy * (1 + c["target"] / 100)
                stop_p   = avg_buy * (1 + c["max_loss"] / 100)
                cprint(f"[자동싱크] {market} {balance:.6f}개 @ {avg_buy:,.0f}원 등록/수정", Fore.GREEN)
                send_msg(
                    f"🔄 {market} 자동 싱크\n"
                    f"매수가: {avg_buy:,.0f}원  수량: {balance:.6f}\n"
                    f"목표가: {target_p:,.0f}원  손절가: {stop_p:,.0f}원",
                    market=market, level="critical"
                )
            _write_status()

        # pending_orders 중 이미 잔고에 반영된 것 정리
        synced = {f"KRW-{a.get('currency')}" for a in accounts
                  if float(a.get("balance", 0)) * float(a.get("avg_buy_price", 0)) >= MIN_ORDER_KRW / 4}
        for uid in list(_pending_orders.keys()):
            if _pending_orders[uid]["market"] in synced:
                _pending_orders.pop(uid, None)

    except Exception as e:
        cprint(f"[자동싱크 오류] {e}", Fore.YELLOW)


def _write_status():
    try:
        with slots_lock:
            holding = list(slots)

        # 실현 손익 + 미실현 손익 합산
        unrealized = 0.0
        for m in holding:
            c = coins.get(m, {})
            buy_p = c.get("buy_price", 0)
            qty   = c.get("filled_qty", 0)
            h     = list(c.get("history", []))
            cur_p = h[-1] if h else 0
            if buy_p > 0 and qty > 0 and cur_p > 0:
                unrealized += (cur_p - buy_p) * qty
        pnl_total = daily_pnl + unrealized

        # 근접 종목 수 계산 (check_buy_score score > 0인 종목)
        near_count = 0
        try:
            with coins_lock:
                snap = dict(coins)
            held_set = set(holding)
            for m, c in snap.items():
                if m in held_set: continue
                h = list(c.get("history", []))
                if not h: continue
                vol_h = list(c.get("vol_history", []))
                vol = vol_h[-1] if vol_h else 0
                ok, score = check_buy_score(m, h[-1], vol, 0)
                if score > 0:
                    near_count += 1
        except Exception:
            pass

        data = {
            "holding":    len(holding) > 0,
            "pnl_today":  daily_pnl,
            "pnl_total":  pnl_total,
            "unrealized": unrealized,
            "weekly_pnl":  weekly_pnl,
            "monthly_pnl": monthly_pnl,
            "total_pnl":   total_pnl,
            "trades":     sum(coins.get(m, {}).get("trade_count", 0) for m in coins),
            "slots":      holding,
            "near_count": near_count,
            "ts":         time.time(),
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
            # API 호출이 포함될 수 있는 명령은 별도 스레드로 실행 (IPC 루프 블로킹 방지)
            threading.Thread(
                target=handle_command, args=(cmd, req_id), daemon=True, name="ipc-cmd"
            ).start()
    except Exception as e:
        cprint(f"[IPC 처리 오류] {e}", Fore.YELLOW)

def handle_command(text, req_id=""):
    global _IPC_REQ_ID, _trading_paused
    _IPC_REQ_ID = req_id
    cmd = text.strip().split()
    if not cmd: return

    if cmd[0] in ("/status", "/s", "/상태"):
        with slots_lock:
            holding = list(slots)
        regime_kor = {"bull": "📈상승장", "neutral": "➖중립장", "bear": "📉하락장"}.get(_market_regime, "?")
        pri_slots = _primary_slots()
        sec_slots = _secondary_slots()
        trend_used = sum(1 for m in holding if coins.get(m, {}).get("is_trend", False))
        slot_str = f"{len(holding)}/{MAX_SLOTS+TREND_SLOTS}" + (f" (역추세{pri_slots+sec_slots}+추세{TREND_SLOTS})" if sec_slots > 0 else f" (역추세{pri_slots}+추세{TREND_SLOTS})")
        lines = [f"📊 멀티코인봇 상태", f"━━━━━━━━━━━━━━━━━━━━",
                 f"캔들: {CANDLE_INTERVAL}분봉  장세: {regime_kor}",
                 f"슬롯: {slot_str}",
                 f"매수: {'⏹ 정지중' if _trading_paused else '🟢 활성'}",
                 f"━━━━━━━━━━━━━━━━━━━━",
                 f"💰 일간:  {daily_pnl:+,.0f}원",
                 f"📅 주간:  {weekly_pnl:+,.0f}원",
                 f"🗓 월간:  {monthly_pnl:+,.0f}원",
                 f"📈 누적:  {total_pnl:+,.0f}원"]
        import time as _t
        for m in holding:
            c = coins.get(m, {})
            buy_p = c.get("buy_price", 0)
            h = list(c.get("history", []))
            cur = h[-1] if h else 0
            # history 없으면 현재가 직접 조회
            if cur <= 0:
                try:
                    cur, _ = get_price_and_volume(m)
                except Exception:
                    cur = 0
            pnl_pct = (cur - buy_p) / buy_p * 100 if buy_p and cur else 0
            hold_h = (_t.time() - c.get("buy_time", _t.time())) / 3600
            lines.append(f"📦 {m.replace('KRW-','')}: {pnl_pct:+.2f}% ({hold_h:.1f}h보유)")
        if not holding:
            lines.append("⏳ 대기중")
        _write_ipc_result("\n".join(lines), req_id)

    elif cmd[0] in ("/start", "/시작"):
        _trading_paused = False
        _write_ipc_result("✅ 매매 재개 — 신규 매수 활성화", req_id)

    elif cmd[0] in ("/stop", "/정지"):
        _trading_paused = True
        with slots_lock:
            holding = list(slots)
        hold_str = f"\n보유 중: {', '.join(m.replace('KRW-','') for m in holding)}" if holding else ""
        _write_ipc_result(f"⏹ 매매 정지 — 신규 매수 중단 (매도는 계속){hold_str}", req_id)

    elif cmd[0] == "/slots":
        with slots_lock:
            s = list(slots)
        _write_ipc_result(f"슬롯 {len(s)}/{MAX_SLOTS}: {', '.join(s) or '없음'}", req_id)

    elif cmd[0] == "/voldown":
        global _vol_ratio_pending
        if _vol_ratio_current <= VOL_RATIO_MIN_CAP:
            _vol_ratio_pending = False
            _write_ipc_result(f"⚠️ 이미 최저 한도({VOL_RATIO_MIN_CAP})입니다.", req_id)
            return
        proposed = round(_vol_ratio_current - VOL_RATIO_STEP, 2)
        _set_vol_ratio(proposed, "manual")
        _vol_ratio_pending = False
        _write_ipc_result(
            f"✅ 거래량 기준 완화 적용\n"
            f"{_vol_ratio_current + VOL_RATIO_STEP:.1f} → {_vol_ratio_current:.1f}\n"
            f"쿨다운: 6시간", req_id
        )

    elif cmd[0] == "/volkeep":
        _vol_ratio_pending = False
        _log_recommend(
            current_val     = _vol_ratio_current,
            recommended_val = round(_vol_ratio_current - VOL_RATIO_STEP, 2),
            reason_type     = "no_trade",
            reason_detail   = "12h_no_trade",
            applied         = "N",
        )
        _write_ipc_result(f"✅ 거래량 기준 유지 ({_vol_ratio_current:.1f})", req_id)

    elif cmd[0] == "/volstatus":
        now = time.time()
        no_trade_h = (now - _last_trade_ts) / 3600
        cooldown_left = max(0, _adjust_cooldown - (now - _last_adjust_ts)) / 3600
        _write_ipc_result(
            f"📊 거래량 기준 현황\n"
            f"현재: {_vol_ratio_current:.1f}  기본: {VOL_RATIO_DEFAULT:.1f}  최저: {VOL_RATIO_MIN_CAP:.1f}\n"
            f"무거래 경과: {no_trade_h:.1f}h\n"
            f"6h내 거래: {len(_recent_trades_ts)}건\n"
            f"조정 쿨다운 잔여: {cooldown_left:.1f}h\n"
            f"승인 대기중: {'✅' if _vol_ratio_pending else '없음'}", req_id
        )

    elif cmd[0] == "/sync":
        """/sync       — 잔고 확인 후 적용 목록 보고 (dry-run)
           /sync apply — 실제 적용"""
        dry_run = not (len(cmd) >= 2 and cmd[1].lower() == "apply")
        try:
            h = _upbit_headers()
            r = requests.get(f"{UPBIT_BASE}/accounts", headers=h, timeout=10)
            if r.status_code != 200:
                _write_ipc_result("❌ 잔고 조회 실패", req_id)
                return
            accounts = r.json()

            to_add    = []   # (market, balance, avg_buy)
            to_remove = []   # market

            # 현재 슬롯
            with slots_lock:
                old_slots = set(slots)

            for a in accounts:
                currency = a.get("currency", "")
                if currency == "KRW":
                    continue
                market  = f"KRW-{currency}"
                balance = float(a.get("balance", 0))
                avg_buy = float(a.get("avg_buy_price", 0))

                # 먼지 수량 필터: 평가금액 5,000원 미만이면 무시
                if avg_buy <= 0 or balance * avg_buy < MIN_ORDER_KRW / 4:
                    continue

                to_add.append((market, balance, avg_buy))

            synced_markets = {m for m, _, _ in to_add}
            for market in old_slots:
                if market not in synced_markets:
                    to_remove.append(market)

            # dry-run: 적용 목록만 보고
            if dry_run:
                lines = ["🔍 싱크 미리보기 (적용하려면 /sync apply)"]
                if to_add:
                    lines.append("➕ 추가될 종목:")
                    for m, bal, avg in to_add:
                        val = int(bal * avg)
                        lines.append(f"  {m.replace('KRW-','')}: {bal:.6f}개 @ {avg:,.0f}원 (평가 {val:,}원)")
                if to_remove:
                    lines.append("➖ 제거될 종목:")
                    for m in to_remove:
                        lines.append(f"  {m.replace('KRW-','')}")
                if not to_add and not to_remove:
                    lines.append("변경사항 없음")
                _write_ipc_result("\n".join(lines), req_id)
                return

            # apply: 실제 적용
            for market, balance, avg_buy in to_add:
                c = get_or_create_coin(market)
                c["has_stock"]      = True
                c["buy_price"]      = avg_buy
                c["filled_qty"]     = balance
                c["be_active"]      = False
                c["highest_profit"] = 0.0
                c["buy_time"]       = c.get("buy_time") or time.time()
                with slots_lock:
                    slots.add(market)

            for market in to_remove:
                with slots_lock:
                    slots.discard(market)
                c = coins.get(market)
                if c:
                    c["has_stock"] = False

            lines = ["✅ 동기화 완료"]
            for m, bal, avg in to_add:
                lines.append(f"  {m.replace('KRW-','')}: {bal:.6f}개 @ {avg:,.0f}원")
            for m in to_remove:
                lines.append(f"  {m.replace('KRW-','')} 제거")
            if not to_add and not to_remove:
                lines.append("보유 코인 없음")
            _write_ipc_result("\n".join(lines), req_id)
            _write_status()

        except Exception as e:
            _write_ipc_result(f"❌ 동기화 오류: {e}", req_id)

    elif cmd[0] in ("/why", "/왜"):
        import time as _t
        regime_kor = {"bull": "📈상승장", "neutral": "➖중립장", "bear": "📉하락장"}.get(_market_regime, "?")
        primary_c, secondary_c = get_regime_conditions()
        cond_str = f"기본 RSI≤{primary_c['rsi_max']} 눌림≥{primary_c['drop_min']}% 변동성≥0.8% + (거래량≥1.05 OR 실체≥0.6%)"
        if secondary_c:
            cond_str += f"\n보조 RSI≤{secondary_c['rsi_max']} 눌림≥{secondary_c['drop_min']}%"
        lines = ["🔍 매수 조건 요약", f"━━━━━━━━━━━━━━━━━━━━",
                 f"장세: {regime_kor}  슬롯: {_primary_slots()}+{_secondary_slots()}개 (추세+{TREND_SLOTS})",
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
                if rsi > cond_t["rsi_max"]: return f"RSI {rsi:.0f}→{cond_t['rsi_max']}"
                if ma20 is None or ma20 <= (ma60 or 0): return "MA하락"
                if drop_pct_w < cond_t["drop_min"]: return f"눌림 {drop_pct_w:.1f}→{cond_t['drop_min']}%"
                if vol is None or vol < 0.8: return f"변동성부족 {vol:.2f}%" if vol else "변동성없음"
                if vol_ratio_w < 1.05 and (vol or 0) < 0.6: return f"거래량+실체 모두 부족"
                if _market_regime == "bull" and d_ma20_w is not None and d_ma60_w is not None and d_ma20_w <= d_ma60_w:
                    return "일봉하락추세"
                return None

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
                if vol is None or vol < 0.8:
                    common_fail.append(f"변동성{vol:.1f}%" if vol else "변동성없음")
                elif vol > 8.0:
                    common_fail.append(f"변동성과다{vol:.1f}%")
                elif vol_ratio_w < 1.05 and (vol or 0) < 0.6:
                    common_fail.append(f"거래량{vol_ratio_w:.1f}+실체부족")

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
                    if vol_ratio_w < 1.05 and (vol or 0) < 0.6:
                        hints.append(f"거래량 {vol_ratio_w:.1f}OR실체부족")
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
            markets = get_watch_markets()
            if markets:
                lines.append(f"⏳ 데이터 수집 중 ({len(markets)}개 종목 — 약 5분 후 표시)")
            else:
                lines.append("감시 종목 없음")

        # 추세추종 후보 표시
        trend_list = []
        with slots_lock:
            held = set(slots)
        for m, c in snap.items():
            if m in held: continue
            h = list(c.get("history", []))
            if not h: continue
            price = h[-1]
            volume = list(c.get("vol_history", []))[-1] if c.get("vol_history") else 0
            ok, score = check_trend_signal(m, price, volume)
            if ok:
                trend_list.append((m, int(score)))
        trend_list.sort(key=lambda x: x[1], reverse=True)
        if trend_list:
            lines.append("📈 추세추종 후보")
            for m, s in trend_list[:5]:
                lines.append(f"  {m.replace('KRW-','')}: {s}점")
        else:
            lines.append("📈 추세추종 후보: 없음 (RSI 50~65 + 가격>MA20 대기)")

        _write_ipc_result("\n".join(lines), req_id)

    elif cmd[0] in ("/trend", "/추세"):
        with coins_lock:
            snap = dict(coins)
        with slots_lock:
            held = set(slots)
        trend_list = []
        for m, c in snap.items():
            if m in held: continue
            h = list(c.get("history", []))
            if not h: continue
            price = h[-1]
            volume = list(c.get("vol_history", []))[-1] if c.get("vol_history") else 0
            ok, score = check_trend_signal(m, price, volume)
            if ok:
                trend_list.append((m, int(score)))
        trend_list.sort(key=lambda x: x[1], reverse=True)
        regime_kor = {"bull": "📈상승장", "neutral": "➖중립장", "bear": "📉하락장"}.get(_market_regime, "?")
        lines = [f"📈 추세추종 후보 ({regime_kor})"]
        if trend_list:
            for m, s in trend_list[:10]:
                lines.append(f"  {m.replace('KRW-','')}: {s}점")
        else:
            lines.append("없음 (RSI 50~65 + 가격>MA20 + RSI상승 조건 대기)")
        _write_ipc_result("\n".join(lines), req_id)

    elif cmd[0] in ("/watchlist", "/감시"):
        markets = get_watch_markets()
        with slots_lock:
            held = list(slots)
        lines = [
            f"👁 감시 종목 ({len(markets)}개)",
            f"보유: {', '.join(m.replace('KRW-','') for m in held) or '없음'}",
            "─────────────────",
        ]
        for m in markets:
            c = coins.get(m, {})
            cnt = c.get("real_data_count", 0)
            status = "📦" if m in held else ("✅" if cnt >= REAL_DATA_MIN else f"⏳{cnt}")
            lines.append(f"{status} {m.replace('KRW-','')}")
        _write_ipc_result("\n".join(lines), req_id)

    elif cmd[0] in ("/sell", "/매도"):
        with slots_lock:
            holding = list(slots)
        if not holding:
            _write_ipc_result("⏳ 보유 종목 없음", req_id)
            return
        lines = ["🔴 즉시 매도 실행"]
        for m in holding:
            c = coins.get(m, {})
            qty = c.get("filled_qty", 0)
            if qty > 0:
                (filled, avg), _ = send_order("SELL", m, qty)
                if filled:
                    pnl_pct = (avg - c.get("buy_price", avg)) / c.get("buy_price", avg) * 100 if c.get("buy_price") else 0
                    lines.append(f"✅ {m.replace('KRW-','')}: {pnl_pct:+.2f}% 매도완료")
                    c["has_stock"] = False
                    with slots_lock:
                        slots.discard(m)
                else:
                    lines.append(f"❌ {m.replace('KRW-','')}: 매도 실패")
            else:
                lines.append(f"⚠️ {m.replace('KRW-','')}: 수량 없음")
        _write_ipc_result("\n".join(lines), req_id)


def _send_hourly_report():
    """1시간마다 텔레그램에 현황 보고."""
    import time as _t
    try:
        with slots_lock:
            holding = list(slots)
        regime_kor = {"bull": "📈상승장", "neutral": "➖중립장", "bear": "📉하락장"}.get(_market_regime, "?")
        slot_str = f"{len(holding)}/{MAX_SLOTS + TREND_SLOTS}"
        lines = [
            f"🕐 1시간 보고 ({datetime.now().strftime('%H:%M')})",
            f"━━━━━━━━━━━━━━━━━━━━",
            f"장세: {regime_kor}  슬롯: {slot_str}",
            f"💰 일간: {daily_pnl:+,.0f}원  주간: {weekly_pnl:+,.0f}원",
            f"🗓 월간: {monthly_pnl:+,.0f}원  누적: {total_pnl:+,.0f}원",
        ]

        if holding:
            lines.append("📦 보유 중")
            for m in holding:
                c = coins.get(m, {})
                buy_p = c.get("buy_price", 0)
                h = list(c.get("history", []))
                cur = h[-1] if h else 0
                if cur <= 0:
                    try:
                        cur, _ = get_price_and_volume(m)
                    except Exception:
                        cur = 0
                pnl_pct = (cur - buy_p) / buy_p * 100 if buy_p and cur else 0
                hold_h = (_t.time() - c.get("buy_time", _t.time())) / 3600
                tag = "추세" if c.get("is_trend") else ("보조" if c.get("is_secondary") else "기본")
                lines.append(f"  {m.replace('KRW-','')}: {pnl_pct:+.2f}% ({hold_h:.1f}h, {tag})")
        else:
            lines.append("⏳ 보유 종목 없음")

        # 감시 중 근접 종목 (near) 상위 3개
        snap = dict(coins)
        near_list = []
        with slots_lock:
            held = set(slots)
        for m, c in snap.items():
            if m in held: continue
            h = list(c.get("history", []))
            if not h: continue
            price = h[-1]
            volume = list(c.get("vol_history", []))[-1] if c.get("vol_history") else 0
            open_price = 0
            ok, score = check_buy_score(m, price, volume, open_price)
            if score > 0:
                near_list.append((m, int(score)))
        near_list.sort(key=lambda x: x[1], reverse=True)
        if near_list:
            lines.append("🔍 근접 후보 (역추세)")
            for m, s in near_list[:3]:
                lines.append(f"  {m.replace('KRW-','')}: {s}점")

        # 추세추종 후보 요약
        with slots_lock:
            held2 = set(slots)
        trend_list = []
        for m, c in snap.items():
            if m in held2: continue
            h2 = list(c.get("history", []))
            if not h2: continue
            p2 = h2[-1]
            v2 = list(c.get("vol_history", []))[-1] if c.get("vol_history") else 0
            ok2, sc2 = check_trend_signal(m, p2, v2)
            if ok2:
                trend_list.append((m, int(sc2)))
        trend_list.sort(key=lambda x: x[1], reverse=True)
        if trend_list:
            lines.append("📈 추세후보")
            for m, s in trend_list[:3]:
                lines.append(f"  {m.replace('KRW-','')}: {s}점")
        else:
            lines.append("📈 추세후보: 없음")

        # 매매 정지 상태 표시
        if _trading_paused:
            lines.append("⏹ 현재 매수 정지 중")

        send_msg("\n".join(lines))
    except Exception as e:
        cprint(f"[시간보고 오류] {e}", Fore.YELLOW)


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
_running        = True
_trading_paused = False   # /stop 으로 매수 일시정지 (매도는 계속)

def run_bot():
    global _running, daily_pnl, weekly_pnl, monthly_pnl, total_pnl
    global _last_reset_day, _last_reset_week, _last_reset_month

    load_config()
    _load_trade_state()
    _load_pnl_state()
    # 시작 직후 잔고 자동 싱크 (이전 미등록/매수가0 포지션 즉시 복구)
    global _last_balance_check_ts
    _last_balance_check_ts = 0.0

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

    last_ticker_ts  = 0.0
    last_report_ts  = 0.0   # 1시간 주기 텔레그램 보고

    while _running:
        try:
            now = time.time()

            # 일간/주간/월간 초기화
            global daily_pnl, weekly_pnl, monthly_pnl
            global _last_reset_day, _last_reset_week, _last_reset_month
            today = date.today()
            if _last_reset_day != today:
                _last_reset_day = today
                daily_pnl = 0.0
                for c in coins.values(): c["daily_pnl"] = 0.0
                _save_pnl_state()
            this_week = today.isocalendar()[:2]
            if _last_reset_week != this_week:
                _last_reset_week = this_week
                weekly_pnl = 0.0
                _save_pnl_state()
            this_month = (today.year, today.month)
            if _last_reset_month != this_month:
                _last_reset_month = this_month
                monthly_pnl = 0.0
                _save_pnl_state()

            # 미체결 주문 재확인 (uuid 기반)
            _check_pending_orders()

            # 잔고 자동 폴링 — 봇이 모르는 보유 코인 자동 등록 (5분마다)
            _auto_sync_holdings()

            # 장세 감지 (1시간마다 자동 갱신)
            detect_market_regime()

            # vol_ratio 자동 조정 체크
            check_vol_ratio_adjust()

            # IPC 상태 주기적 전송
            _write_status()

            # 1시간마다 텔레그램 보고
            if now - last_report_ts >= 3600:
                last_report_ts = now
                _send_hourly_report()

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

            if _trading_paused:
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

            # 추세추종 신호 수집
            trend_signals = []
            for market, td in tickers.items():
                with slots_lock:
                    if market in slots: continue
                price  = float(td.get("trade_price", 0))
                volume = td.get("acc_trade_volume_24h", 0)
                if not market or price <= 0: continue
                c = get_or_create_coin(market)
                ok, score = check_trend_signal(market, price, volume)
                if ok:
                    trend_signals.append((market, price, score))

            # 추세추종 매수 (1슬롯, 역추세와 종목 겹치지 않게)
            trend_signals.sort(key=lambda x: x[2], reverse=True)
            trend_used = sum(1 for m in slots if coins.get(m, {}).get("is_trend", False))
            with slots_lock:
                held = set(slots)
            for market, price, score in trend_signals:
                if trend_used >= _trend_slots(): break
                if market in held: continue   # 역추세 보유 종목 제외
                real_score = int(score)
                coins.get(market, {})["is_trend"] = True
                coins.get(market, {})["is_secondary"] = False
                do_buy(market, price, f"추세추종:{real_score}점")
                trend_used += 1
                with slots_lock:
                    held = set(slots)

            # 점수 순 정렬 → 역추세 슬롯 분리 매수
            signals.sort(key=lambda x: x[2], reverse=True)
            pri_used = sum(1 for m in slots if not coins.get(m, {}).get("is_secondary", False) and not coins.get(m, {}).get("is_trend", False))
            sec_used = sum(1 for m in slots if coins.get(m, {}).get("is_secondary", False))
            with slots_lock:
                held = set(slots)
            for market, price, score in signals:
                with slots_lock:
                    if len(slots) >= MAX_SLOTS + TREND_SLOTS: break
                if market in held: continue   # 추세추종 보유 종목 제외
                is_sec = (score % 1) > 0.005
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
                coins.get(market, {})["is_trend"] = False
                do_buy(market, price, tag)

        except Exception as e:
            cprint(f"[메인 루프 오류] {e}\n{traceback.format_exc()}", Fore.RED)
            time.sleep(5)

if __name__ == "__main__":
    run_bot()
