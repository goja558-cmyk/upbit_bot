"""
==============================================================
  업비트 코인 자동매매 봇 v1.0
  KIS 봇 v10.9 구조 기반 → 업비트 API로 교체
  마스터 리스트 1~7순위 수정 반영
  
  변경 이력:
  - KIS API → 업비트 REST API 교체
    (get_token, kis_headers, send_order, confirm_order,
     get_price_and_volume, get_balance_krw, cancel_order)
  - 장시간/점심/웜업 로직 제거 (24시간 운영)
  - 거래세 제거 (코인은 없음)
  - 호가 단위(tick) → 코인 소수점 처리로 교체
  - 마스터 리스트 1~7순위 버그 수정 전부 반영

  사용 전 준비:
  1. pip install requests pyyaml numpy psutil colorama
  2. upbit_cfg.yaml 파일 생성 (아래 형식 참고)
  3. 업비트 Open API 키 발급: https://upbit.com/mypage/open_api_management

  upbit_cfg.yaml 형식:
    access_key: "여기에_발급받은_ACCESS_KEY"
    secret_key: "여기에_발급받은_SECRET_KEY"
    telegram_token: "여기에_텔레그램_봇_토큰"
    chat_id: "여기에_내_채팅_ID"
==============================================================
"""

BOT_VERSION  = "1.0"    # 업데이트 시 이 숫자를 올려주세요
BOT_NAME     = "업비트 코인봇"

import sys, os, time, json, csv, requests, yaml, shutil, traceback
import numpy as np
import psutil
import hmac, hashlib, uuid
from datetime import datetime, date, timedelta
from collections import deque
from urllib.parse import urlencode

try:
    import jwt as pyjwt          # pip install PyJWT
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
    prefix = (Style.BRIGHT if bright else "") + color if COLOR_OK else ""
    print(f"{prefix}{text}{Style.RESET_ALL if COLOR_OK else ''}")

# ============================================================
# [1] 사용자 설정
# ============================================================

# 텔레그램 (upbit_cfg.yaml 에서 자동 설정됨)
TELEGRAM_TOKEN = ""
CHAT_ID        = ""

# ── 거래 종목 ─────────────────────────────────────────────────
# 업비트 마켓 코드: KRW-BTC, KRW-ETH, KRW-XRP 등
MARKET_CODE    = "KRW-BTC"
FEE_RATE       = 0.0005    # 업비트 수수료 0.05%

# ── 매매 예산 ────────────────────────────────────────────────
ORDER_BUDGET_KRW   = 10_000   # 1회 주문에 쓸 최대 금액 (원) — 테스트용 1만원
ORDER_BUDGET_RATIO = 0.98     # 예산의 98%까지 사용 (업비트 최소주문 5,000원)

# ── 손실 한도 ────────────────────────────────────────────────
MAX_DAILY_LOSS_KRW  = -5_000    # 하루 최대 손실 (원)
DAILY_LOSS_BASE_KRW = 50_000    # 손실 한도 계산 기준금액
MAX_DAILY_LOSS_PCT  = -2.0      # 기준 대비 최대 손실 %
WEEKLY_LOSS_LIMIT_KRW = -30_000 # 주간 최대 손실 (원)

# ── 매매 횟수 제한 ───────────────────────────────────────────
MAX_TRADE_COUNT = 20    # 하루 최대 매매 횟수 (24시간이라 주식보다 많이 설정)

# ── 전략 수치 ────────────────────────────────────────────────
BOT_TARGET      =  1.0   # 익절 목표 (%) — 코인은 수수료 0.1% 감안해서 높게
BOT_MAX_LOSS    = -0.8   # 손절 기준 (%) — 타이트하게
BOT_DROP        =  0.5   # 눌림 기준 (%) — MA20 대비 이만큼 빠져야 매수 고려
BOT_TRAIL_START =  0.5   # 트레일링 스탑 시작 수익률 (%)
BOT_TRAIL_GAP   =  0.3   # 트레일링 스탑 간격 (%)
BOT_BE_TRIGGER  =  0.3   # 본절 보호 발동 수익률 (%)
BOT_RSI_BUY     =  38    # RSI 매수 기준 — 코인은 변동성 커서 주식보다 약간 높게
BOT_RSI_PERIOD  =  14    # RSI 계산 기간

# ── VWAP / 거래량 필터 ───────────────────────────────────────
VWAP_FILTER   = True
VOL_RATIO_MIN = 1.0

# ── 변동성 필터 ──────────────────────────────────────────────
VOL_WINDOW_SEC = 300
VOL_MIN_PCT    = 0.3    # 코인은 최소 변동성 더 높게 (주식보다 활발)
VOL_MAX_PCT    = 5.0    # 코인은 최대 변동성도 더 넓게

# ── 슬리피지 / 쿨다운 ────────────────────────────────────────
MAX_SLIPPAGE_PCT = 0.5
COOLDOWN_SEC     = 120   # 코인은 24시간이라 쿨다운 짧게

# ── 포지션 타임아웃 ──────────────────────────────────────────
POS_TIMEOUT_MIN      = 60    # 코인은 횡보 길 수 있어 넉넉하게
POS_TIMEOUT_LOSS_PCT = 0.3

# ── 연패 시 RSI 자동 강화 ────────────────────────────────────
RSI_TIGHTEN_STEP = 3
RSI_BUY_DEFAULT  = BOT_RSI_BUY
RSI_BUY_MIN      = 20

# ── 연승 시 예산 자동 확대 ───────────────────────────────────
WIN_STREAK_STEP    = 3
WIN_BUDGET_ADD_PCT = 10
WIN_BUDGET_MAX_KRW = 100_000

# ── 알림 설정 ────────────────────────────────────────────────
# 코인은 24시간이라 야간 차단 없음 — 필요하면 조정
NIGHT_SILENCE_START   = (2,  0)   # 새벽 2시 이후
NIGHT_SILENCE_END     = (7,  0)   # 아침 7시까지 (critical 외 차단)
NONMARKET_ALERT_HOURS = [9, 21]   # 정기 상태 알림 시각

# ── 기타 ─────────────────────────────────────────────────────
LOOP_INTERVAL    = 5     # 5초 간격 (업비트 API 분당 600회 제한)
HISTORY_PREFILL  = 70
MAX_API_FAIL     = 5
WARMUP_MINUTES   = 0     # 코인은 웜업 불필요
BACKUP_KEEP_DAYS = 7
CONSEC_LOSS_ALERT = 3

SET_ALLOWED_KEYS = {
    "target", "max_loss", "drop",
    "trail_start", "trail_gap", "be_trigger", "rsi_buy",
    "trade_count", "cooldown", "vol_min", "vol_max",
    "slippage", "timeout_min", "vwap_filter",
}

# ============================================================
# [2] 경로 설정
# ============================================================
BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
LOG_FILE       = os.path.join(BASE_DIR, "coin_trade_log.csv")
INDICATOR_LOG_FILE = os.path.join(BASE_DIR, "coin_indicator_log.csv")
STATE_FILE     = os.path.join(BASE_DIR, "coin_state_v1.json")
CFG_FILE       = os.path.join(BASE_DIR, "upbit_cfg.yaml")
BACKUP_DIR     = os.path.join(BASE_DIR, "backups")
CHANGELOG_DIR  = os.path.join(BASE_DIR, "changelog")
SHARED_DIR     = os.path.join(BASE_DIR, "shared")   # 두 봇 공유 폴더
for d in [BACKUP_DIR, CHANGELOG_DIR, SHARED_DIR]:
    os.makedirs(d, exist_ok=True)
# 공유 로그 경로 (통합 리포트용)
KIS_SHARED_LOG  = os.path.join(SHARED_DIR, "kis_trade_log.csv")
COIN_SHARED_LOG = os.path.join(SHARED_DIR, "upbit_trade_log.csv")

# ============================================================
# [3] YAML 설정 읽기
# ============================================================
_cfg = {}

def load_config():
    global _cfg, TELEGRAM_TOKEN, CHAT_ID
    if not os.path.exists(CFG_FILE):
        print(f"❌ {CFG_FILE} 파일이 없습니다.")
        print("upbit_cfg.yaml 파일을 만들고 access_key, secret_key, telegram_token, chat_id 를 입력하세요.")
        sys.exit(1)
    with open(CFG_FILE, encoding="utf-8") as f:
        _cfg = yaml.safe_load(f) or {}
    TELEGRAM_TOKEN = _cfg.get("telegram_token", "")
    CHAT_ID        = str(_cfg.get("chat_id", ""))
    if not JWT_OK:
        print("❌ PyJWT 라이브러리가 없습니다. pip install PyJWT")
        sys.exit(1)
    cprint("✅ 설정 파일 로드 완료", Fore.GREEN)

# ============================================================
# [4] 전역 상태 변수
# ============================================================
bot = {
    "has_stock":   False,
    "buy_price":   0,
    "filled_qty":  0.0,      # 코인은 소수점 수량
    "be_active":   False,
    "prev_rsi":    None,
    "prev_rsi2":   None,
    "target":      BOT_TARGET,
    "max_loss":    BOT_MAX_LOSS,
    "drop":        BOT_DROP,
    "trail_start": BOT_TRAIL_START,
    "trail_gap":   BOT_TRAIL_GAP,
    "be_trigger":  BOT_BE_TRIGGER,
    "rsi_buy":     BOT_RSI_BUY,
    "running":     False,
    "_last_rsi":   None,
    "_ma20":       None,
    "_ma60":       None,
}

price_history   = deque(maxlen=300)
volume_history  = deque(maxlen=300)
timed_prices    = deque(maxlen=3600)
_price_trough_5m = deque(maxlen=5)

daily_pnl_krw    = 0
weekly_pnl_krw   = 0
trade_count      = 0
highest_profit   = 0.0
win_count        = 0
loss_count       = 0
consecutive_loss = 0
consecutive_win  = 0
_last_sell_time  = 0.0
_buy_time        = 0.0
_order_pending   = False   # 중복 주문 방지
_weekly_stop     = False
_last_price      = 0
_vwap_value      = None
_vwap_sum_pv     = 0.0
_vwap_sum_v      = 0.0
_daily_report_sent = False
_real_data_count   = 0
REAL_DATA_MIN      = 60    # MA60 계산에 최소 60개 필요

_api_fail_count    = 0
_api_fail_first_ts = 0.0
last_update_id     = 0
_last_tg_poll      = 0.0
_last_heartbeat_hour = -1
_last_backup_date  = ""
_pause_alert_sent  = ""
_dynamic_mode      = True
_last_status_line_ts = 0.0
_last_detail_log_ts  = 0.0
_indicator_log_counter = 0
INDICATOR_LOG_INTERVAL = 3  # 코인은 루프 간격이 5초라 3번마다 = 15초마다 기록
_tg_fail_count     = 0
TG_FAIL_ALERT_THRESHOLD = 10

# 쿨다운/최대거래 알림 플래그
_cooldown_alert_sent    = False
_max_trade_alert_sent   = False

# ============================================================
# [5] 알림 / 유틸
# ============================================================
def is_night_silence():
    h = datetime.now().hour
    s, e = NIGHT_SILENCE_START[0], NIGHT_SILENCE_END[0]
    if s > e:
        return h >= s or h < e
    return s <= h < e

def send_msg(text, level="normal", keyboard=None, force=False):
    if is_night_silence() and level != "critical" and not force:
        return
    color_map = {"critical": Fore.RED, "normal": Fore.CYAN, "silent": Fore.WHITE}
    cprint(f"\n📡 [{level.upper()}] {text}", color_map.get(level, Fore.WHITE),
           bright=(level == "critical"))
    try:
        payload = {
            "chat_id": CHAT_ID,
            "text": text,
            "disable_notification": (level == "silent"),
        }
        if keyboard:
            payload["reply_markup"] = {"inline_keyboard": keyboard}
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json=payload, timeout=5
        )
    except Exception as e:
        cprint(f"❌ 텔레그램 전송 오류: {e}", Fore.RED)

KB_MAIN = [
    [
        {"text": "📊 상태",   "callback_data": "/status"},
        {"text": "📋 내역",   "callback_data": "/log"},
        {"text": "💰 잔고",   "callback_data": "/balance"},
    ],
    [
        {"text": "▶️ 시작",   "callback_data": "/start"},
        {"text": "⏸️ 정지",   "callback_data": "/stop"},
        {"text": "📈 리포트", "callback_data": "/report"},
    ],
    [
        {"text": "❓ 왜 안사?", "callback_data": "/why"},
        {"text": "🔄 설정",   "callback_data": "/reload"},
        {"text": "❓ 도움말", "callback_data": "/help"},
    ],
]

def log_change(category, detail):
    today    = str(date.today())
    filepath = os.path.join(CHANGELOG_DIR, f"changelog_{today}.txt")
    now_str  = datetime.now().strftime("%H:%M:%S")
    with open(filepath, "a", encoding="utf-8") as f:
        f.write(f"[{now_str}] [{category}] {detail}\n")

# ============================================================
# [6] 업비트 API
# ============================================================
UPBIT_BASE = "https://api.upbit.com/v1"

def _upbit_headers(query_params=None):
    """업비트 JWT 인증 헤더 생성"""
    access_key = _cfg.get("access_key", "")
    secret_key = _cfg.get("secret_key", "")
    payload = {
        "access_key": access_key,
        "nonce": str(uuid.uuid4()),
    }
    if query_params:
        query_string = urlencode(query_params).encode()
        m = hashlib.sha512()
        m.update(query_string)
        query_hash = m.hexdigest()
        payload["query_hash"] = query_hash
        payload["query_hash_alg"] = "SHA512"
    token = pyjwt.encode(payload, secret_key, algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}

def get_token():
    """업비트는 요청마다 JWT 생성 — 별도 토큰 발급 불필요"""
    return "upbit_jwt"

def get_price_and_volume(market=None):
    """현재가 + 거래량 조회. 반환: (price, volume) 또는 (None, None)"""
    if market is None:
        market = MARKET_CODE
    try:
        res = requests.get(
            f"{UPBIT_BASE}/ticker",
            params={"markets": market},
            timeout=5
        )
        if res.status_code == 200:
            data = res.json()[0]
            price  = float(data["trade_price"])
            volume = float(data["acc_trade_volume_24h"])
            return price, volume
    except Exception as e:
        cprint(f"[현재가 조회 오류] {e}", Fore.YELLOW)
    return None, None

def get_price(market=None):
    p, _ = get_price_and_volume(market)
    return p

def get_balance_krw():
    """원화 잔고 조회"""
    try:
        h = _upbit_headers()
        res = requests.get(f"{UPBIT_BASE}/accounts", headers=h, timeout=5)
        if res.status_code == 200:
            for acc in res.json():
                if acc["currency"] == "KRW":
                    return float(acc["balance"])
    except Exception as e:
        cprint(f"[잔고 조회 오류] {e}", Fore.YELLOW)
    return 0

def get_coin_balance(currency=None):
    """보유 코인 잔고 조회"""
    if currency is None:
        currency = MARKET_CODE.replace("KRW-", "")
    try:
        h = _upbit_headers()
        res = requests.get(f"{UPBIT_BASE}/accounts", headers=h, timeout=5)
        if res.status_code == 200:
            for acc in res.json():
                if acc["currency"] == currency:
                    return float(acc["balance"])
    except Exception as e:
        cprint(f"[코인 잔고 조회 오류] {e}", Fore.YELLOW)
    return 0.0

def send_order(side, market, qty_or_price, order_type="price"):
    """
    매수: side="BUY", order_type="price" → qty_or_price = 주문금액(KRW)
    매도: side="SELL", order_type="volume" → qty_or_price = 수량
    반환: (체결수량, 평균체결가) 또는 (0, 0)
    """
    try:
        if side == "BUY":
            body = {
                "market": market,
                "side": "bid",
                "price": str(int(qty_or_price)),   # 시장가 매수는 금액 지정
                "ord_type": "price",
            }
        else:
            body = {
                "market": market,
                "side": "ask",
                "volume": str(qty_or_price),        # 시장가 매도는 수량 지정
                "ord_type": "market",
            }
        query = urlencode(body)
        h = _upbit_headers(body)
        h["Content-Type"] = "application/json"
        res = requests.post(f"{UPBIT_BASE}/orders", headers=h, json=body, timeout=10)
        if res.status_code in (200, 201):
            data = res.json()
            order_uuid = data.get("uuid", "")
            return confirm_order(order_uuid)
        else:
            cprint(f"[주문 오류] {res.status_code}: {res.text}", Fore.RED)
    except Exception as e:
        cprint(f"[주문 예외] {e}", Fore.RED)
    return 0, 0

def confirm_order(order_uuid, retry=8):
    """주문 체결 확인. 반환: (체결수량, 평균체결가)"""
    if not order_uuid:
        return 0, 0
    for attempt in range(retry):
        try:
            params = {"uuid": order_uuid}
            h = _upbit_headers(params)
            res = requests.get(f"{UPBIT_BASE}/order", headers=h, params=params, timeout=5)
            if res.status_code == 200:
                data = res.json()
                state = data.get("state", "")
                if state == "done":
                    filled = float(data.get("executed_volume", 0))
                    # 평균체결가 = 총체결금액 / 총체결수량
                    trades = data.get("trades", [])
                    if trades:
                        total_price  = sum(float(t["price"]) * float(t["volume"]) for t in trades)
                        total_volume = sum(float(t["volume"]) for t in trades)
                        avg_p = total_price / total_volume if total_volume > 0 else 0
                    else:
                        avg_p = float(data.get("price", 0)) or float(data.get("avg_price", 0))
                    return filled, avg_p
                elif state in ("wait", "watch"):
                    time.sleep(1.5 if attempt < 3 else 2.5)
                    continue
                else:
                    cprint(f"[주문상태 이상] {state}", Fore.YELLOW)
                    return 0, 0
        except Exception as e:
            cprint(f"[체결확인 오류 {attempt+1}회] {e}", Fore.YELLOW)
            time.sleep(2)
    send_msg("⚠️ 주문 체결 확인 안 됨. 업비트 앱에서 확인하세요.", level="critical")
    return 0, 0

def cancel_order(order_uuid):
    """미체결 주문 취소"""
    try:
        params = {"uuid": order_uuid}
        h = _upbit_headers(params)
        res = requests.delete(f"{UPBIT_BASE}/order", headers=h, params=params, timeout=5)
        return res.status_code == 200
    except Exception as e:
        cprint(f"[주문취소 오류] {e}", Fore.YELLOW)
    return False

def get_ohlcv(market=None, count=200, unit="minutes", interval=1):
    """분봉 OHLCV 조회 — RSI/MA 계산용"""
    if market is None:
        market = MARKET_CODE
    try:
        url = f"{UPBIT_BASE}/candles/minutes/{interval}"
        res = requests.get(
            url,
            params={"market": market, "count": count},
            timeout=10
        )
        if res.status_code == 200:
            data = res.json()
            # 최신순 → 오래된순 정렬
            data = sorted(data, key=lambda x: x["candle_date_time_utc"])
            return [float(c["trade_price"]) for c in data]
    except Exception as e:
        cprint(f"[OHLCV 조회 오류] {e}", Fore.YELLOW)
    return []

# ============================================================
# [7] 지표 계산
# ============================================================
def calc_rsi(prices, period=14):
    if len(prices) < period + 1:
        return None
    p      = np.array(list(prices)[-(period + 100):])
    deltas = np.diff(p)
    gains  = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_g  = np.mean(gains[:period])
    avg_l  = np.mean(losses[:period])
    for i in range(period, len(gains)):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return round(100 - (100 / (1 + rs)), 2)

def calc_ma(prices, period):
    if len(prices) < period:
        return None
    return float(np.mean(list(prices)[-period:]))

def calc_vol_pct(timed_buf, window_sec=300):
    now = time.time()
    pts = [p for t, p in timed_buf if now - t <= window_sec]
    if len(pts) < 2:
        return None
    return (max(pts) - min(pts)) / min(pts) * 100

def calc_vwap(prices_vol):
    """VWAP 계산: prices_vol = [(price, volume), ...]"""
    if not prices_vol:
        return None
    total_pv = sum(p * v for p, v in prices_vol)
    total_v  = sum(v for _, v in prices_vol)
    return total_pv / total_v if total_v > 0 else None

def get_atr(prices, period=14):
    if len(prices) < period + 1:
        return None
    p    = list(prices)
    trs  = [abs(p[i] - p[i-1]) for i in range(1, len(p))]
    return float(np.mean(trs[-period:]))

def calc_vol_ratio(volume_history):
    if len(volume_history) < 10:
        return None
    recent   = list(volume_history)[-5:]
    baseline = list(volume_history)[-30:-5] if len(volume_history) >= 30 else list(volume_history)[:-5]
    if not baseline:
        return None
    return float(np.mean(recent)) / float(np.mean(baseline))

def net_diff_krw(buy_price, sell_price, qty):
    """수수료 포함 실현손익 계산"""
    gross  = (sell_price - buy_price) * qty
    fee    = (buy_price + sell_price) * qty * FEE_RATE
    return gross - fee

# ============================================================
# [8] 상태 저장 / 불러오기
# ============================================================
def save_state():
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        from datetime import datetime as _dt
        now = _dt.now()
        json.dump({
            "has_stock":           bot["has_stock"],
            "buy_price":           bot["buy_price"],
            "filled_qty":          bot["filled_qty"],
            "be_active":           bot["be_active"],
            "prev_rsi":            bot["prev_rsi"],
            "prev_rsi2":           bot["prev_rsi2"],
            "buy_time":            _buy_time,
            "daily_pnl_krw":       daily_pnl_krw,
            "weekly_pnl_krw":      weekly_pnl_krw,
            "trade_count":         trade_count,
            "highest_profit":      highest_profit,
            "win_count":           win_count,
            "loss_count":          loss_count,
            "consecutive_loss":    consecutive_loss,
            "consecutive_win":     consecutive_win,
            "_last_sell_time":     _last_sell_time,
            "DAILY_LOSS_BASE_KRW": DAILY_LOSS_BASE_KRW,
            "ORDER_BUDGET_KRW":    ORDER_BUDGET_KRW,
            "_weekly_stop":        _weekly_stop,
            "saved_week":          [now.year, now.isocalendar()[1]],
            "market_code":         MARKET_CODE,
            "target":      bot["target"],
            "max_loss":    bot["max_loss"],
            "drop":        bot["drop"],
            "trail_start": bot["trail_start"],
            "trail_gap":   bot["trail_gap"],
            "be_trigger":  bot["be_trigger"],
            "rsi_buy":     bot["rsi_buy"],
            "date":        str(date.today()),
        }, f, ensure_ascii=False, indent=2)

def load_state():
    global daily_pnl_krw, weekly_pnl_krw, trade_count, highest_profit, _last_sell_time
    global DAILY_LOSS_BASE_KRW, ORDER_BUDGET_KRW
    global win_count, loss_count, consecutive_loss, consecutive_win
    global _buy_time, _weekly_stop, MARKET_CODE
    if not os.path.exists(STATE_FILE):
        return
    with open(STATE_FILE, encoding="utf-8") as f:
        s = json.load(f)
    bot.update({
        "target":      s.get("target",      bot["target"]),
        "max_loss":    s.get("max_loss",     bot["max_loss"]),
        "drop":        s.get("drop",         bot["drop"]),
        "trail_start": s.get("trail_start",  bot["trail_start"]),
        "trail_gap":   s.get("trail_gap",    bot["trail_gap"]),
        "be_trigger":  s.get("be_trigger",   bot["be_trigger"]),
        "rsi_buy":     s.get("rsi_buy",      bot["rsi_buy"]),
    })
    DAILY_LOSS_BASE_KRW = s.get("DAILY_LOSS_BASE_KRW", DAILY_LOSS_BASE_KRW)
    ORDER_BUDGET_KRW    = s.get("ORDER_BUDGET_KRW",    ORDER_BUDGET_KRW)
    _weekly_stop        = s.get("_weekly_stop",        False)

    # weekly_pnl_krw: 같은 주(week)일 때만 복원
    saved_week = s.get("saved_week")
    from datetime import datetime as _dt
    now = _dt.now()
    current_week = (now.year, now.isocalendar()[1])
    if saved_week and tuple(saved_week) == current_week:
        weekly_pnl_krw = s.get("weekly_pnl_krw", 0)

    if s.get("date") != str(date.today()):
        return
    bot.update({
        "has_stock":  s.get("has_stock",  False),
        "buy_price":  s.get("buy_price",  0),
        "filled_qty": s.get("filled_qty", 0.0),
        "be_active":  s.get("be_active",  False),
        "prev_rsi":   s.get("prev_rsi",   None),
        "prev_rsi2":  s.get("prev_rsi2",  None),
    })
    _buy_time        = s.get("buy_time",        0.0)
    daily_pnl_krw    = s.get("daily_pnl_krw",   0)
    trade_count      = s.get("trade_count",      0)
    highest_profit   = s.get("highest_profit",   0.0)
    win_count        = s.get("win_count",         0)
    loss_count       = s.get("loss_count",        0)
    consecutive_loss = s.get("consecutive_loss",  0)
    consecutive_win  = s.get("consecutive_win",   0)
    _last_sell_time  = s.get("_last_sell_time",   0.0)

# ============================================================
# [9] 로그 기록
# ============================================================
def log_trade(side, price, qty, pnl_krw=0, rsi=None, vwap=None, ma20=None, ma60=None,
              reason="", vol_pct=None, vol_ratio=None, drop_pct=None,
              divergence_ok=None, pos_hold_time=None, buy_price_ref=None):
    header = ["datetime", "market", "side", "price", "qty", "pnl_krw",
              "daily_pnl_krw", "rsi", "vwap", "ma20", "ma60", "reason",
              "vol_pct", "vol_ratio", "drop_pct", "divergence_ok",
              "pos_hold_time", "buy_price_ref"]
    row_data = [
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        MARKET_CODE, side,
        round(price, 4), round(float(qty), 8),
        round(pnl_krw, 0), round(daily_pnl_krw, 0),
        round(rsi,  2)    if rsi       is not None else "",
        round(vwap, 2)    if vwap      is not None else "",
        round(ma20, 2)    if ma20      is not None else "",
        round(ma60, 2)    if ma60      is not None else "",
        reason,
        round(vol_pct,   4) if vol_pct   is not None else "",
        round(vol_ratio, 2) if vol_ratio  is not None else "",
        round(drop_pct,  4) if drop_pct   is not None else "",
        int(divergence_ok)  if divergence_ok is not None else "",
        round(pos_hold_time, 1) if pos_hold_time is not None else "",
        round(buy_price_ref, 4) if buy_price_ref is not None else "",
    ]
    # 메인 로그
    write_header = not os.path.exists(LOG_FILE)
    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(header)
        w.writerow(row_data)
    # shared 폴더 동기화 (통합 리포트용)
    try:
        write_header2 = not os.path.exists(COIN_SHARED_LOG)
        with open(COIN_SHARED_LOG, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if write_header2:
                w.writerow(header)
            w.writerow(row_data)
    except Exception as e:
        cprint(f"[shared 로그 오류] {e}", Fore.YELLOW)

def log_indicator(price, rsi, ma20, ma60, vwap, vol_pct, vol_ratio, drop_pct):
    global _indicator_log_counter
    _indicator_log_counter += 1
    if _indicator_log_counter % INDICATOR_LOG_INTERVAL != 0:
        return
    write_header = not os.path.exists(INDICATOR_LOG_FILE)
    try:
        with open(INDICATOR_LOG_FILE, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if write_header:
                w.writerow(["datetime", "market", "price", "rsi", "ma20", "ma60",
                            "vwap", "vol_pct", "vol_ratio", "drop_pct"])
            w.writerow([
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                MARKET_CODE, round(price, 4),
                round(rsi,  2)    if rsi       is not None else "",
                round(ma20, 2)    if ma20      is not None else "",
                round(ma60, 2)    if ma60      is not None else "",
                round(vwap, 2)    if vwap      is not None else "",
                round(vol_pct,  4) if vol_pct  is not None else "",
                round(vol_ratio,2) if vol_ratio is not None else "",
                round(drop_pct, 4) if drop_pct is not None else "",
            ])
    except Exception as e:
        cprint(f"[indicator_log 오류] {e}", Fore.YELLOW)

# ============================================================
# [10] 매수 / 매도 실행
# ============================================================
def calc_order_qty_krw(price):
    """주문 금액 계산 (코인은 금액으로 주문)"""
    balance = get_balance_krw()
    if balance == 0:
        cprint("[잔고 조회] 잔고 0 또는 API 실패 — 매수 건너뜀", Fore.YELLOW)
        return 0
    usable = min(ORDER_BUDGET_KRW, balance) * ORDER_BUDGET_RATIO
    return int(usable) if usable >= 5000 else 0   # 업비트 최소주문 5,000원

def do_buy(price, reason, retry=2):
    """시장가 매수"""
    global _order_pending, _buy_time
    if _order_pending:
        cprint("[중복 주문 방지] 이미 주문 진행 중입니다.", Fore.YELLOW)
        return False
    _order_pending = True
    try:
        order_krw = calc_order_qty_krw(price)
        if order_krw < 5000:
            send_msg(
                f"ℹ️ 매수 신호! 잔고 부족으로 건너뜀\n"
                f"현재가: {price:,.0f}원 / 예산: {ORDER_BUDGET_KRW:,.0f}원",
                level="silent"
            )
            return False

        for attempt in range(retry):
            filled, avg_p = send_order("BUY", MARKET_CODE, order_krw)
            if filled > 0 and avg_p > 0:
                actual_buy = avg_p
                slippage   = abs(actual_buy - price) / price * 100
                _buy_time  = time.time()

                bot.update({
                    "has_stock": True,
                    "buy_price": actual_buy,
                    "filled_qty": filled,
                    "be_active": False,
                })

                if slippage > MAX_SLIPPAGE_PCT:
                    send_msg(
                        f"⚠️ 슬리피지 초과 ({slippage:.2f}%)\n→ 즉시 매도합니다.",
                        level="critical"
                    )
                    sell_ok = do_sell(actual_buy, "슬리피지 초과")
                    if not sell_ok:
                        send_msg(
                            f"🚨 슬리피지 초과 후 즉시 매도 실패!\n"
                            f"→ 업비트 앱에서 {filled:.8f} {MARKET_CODE} 직접 매도해 주세요.",
                            level="critical"
                        )
                    return False

                target_krw   = actual_buy * (1 + bot["target"]  / 100)
                stoploss_krw = actual_buy * (1 + bot["max_loss"] / 100)

                log_change("매수", f"{MARKET_CODE} {actual_buy:,.2f}원 × {filled:.8f}  [{reason}]")
                log_trade("BUY", actual_buy, filled,
                          rsi=bot.get("_last_rsi"), vwap=_vwap_value,
                          ma20=bot.get("_ma20"), ma60=bot.get("_ma60"),
                          reason=reason, buy_price_ref=actual_buy)
                send_msg(
                    f"🛒 매수 완료! [{reason}]\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"종목   : {MARKET_CODE}\n"
                    f"가격   : {actual_buy:,.2f}원\n"
                    f"수량   : {filled:.8f}\n"
                    f"투자금 : {actual_buy * filled:,.0f}원\n"
                    f"─────────────────\n"
                    f"🎯 목표가 : {target_krw:,.2f}원  (+{bot['target']}%)\n"
                    f"🔻 손절가 : {stoploss_krw:,.2f}원  ({bot['max_loss']}%)\n"
                    f"⏱️ {POS_TIMEOUT_MIN}분 후 미결 시 자동 청산",
                    level="critical"
                )
                save_state()
                return True
            time.sleep(2)

        send_msg(
            f"🚨 매수 실패! [{reason}]\n→ 업비트 앱에서 확인해 주세요.\n현재가: {price:,.2f}원",
            level="critical"
        )
        return False
    finally:
        _order_pending = False

def do_sell(price, reason, retry=2):
    global daily_pnl_krw, weekly_pnl_krw, trade_count, highest_profit, _last_sell_time
    global consecutive_loss, consecutive_win, win_count, loss_count, _buy_time
    global ORDER_BUDGET_KRW, _daily_report_sent, _weekly_stop
    rsi  = bot.get("_last_rsi")
    vwap = _vwap_value
    ma20 = bot.get("_ma20")
    ma60 = bot.get("_ma60")
    pos_hold = (time.time() - _buy_time) / 60 if _buy_time > 0 else None

    for attempt in range(retry):
        filled, avg_p = send_order("SELL", MARKET_CODE, bot["filled_qty"])
        if filled > 0:
            actual_sell    = avg_p if avg_p > 0 else price
            pnl_krw        = net_diff_krw(bot["buy_price"], actual_sell, filled)
            daily_pnl_krw += pnl_krw
            weekly_pnl_krw += pnl_krw

            if pnl_krw >= 0:
                win_count += 1
                consecutive_loss = 0
                consecutive_win  += 1
                if bot["rsi_buy"] < RSI_BUY_DEFAULT:
                    bot["rsi_buy"] = RSI_BUY_DEFAULT
                    send_msg(f"✅ 연패 해소! RSI 기준 복원 → {RSI_BUY_DEFAULT}", level="normal")
                if consecutive_win > 0 and consecutive_win % WIN_STREAK_STEP == 0:
                    old_budget = ORDER_BUDGET_KRW
                    ORDER_BUDGET_KRW = min(
                        int(ORDER_BUDGET_KRW * (1 + WIN_BUDGET_ADD_PCT / 100)),
                        WIN_BUDGET_MAX_KRW
                    )
                    if ORDER_BUDGET_KRW > old_budget:
                        send_msg(
                            f"📈 {consecutive_win}연승 달성!\n"
                            f"→ 주문 예산 자동 확대: {old_budget:,.0f}원 → {ORDER_BUDGET_KRW:,.0f}원",
                            level="normal"
                        )
                        save_state()
            else:
                loss_count += 1
                consecutive_loss += 1
                consecutive_win  = 0

            # 주간 손실 한도 체크
            if not _weekly_stop and weekly_pnl_krw < WEEKLY_LOSS_LIMIT_KRW:
                _weekly_stop = True
                send_msg(
                    f"🚨 주간 손실 한도 초과! ({weekly_pnl_krw:+,.0f}원)\n"
                    f"→ 다음 주 월요일까지 자동 정지합니다.",
                    level="critical"
                )

            log_change("매도", f"{MARKET_CODE} {price:,.2f}원 × {filled:.8f}  손익:{pnl_krw:+,.0f}원  [{reason}]")
            trade_count += 1
            _daily_report_sent = False
            bot.update({"has_stock": False, "filled_qty": 0.0, "be_active": False})
            highest_profit  = 0.0
            _last_sell_time = time.time()
            _buy_time       = 0.0
            save_state()
            log_trade("SELL", actual_sell, filled, pnl_krw,
                      rsi=rsi, vwap=vwap, ma20=ma20, ma60=ma60, reason=reason,
                      pos_hold_time=pos_hold, buy_price_ref=bot.get("buy_price", 0))

            send_msg(
                f"✅ 팔았어요! [{reason}]\n"
                f"판 가격  : {actual_sell:,.2f}원\n"
                f"이번 손익: {pnl_krw:+,.0f}원\n"
                f"오늘 누적: {daily_pnl_krw:+,.0f}원\n"
                f"주간 누적: {weekly_pnl_krw:+,.0f}원\n"
                f"오늘 거래: {trade_count}회 (승 {win_count} / 패 {loss_count})",
                level="critical"
            )

            if consecutive_loss >= CONSEC_LOSS_ALERT:
                new_rsi = max(bot["rsi_buy"] - RSI_TIGHTEN_STEP, RSI_BUY_MIN)
                if new_rsi < bot["rsi_buy"]:
                    bot["rsi_buy"] = new_rsi
                    send_msg(
                        f"⚠️ 연속 {consecutive_loss}번 손해!\n"
                        f"→ RSI 기준 자동 강화: {bot['rsi_buy'] + RSI_TIGHTEN_STEP} → {bot['rsi_buy']}",
                        level="critical"
                    )
            return True
        time.sleep(2)

    send_msg(
        f"🚨 매도 실패! [{reason}]\n→ 업비트 앱에서 직접 매도해 주세요.\n현재가: {price:,.2f}원",
        level="critical"
    )
    return False

# ============================================================
# [11] 5분 다이버전스
# ============================================================
def update_5m_trough(ts, price, rsi):
    _price_trough_5m.appendleft((ts, price, rsi))

def check_5m_divergence():
    if len(_price_trough_5m) < 2:
        return False   # 데이터 부족 → False (거짓 신호 방지)
    _, price1, rsi1 = _price_trough_5m[0]
    _, price2, rsi2 = _price_trough_5m[1]
    return (price2 < price1) and (rsi2 > rsi1)

# ============================================================
# [12] 손실 한도 체크
# ============================================================
def is_daily_loss_exceeded():
    threshold = min(MAX_DAILY_LOSS_KRW, DAILY_LOSS_BASE_KRW * MAX_DAILY_LOSS_PCT / 100)
    return daily_pnl_krw <= threshold

def is_weekly_loss_exceeded():
    return weekly_pnl_krw <= WEEKLY_LOSS_LIMIT_KRW

# ============================================================
# [13] 동적 파라미터 조정
# ============================================================
def update_dynamic_parameters(price):
    if not _dynamic_mode:
        return
    if len(price_history) < 14:
        return
    atr = get_atr(price_history, 14)
    if atr:
        atr_pct    = (atr / price) * 100
        bot["target"]   = round(max(0.6, min(3.0, atr_pct * 1.5)), 2)
        bot["max_loss"] = -round(max(0.5, min(3.0, atr_pct * 2.0)), 2)
        bot["drop"]     = round(max(0.3, min(2.0, atr_pct * 1.0)), 2)

# ============================================================
# [통합 리포트]
# ============================================================
def _read_log_summary(log_path, period="daily"):
    if not os.path.exists(log_path):
        return None
    today_str = str(date.today())
    now       = datetime.now()
    year, week_no, _ = now.isocalendar()
    month_str = now.strftime("%Y-%m")
    total_pnl = 0
    wins = losses = trades = 0
    try:
        with open(log_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("side") != "SELL":
                    continue
                dt_str = row.get("datetime", "")
                if period == "daily" and not dt_str.startswith(today_str):
                    continue
                if period == "weekly":
                    try:
                        dt = datetime.strptime(dt_str[:10], "%Y-%m-%d")
                        y, w, _ = dt.isocalendar()
                        if (y, w) != (year, week_no):
                            continue
                    except:
                        continue
                if period == "monthly" and not dt_str.startswith(month_str):
                    continue
                pnl = float(row.get("pnl_krw") or 0)
                total_pnl += pnl
                trades    += 1
                if pnl >= 0:
                    wins += 1
                else:
                    losses += 1
    except Exception as e:
        cprint(f"[리포트 읽기 오류] {e}", Fore.YELLOW)
        return None
    return {"pnl": total_pnl, "trades": trades, "wins": wins, "losses": losses}

def send_combined_report(period="daily"):
    period_kr = {"daily": "오늘", "weekly": "이번 주", "monthly": "이번 달", "total": "전체"}.get(period, period)
    kis_data  = _read_log_summary(KIS_SHARED_LOG,  period)
    coin_data = _read_log_summary(COIN_SHARED_LOG, period) or _read_log_summary(LOG_FILE, period)

    def fmt(data, name):
        if not data:
            return f"{name}: 데이터 없음"
        rate = f"{data['wins']/data['trades']*100:.0f}%" if data['trades'] > 0 else "N/A"
        return (
            f"{name}\n"
            f"  손익: {data['pnl']:+,.0f}원\n"
            f"  거래: {data['trades']}회 (승{data['wins']}/패{data['losses']})  승률: {rate}"
        )

    kis_str  = fmt(kis_data,  "📈 주식봇 (KIS)")
    coin_str = fmt(coin_data, "🪙 코인봇 (업비트)")
    combined_pnl    = (kis_data["pnl"]    if kis_data    else 0) + (coin_data["pnl"]    if coin_data    else 0)
    combined_trades = (kis_data["trades"] if kis_data    else 0) + (coin_data["trades"] if coin_data    else 0)
    combined_wins   = (kis_data["wins"]   if kis_data    else 0) + (coin_data["wins"]   if coin_data    else 0)
    combined_rate   = f"{combined_wins/combined_trades*100:.0f}%" if combined_trades > 0 else "N/A"

    send_msg(
        f"📊 통합 리포트 [{period_kr}]\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{kis_str}\n\n"
        f"{coin_str}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 합산\n"
        f"  총 손익: {combined_pnl:+,.0f}원\n"
        f"  총 거래: {combined_trades}회  통합 승률: {combined_rate}",
        level="normal", force=True
    )

# ============================================================
# [GitHub 원격 업데이트]
# ============================================================
VERSION_FILE = os.path.join(BASE_DIR, ".coin_bot_version.json")
BOT_SCRIPT   = os.path.abspath(__file__)

def _load_local_version():
    if not os.path.exists(VERSION_FILE):
        return None
    try:
        with open(VERSION_FILE, encoding="utf-8") as f:
            return json.load(f)
    except:
        return None

def _save_local_version(info):
    with open(VERSION_FILE, "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=2)

def _github_latest(repo, token):
    try:
        res = requests.get(
            f"https://api.github.com/repos/{repo}/commits/main",
            headers={"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"},
            timeout=10
        )
        if res.status_code == 200:
            data = res.json()
            return {
                "hash":    data["sha"][:7],
                "full":    data["sha"],
                "message": data["commit"]["message"].split("\n")[0],
                "time":    data["commit"]["author"]["date"][:16].replace("T", " "),
            }
    except Exception as e:
        cprint(f"[GitHub 조회 오류] {e}", Fore.YELLOW)
    return None

def _github_download(repo, token, filename, ref=None):
    try:
        params = {"ref": ref} if ref else {}
        res = requests.get(
            f"https://api.github.com/repos/{repo}/contents/{filename}",
            headers={"Authorization": f"token {token}", "Accept": "application/vnd.github.v3.raw"},
            params=params, timeout=30
        )
        if res.status_code == 200:
            return res.text
    except Exception as e:
        cprint(f"[GitHub 다운로드 오류] {e}", Fore.YELLOW)
    return None

def _extract_version(code):
    for line in code.splitlines():
        if line.startswith("BOT_VERSION"):
            try:
                return line.split("=")[1].strip().strip('"').strip("'")
            except:
                pass
    return None

def _version_newer(new_ver, cur_ver):
    try:
        return tuple(map(int, new_ver.split("."))) > tuple(map(int, cur_ver.split(".")))
    except:
        return False

def _restart():
    import subprocess
    subprocess.Popen([sys.executable] + sys.argv)
    time.sleep(1)
    os._exit(0)

def _apply_code(new_code, new_info, current_hash):
    backup_path = BOT_SCRIPT + ".bak"
    shutil.copy2(BOT_SCRIPT, backup_path)
    try:
        with open(BOT_SCRIPT, "w", encoding="utf-8") as f:
            f.write(new_code)
    except Exception as e:
        send_msg(f"❌ 파일 저장 실패: {e}", level="critical", force=True)
        shutil.copy2(backup_path, BOT_SCRIPT)
        return
    _save_local_version(new_info)
    send_msg(
        f"✅ 교체 완료! 재시작합니다...\n"
        f"이전: {current_hash}\n"
        f"현재: {new_info['hash']}  v{new_info.get('version','?')}\n"
        f"메시지: {new_info['message']}",
        level="critical", force=True
    )
    time.sleep(2)
    _restart()

def do_update(force=False):
    github_token = _cfg.get("github_token", "")
    github_repo  = _cfg.get("github_repo", "")
    bot_filename = os.path.basename(BOT_SCRIPT)

    if not github_token or not github_repo:
        send_msg(
            "❌ 업데이트 설정 없음\n"
            "upbit_cfg.yaml 에 아래 항목을 추가하세요:\n"
            "  github_token: \"ghp_...\"\n"
            "  github_repo: \"아이디/레포명\"",
            level="normal", force=True
        )
        return

    send_msg("🔄 업데이트 확인 중...", level="normal", force=True)
    latest = _github_latest(github_repo, github_token)
    if not latest:
        send_msg("❌ GitHub 연결 실패. 토큰/레포 이름을 확인하세요.", level="critical", force=True)
        return

    current      = _load_local_version()
    current_hash = current["hash"] if current else "없음"

    if current and current["hash"] == latest["hash"]:
        send_msg(
            f"✅ 이미 최신 버전입니다\n"
            f"버전: v{BOT_VERSION}  커밋: {latest['hash']}\n"
            f"메시지: {latest['message']}",
            level="normal", force=True
        )
        return

    new_code = _github_download(github_repo, github_token, bot_filename)
    if not new_code:
        send_msg("❌ 코드 다운로드 실패", level="critical", force=True)
        return

    new_ver = _extract_version(new_code)
    latest["version"] = new_ver or "?"

    if new_ver and not force:
        if not _version_newer(new_ver, BOT_VERSION):
            send_msg(
                f"⚠️ 다운그레이드 감지!\n"
                f"현재: v{BOT_VERSION} → GitHub: v{new_ver}\n"
                f"→ 업데이트 취소\n"
                f"→ 강제 진행: /update force",
                level="critical", force=True
            )
            return

    send_msg(
        f"📥 새 버전!\n"
        f"현재: v{BOT_VERSION}  ({current_hash})\n"
        f"최신: v{new_ver}  ({latest['hash']})\n"
        f"메시지: {latest['message']}\n→ 교체 중...",
        level="normal", force=True
    )
    _apply_code(new_code, latest, current_hash)

def do_rollback(target_hash=None):
    """
    /rollback         → .bak 파일로 한 단계 복원
    /rollback abc1234 → GitHub 특정 커밋으로 복원
    """
    github_token = _cfg.get("github_token", "")
    github_repo  = _cfg.get("github_repo", "")
    bot_filename = os.path.basename(BOT_SCRIPT)
    backup_path  = BOT_SCRIPT + ".bak"

    # ── 커밋 해시 지정 롤백 ──────────────────────────────────
    if target_hash:
        if not github_token or not github_repo:
            send_msg("❌ github_token / github_repo 설정이 없습니다.", level="normal", force=True)
            return
        send_msg(f"🔄 커밋 {target_hash} 로 롤백 중...", level="normal", force=True)
        new_code = _github_download(github_repo, github_token, bot_filename, ref=target_hash)
        if not new_code:
            send_msg(f"❌ 커밋 {target_hash} 다운로드 실패\n→ 해시를 다시 확인하세요.", level="critical", force=True)
            return
        rollback_ver = _extract_version(new_code) or "?"
        current      = _load_local_version()
        current_hash = current["hash"] if current else "없음"
        info = {"hash": target_hash[:7], "full": target_hash, "message": f"롤백: {target_hash[:7]}", "time": "", "version": rollback_ver}
        shutil.copy2(BOT_SCRIPT, backup_path)
        try:
            with open(BOT_SCRIPT, "w", encoding="utf-8") as f:
                f.write(new_code)
        except Exception as e:
            send_msg(f"❌ 파일 저장 실패: {e}", level="critical", force=True)
            shutil.copy2(backup_path, BOT_SCRIPT)
            return
        _save_local_version(info)
        send_msg(
            f"✅ 롤백 완료! 재시작합니다...\n"
            f"이전: v{BOT_VERSION}  ({current_hash})\n"
            f"복원: v{rollback_ver}  ({target_hash[:7]})",
            level="critical", force=True
        )
        time.sleep(2)
        _restart()
        return

    # ── .bak 한 단계 롤백 ────────────────────────────────────
    if not os.path.exists(backup_path):
        send_msg("❌ 백업 파일이 없습니다. 이전에 업데이트한 적이 없어요.", level="normal", force=True)
        return

    send_msg("🔄 이전 버전으로 롤백 중...", level="normal", force=True)
    try:
        with open(backup_path, encoding="utf-8") as f:
            bak_code = f.read()
        bak_ver = _extract_version(bak_code) or "?"
        tmp = BOT_SCRIPT + ".tmp"
        shutil.copy2(BOT_SCRIPT, tmp)
        shutil.copy2(backup_path, BOT_SCRIPT)
        shutil.copy2(tmp, backup_path)
        os.remove(tmp)
        info = {"hash": "bak", "full": "bak", "message": "롤백 (.bak)", "time": "", "version": bak_ver}
        _save_local_version(info)
        send_msg(
            f"✅ 롤백 완료! 재시작합니다...\n"
            f"이전: v{BOT_VERSION}\n"
            f"복원: v{bak_ver}",
            level="critical", force=True
        )
        time.sleep(2)
        _restart()
    except Exception as e:
        send_msg(f"❌ 롤백 실패: {e}", level="critical", force=True)

# ============================================================
# [14] 텔레그램 폴링 / 명령 처리
# ============================================================
def poll_telegram():
    global last_update_id, _last_tg_poll, _tg_fail_count
    if time.time() - _last_tg_poll < 3:
        return
    _last_tg_poll = time.time()
    try:
        res = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
            params={"offset": last_update_id + 1, "timeout": 1},
            timeout=5
        ).json()
        _tg_fail_count = 0
        for upd in res.get("result", []):
            last_update_id = upd["update_id"]
            msg = upd.get("message", {})
            if str(msg.get("chat", {}).get("id", "")) == str(CHAT_ID):
                handle_command(msg.get("text", "").strip())
            cb = upd.get("callback_query", {})
            if cb and str(cb.get("from", {}).get("id", "")) == str(CHAT_ID):
                try:
                    requests.post(
                        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery",
                        json={"callback_query_id": cb["id"]}, timeout=3
                    )
                except:
                    pass
                handle_command(cb.get("data", "").strip())
    except Exception as e:
        _tg_fail_count += 1
        cprint(f"[텔레그램 폴링 오류 {_tg_fail_count}회] {e}", Fore.YELLOW)
        if _tg_fail_count >= TG_FAIL_ALERT_THRESHOLD:
            cprint(f"🚨 텔레그램 연속 {_tg_fail_count}회 실패 — 네트워크 확인 필요", Fore.RED, bright=True)
            _tg_fail_count = 0

def poll_sleep(seconds):
    end = time.time() + seconds
    while time.time() < end:
        poll_telegram()
        remaining = end - time.time()
        if remaining <= 0:
            break
        time.sleep(min(3, remaining))

def handle_command(text):
    global _weekly_stop, _dynamic_mode
    global DAILY_LOSS_BASE_KRW, ORDER_BUDGET_KRW, MARKET_CODE
    global MAX_TRADE_COUNT, COOLDOWN_SEC, VOL_MIN_PCT, VOL_MAX_PCT
    global MAX_SLIPPAGE_PCT, POS_TIMEOUT_MIN, VWAP_FILTER

    if not text:
        return
    cmd = text.strip().split()

    # ── 시작 / 정지 ───────────────────────────────────────────
    if cmd[0] in ("/start", "/켜줘"):
        bot["running"] = True
        bot["rsi_buy"] = RSI_BUY_DEFAULT
        send_msg(
            f"▶️ 봇 시작!\n"
            f"종목: {MARKET_CODE}\n"
            f"예산: {ORDER_BUDGET_KRW:,.0f}원\n"
            f"RSI: {bot['rsi_buy']} / 익절: {bot['target']}% / 손절: {bot['max_loss']}%",
            level="critical", keyboard=KB_MAIN, force=True
        )

    elif cmd[0] in ("/stop", "/멈춰"):
        bot["running"] = False
        send_msg("⏹️ 봇 정지. /start 로 재시작하세요.", level="critical", force=True)

    # ── 즉시 매도 (Kill Switch) ───────────────────────────────
    elif cmd[0] == "/sell":
        if bot["has_stock"] and bot["buy_price"] > 0:
            price = get_price()
            if price:
                send_msg("🔴 수동 즉시 매도 실행 중...", level="critical", force=True)
                do_sell(price, "수동 즉시 매도")
            else:
                send_msg("❌ 현재가 조회 실패. 업비트 앱에서 직접 매도하세요.", level="critical", force=True)
        else:
            send_msg("ℹ️ 현재 보유 중인 코인이 없습니다.", level="normal", force=True)

    # ── 상태 조회 ─────────────────────────────────────────────
    elif cmd[0] in ("/status", "/s", "/상태"):
        price = get_price() or _last_price
        rsi   = bot.get("_last_rsi")
        ma20  = bot.get("_ma20")
        ma60  = bot.get("_ma60")
        pnl_str = ""
        if bot["has_stock"] and price and bot["buy_price"] > 0:
            pnl = net_diff_krw(bot["buy_price"], price, bot["filled_qty"])
            pnl_pct = (price - bot["buy_price"]) / bot["buy_price"] * 100
            hold_min = int((time.time() - _buy_time) / 60) if _buy_time else 0
            pnl_str = (
                f"\n보유  : {bot['filled_qty']:.8f} ({bot['buy_price']:,.2f}원에 매수)\n"
                f"현재  : {price:,.2f}원  ({pnl_pct:+.2f}%)\n"
                f"평가손익: {pnl:+,.0f}원  |  보유: {hold_min}분"
            )
        status = (
            f"📊 [{MARKET_CODE}] 상태\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"가격  : {price:,.2f}원\n"
            f"RSI   : {rsi if rsi else 'N/A'}\n"
            f"MA20  : {ma20:,.2f}원\n" if ma20 else "MA20  : N/A\n"
            f"VWAP  : {_vwap_value:,.2f}원\n" if _vwap_value else "VWAP  : N/A\n"
            f"{pnl_str}\n"
            f"─────────────────\n"
            f"오늘  : {daily_pnl_krw:+,.0f}원  |  주간: {weekly_pnl_krw:+,.0f}원\n"
            f"거래  : {trade_count}회 (승{win_count}/패{loss_count})\n"
            f"봇    : {'▶️ 실행 중' if bot['running'] else '⏹️ 정지'}"
        )
        send_msg(status, level="normal", force=True)

    # ── 잔고 조회 ─────────────────────────────────────────────
    elif cmd[0] in ("/balance", "/b", "/잔고"):
        krw  = get_balance_krw()
        coin = get_coin_balance()
        send_msg(
            f"💰 잔고\n"
            f"원화  : {krw:,.0f}원\n"
            f"코인  : {coin:.8f} {MARKET_CODE.replace('KRW-','')}",
            level="normal", force=True
        )

    # ── /why : 매수 미발생 이유 ───────────────────────────────
    elif cmd[0] == "/why":
        price = _last_price
        rsi   = bot.get("_last_rsi")
        ma20  = bot.get("_ma20")
        ma60  = bot.get("_ma60")
        vol   = calc_vol_pct(timed_prices)
        drop  = ((ma20 - price) / ma20 * 100) if ma20 and price else None
        chk   = lambda ok: "✅" if ok else "❌"
        reasons = []
        if not bot["running"]:          reasons.append("❌ 봇 정지 상태 (/start)")
        if bot["has_stock"]:            reasons.append("❌ 이미 보유 중 (매도 후 재진입)")
        if _weekly_stop:                reasons.append("❌ 주간 손실 한도 초과 (다음주 월요일 해제)")
        if is_daily_loss_exceeded():    reasons.append("❌ 일일 손실 한도 초과")
        if trade_count >= MAX_TRADE_COUNT: reasons.append(f"❌ 하루 최대 거래 횟수 도달 ({trade_count}/{MAX_TRADE_COUNT})")
        cooldown_left = COOLDOWN_SEC - (time.time() - _last_sell_time)
        if cooldown_left > 0:           reasons.append(f"❌ 쿨다운 대기 중 ({int(cooldown_left)}초 남음)")
        if _real_data_count < REAL_DATA_MIN: reasons.append(f"❌ 데이터 수집 중 ({_real_data_count}/{REAL_DATA_MIN})")
        if rsi is None:                 reasons.append("❌ RSI 계산 불가")
        elif rsi > bot["rsi_buy"] + 5:  reasons.append(f"❌ RSI 높음 ({rsi:.1f} > 기준 {bot['rsi_buy']})")
        if ma20 and ma60 and ma20 < ma60: reasons.append(f"❌ 하락 추세 (MA20 {ma20:,.2f} < MA60 {ma60:,.2f})")
        if vol is not None:
            if vol < VOL_MIN_PCT:       reasons.append(f"❌ 변동성 너무 낮음 ({vol:.2f}% < {VOL_MIN_PCT}%)")
            elif vol > VOL_MAX_PCT:     reasons.append(f"❌ 변동성 너무 높음 ({vol:.2f}% > {VOL_MAX_PCT}%)")
        if drop is not None and drop < bot["drop"]: reasons.append(f"❌ 눌림 부족 ({drop:.2f}% < {bot['drop']}%)")
        if not reasons:
            reasons.append("✅ 현재 모든 조건 통과 — RSI V-Turn 대기 중")
        send_msg("🔍 매수 미발생 이유\n" + "\n".join(reasons), level="normal", force=True)

    # ── 설정 변경 ─────────────────────────────────────────────
    elif cmd[0] == "/set":
        SET_KR_ALIAS = {
            "익절": "target", "손절": "max_loss", "눌림": "drop",
            "rsi": "rsi_buy", "RSI": "rsi_buy",
        }
        if len(cmd) != 3:
            send_msg(
                "⚙️ /set 항목 값\n\n"
                "전략: target max_loss drop rsi_buy trail_start trail_gap be_trigger\n"
                "운영: trade_count cooldown vol_min vol_max slippage timeout_min vwap_filter\n\n"
                f"현재: target={bot['target']} max_loss={bot['max_loss']} "
                f"rsi={bot['rsi_buy']} drop={bot['drop']}\n"
                f"trade_count={MAX_TRADE_COUNT} cooldown={COOLDOWN_SEC}s "
                f"vol={VOL_MIN_PCT}~{VOL_MAX_PCT}%",
                level="normal", force=True
            )
        else:
            raw_key = cmd[1].lower()
            key = SET_KR_ALIAS.get(raw_key, SET_KR_ALIAS.get(cmd[1], raw_key))
            if key in SET_ALLOWED_KEYS:
                try:
                    val = float(cmd[2])
                    if key == "trade_count":
                        MAX_TRADE_COUNT = int(val)
                        send_msg(f"✅ 하루 최대 거래횟수: {MAX_TRADE_COUNT}회", level="normal", force=True)
                    elif key == "cooldown":
                        COOLDOWN_SEC = int(val)
                        send_msg(f"✅ 쿨다운: {COOLDOWN_SEC}초", level="normal", force=True)
                    elif key == "vol_min":
                        VOL_MIN_PCT = val
                        send_msg(f"✅ 최소 변동성: {VOL_MIN_PCT}%", level="normal", force=True)
                    elif key == "vol_max":
                        VOL_MAX_PCT = val
                        send_msg(f"✅ 최대 변동성: {VOL_MAX_PCT}%", level="normal", force=True)
                    elif key == "slippage":
                        MAX_SLIPPAGE_PCT = val
                        send_msg(f"✅ 슬리피지 한도: {MAX_SLIPPAGE_PCT}%", level="normal", force=True)
                    elif key == "timeout_min":
                        POS_TIMEOUT_MIN = int(val)
                        send_msg(f"✅ 포지션 타임아웃: {POS_TIMEOUT_MIN}분", level="normal", force=True)
                    elif key == "vwap_filter":
                        VWAP_FILTER = bool(int(val))
                        send_msg(f"✅ VWAP 필터: {'켜짐' if VWAP_FILTER else '꺼짐'}", level="normal", force=True)
                    else:
                        bot[key] = val
                        if key in ("target", "max_loss"):
                            _dynamic_mode = False
                        send_msg(f"✅ {key} = {val}", level="normal", force=True)
                    log_change("설정변경", f"{key} = {cmd[2]}")
                    save_state()
                except:
                    send_msg("❌ 숫자로 입력하세요. 예) /set rsi_buy 35", level="normal")
            else:
                send_msg(f"❌ 변경 불가 항목: {cmd[1]}", level="normal")

    # ── 예산 / 손실한도 ──────────────────────────────────────
    elif cmd[0] == "/budget":
        if len(cmd) == 2:
            try:
                val = int(cmd[1].replace(",", ""))
                if val < 5000:
                    send_msg("❌ 최소 5,000원 이상으로 설정하세요.", level="normal")
                else:
                    ORDER_BUDGET_KRW = val
                    log_change("예산변경", f"ORDER_BUDGET_KRW = {val:,}원")
                    send_msg(f"✅ 주문 예산 변경: {ORDER_BUDGET_KRW:,.0f}원", level="normal", force=True)
                    save_state()
            except:
                send_msg("❌ 숫자로 입력하세요. 예) /budget 20000", level="normal")

    elif cmd[0] == "/risk":
        if len(cmd) == 2:
            try:
                val = int(cmd[1].replace(",", ""))
                DAILY_LOSS_BASE_KRW = val
                log_change("손실한도변경", f"DAILY_LOSS_BASE_KRW = {val:,}원")
                threshold = DAILY_LOSS_BASE_KRW * MAX_DAILY_LOSS_PCT / 100
                send_msg(
                    f"✅ 손실 기준 변경: {DAILY_LOSS_BASE_KRW:,.0f}원\n"
                    f"→ 일일 최대 손실: {threshold:,.0f}원",
                    level="normal", force=True
                )
                save_state()
            except:
                send_msg("❌ 숫자로 입력하세요. 예) /risk 100000", level="normal")

    # ── 거래 내역 ─────────────────────────────────────────────
    elif cmd[0] in ("/log", "/l", "/내역"):
        try:
            if not os.path.exists(LOG_FILE):
                send_msg("📋 아직 거래 내역이 없어요.", level="normal", force=True)
                return
            today_str = str(date.today())
            lines = []
            with open(LOG_FILE, encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get("datetime", "").startswith(today_str):
                        side_kr = "매수" if row["side"] == "BUY" else "매도"
                        pnl_str = ""
                        if row["side"] == "SELL":
                            pnl = float(row.get("pnl_krw") or 0)
                            pnl_str = f"  손익:{pnl:+,.0f}원"
                        lines.append(
                            f"{row['datetime'][11:16]} {side_kr} "
                            f"{float(row['price']):,.2f}원 × {float(row['qty']):.6f}"
                            f"{pnl_str}"
                        )
            if not lines:
                send_msg("📋 오늘 거래 내역이 없어요.", level="normal", force=True)
            else:
                chunk = f"📋 오늘 거래 ({len(lines)}건)\n{'─'*24}\n"
                for line in lines:
                    chunk += line + "\n"
                chunk += f"{'─'*24}\n오늘 누적: {daily_pnl_krw:+,.0f}원"
                send_msg(chunk, level="normal", force=True)
        except Exception as e:
            send_msg(f"❌ 로그 읽기 실패: {e}", level="normal", force=True)

    # ── 주간 손익 ─────────────────────────────────────────────
    elif cmd[0] in ("/weekly", "/w", "/주간"):
        send_msg(
            f"📆 주간 손익\n"
            f"주간 누적: {weekly_pnl_krw:+,.0f}원\n"
            f"한도     : {WEEKLY_LOSS_LIMIT_KRW:,.0f}원\n"
            f"상태     : {'🚨 정지 중' if _weekly_stop else '✅ 정상'}",
            level="normal", force=True
        )

    # ── 도움말 ────────────────────────────────────────────────
    elif cmd[0] in ("/help", "/도움말"):
        send_msg(
            "📋 명령어 목록\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "▶️ /start  ⏹️ /stop  🔴 /sell (즉시 매도)\n"
            "📊 /status  💰 /balance  📋 /log  📆 /weekly\n"
            "⚙️ /set 항목 값  /budget 금액  /risk 금액\n"
            "🔍 /why (매수 안 되는 이유)\n"
            "🔄 /reload (설정 재로드)\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "예) /set rsi_buy 35\n"
            "    /set trade_count 30\n"
            "    /budget 20000",
            level="normal", keyboard=KB_MAIN, force=True
        )

    elif cmd[0] == "/reload":
        try:
            load_config()
            send_msg("✅ 설정 파일 재로드 완료", level="normal", force=True)
        except Exception as e:
            send_msg(f"❌ 설정 파일 재로드 실패: {e}", level="critical", force=True)

    elif cmd[0] == "/version":
        ver = _load_local_version()
        if ver:
            send_msg(
                f"🔖 현재 버전\n"
                f"커밋: {ver['hash']}\n"
                f"메시지: {ver['message']}\n"
                f"시각: {ver['time']}",
                level="normal", force=True
            )
        else:
            send_msg("ℹ️ 버전 정보 없음 (GitHub 업데이트 후 생성됩니다)", level="normal", force=True)

    elif cmd[0] == "/report":
        period = cmd[1].lower() if len(cmd) > 1 else "daily"
        if period not in ("daily", "weekly", "monthly", "total"):
            send_msg(
                "📊 리포트 기간 선택\n"
                "/report          → 오늘\n"
                "/report weekly   → 이번 주\n"
                "/report monthly  → 이번 달\n"
                "/report total    → 전체 누적",
                level="normal", force=True
            )
        else:
            send_combined_report(period)

    elif cmd[0] in ("/update", "/업데이트"):
        force = len(cmd) > 1 and cmd[1].lower() == "force"
        if bot["has_stock"] and not force:
            send_msg(
                "⚠️ 현재 포지션 보유 중입니다!\n"
                "→ 업데이트 시 수 초간 감시가 끊깁니다.\n"
                "→ 강제 진행: /update force\n"
                "→ 매도 후 진행 권장",
                level="critical", force=True
            )
            return
        do_update(force=force)

    elif cmd[0] == "/rollback":
        if bot["has_stock"]:
            send_msg(
                "⚠️ 현재 포지션 보유 중입니다!\n"
                "→ 롤백 시 수 초간 감시가 끊깁니다.\n"
                "→ 매도 후 진행 권장\n"
                "→ 강제: /rollback force  또는  /rollback 커밋해시",
                level="critical", force=True
            )
            return
        target = cmd[1] if len(cmd) > 1 and cmd[1].lower() != "force" else None
        do_rollback(target_hash=target)

    elif cmd[0] == "/menu":
        send_msg("📋 메뉴", level="normal", keyboard=KB_MAIN, force=True)

# ============================================================
# [15] 일별 리셋 / 백업
# ============================================================
def check_daily_reset(now_dt):
    global daily_pnl_krw, trade_count, win_count, loss_count
    global _last_backup_date, _daily_report_sent, _real_data_count
    global _vwap_value, _vwap_sum_pv, _vwap_sum_v
    global _cooldown_alert_sent, _max_trade_alert_sent

    today_str = str(now_dt.date())
    if _last_backup_date == today_str:
        return
    _last_backup_date = today_str

    import gc
    price_history.clear()
    volume_history.clear()
    timed_prices.clear()
    _real_data_count = 0
    _vwap_sum_pv = 0.0
    _vwap_sum_v  = 0.0
    _vwap_value  = None
    _cooldown_alert_sent  = False
    _max_trade_alert_sent = False
    gc.collect()

    # 백업
    backup_date = (now_dt - timedelta(days=1)).strftime("%Y%m%d")
    for src, name in [
        (LOG_FILE, f"coin_trade_{backup_date}.csv"),
        (STATE_FILE, f"coin_state_{backup_date}.json"),
    ]:
        if os.path.exists(src):
            dst = os.path.join(BACKUP_DIR, name)
            shutil.copy2(src, dst)

    # 오래된 백업 삭제
    cutoff = datetime.now() - timedelta(days=BACKUP_KEEP_DAYS)
    for fn in os.listdir(BACKUP_DIR):
        fp = os.path.join(BACKUP_DIR, fn)
        if os.path.isfile(fp) and datetime.fromtimestamp(os.path.getmtime(fp)) < cutoff:
            os.remove(fp)

def check_weekly_reset(now_dt):
    global weekly_pnl_krw, _weekly_stop
    if now_dt.weekday() == 0 and now_dt.hour == 0 and now_dt.minute < 2:
        if weekly_pnl_krw != 0 or _weekly_stop:
            weekly_pnl_krw = 0
            _weekly_stop   = False
            save_state()
            send_msg("📅 새로운 주 시작! 주간 손익 초기화됐어요.", level="normal")

# ============================================================
# [16] 하트비트
# ============================================================
def check_heartbeat(now_dt):
    global _last_heartbeat_hour
    if now_dt.minute != 0:
        return
    if now_dt.hour == _last_heartbeat_hour:
        return
    _last_heartbeat_hour = now_dt.hour

    pnl_now = 0
    if bot["has_stock"] and bot["buy_price"] > 0 and _last_price:
        pnl_now = net_diff_krw(bot["buy_price"], _last_price, bot["filled_qty"])

    cooldown_left = max(0, COOLDOWN_SEC - (time.time() - _last_sell_time))
    import psutil as _ps
    cpu = _ps.cpu_percent(interval=0.1)
    ram = _ps.virtual_memory().percent

    send_msg(
        f"💓 하트비트 [{now_dt.strftime('%H:%M')}]\n"
        f"가격   : {_last_price:,.2f}원  |  RSI: {bot.get('_last_rsi') or 'N/A'}\n"
        f"평가손익: {pnl_now:+,.0f}원  |  오늘: {daily_pnl_krw:+,.0f}원\n"
        f"거래   : {trade_count}회  |  쿨다운: {int(cooldown_left)}초 남음\n"
        f"루프   : {LOOP_INTERVAL}초  |  CPU:{cpu:.0f}% RAM:{ram:.0f}%\n"
        f"봇     : {'▶️ 실행 중' if bot['running'] else '⏹️ 정지'}",
        level="silent"
    )

# ============================================================
# [17] 데이터 프리필
# ============================================================
def prefill_history():
    """과거 분봉 데이터로 price_history 채우기"""
    global _real_data_count
    cprint(f"[프리필] {MARKET_CODE} 과거 데이터 로딩 중...", Fore.CYAN)
    prices = get_ohlcv(count=min(HISTORY_PREFILL, 200), interval=1)
    if prices:
        for p in prices:
            price_history.append(p)
        _real_data_count = len(prices)
        cprint(f"[프리필] {len(prices)}개 로드 완료", Fore.GREEN)
    else:
        cprint("[프리필] 데이터 로드 실패 — 실시간 데이터 수집 대기", Fore.YELLOW)

# ============================================================
# [18] 메인 루프
# ============================================================
def _register_bot_commands():
    """텔레그램 메뉴바에 명령어 목록 등록"""
    commands = [
        {"command": "menu",     "description": "버튼 메뉴 열기"},
        {"command": "status",   "description": "현재 상태 확인"},
        {"command": "report",   "description": "통합 리포트  예) /report weekly"},
        {"command": "log",      "description": "오늘 거래 내역"},
        {"command": "weekly",   "description": "주간 손익 확인"},
        {"command": "balance",  "description": "잔고 조회"},
        {"command": "start",    "description": "매매 시작"},
        {"command": "stop",     "description": "매매 정지"},
        {"command": "sell",     "description": "즉시 매도"},
        {"command": "why",      "description": "매수 안 되는 이유"},
        {"command": "set",      "description": "전략 수치 변경  예) /set rsi_buy 35"},
        {"command": "budget",   "description": "주문 예산 변경  예) /budget 20000"},
        {"command": "risk",     "description": "손실 한도 변경  예) /risk 100000"},
        {"command": "reload",   "description": "설정 파일 재로드"},
        {"command": "version",  "description": "현재 버전 확인"},
        {"command": "update",   "description": "GitHub 최신 코드로 업데이트"},
        {"command": "rollback", "description": "이전 버전으로 롤백  예) /rollback  또는  /rollback abc1234"},
        {"command": "help",     "description": "명령어 전체 목록"},
    ]
    try:
        res = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setMyCommands",
            json={"commands": commands}, timeout=5
        )
        if res.status_code == 200 and res.json().get("result"):
            cprint("[텔레그램] 메뉴바 명령어 등록 완료 ✅", Fore.GREEN)
        else:
            cprint(f"[텔레그램] 메뉴바 등록 실패: {res.text}", Fore.YELLOW)
    except Exception as e:
        cprint(f"[텔레그램] 메뉴바 등록 오류: {e}", Fore.YELLOW)

def run_bot():
    global _last_price, _vwap_value, _vwap_sum_pv, _vwap_sum_v
    global _real_data_count, daily_pnl_krw, _dynamic_mode
    global _cooldown_alert_sent, _max_trade_alert_sent, _weekly_stop

    load_config()
    load_state()
    _register_bot_commands()
    prefill_history()

    send_msg(
        f"🚀 코인 봇 시작!\n"
        f"종목: {MARKET_CODE}\n"
        f"예산: {ORDER_BUDGET_KRW:,.0f}원\n"
        f"RSI: {bot['rsi_buy']} / 익절: {bot['target']}% / 손절: {bot['max_loss']}%\n"
        f"루프: {LOOP_INTERVAL}초 / 쿨다운: {COOLDOWN_SEC}초\n"
        f"데이터 준비: {_real_data_count}/{REAL_DATA_MIN}\n\n"
        f"/start 로 매매를 시작하세요.",
        level="critical", keyboard=KB_MAIN
    )

    while True:
        try:
            poll_telegram()
            now_dt = datetime.now()
            now_ts = time.time()

            check_daily_reset(now_dt)
            check_weekly_reset(now_dt)
            check_heartbeat(now_dt)

            # 현재가 조회
            price, volume = get_price_and_volume()
            if price is None:
                time.sleep(LOOP_INTERVAL)
                continue

            _last_price = price
            price_history.append(price)
            if volume is not None:
                volume_history.append(volume)
            timed_prices.append((now_ts, price))

            # VWAP 갱신
            if volume:
                _vwap_sum_pv += price * volume
                _vwap_sum_v  += volume
                _vwap_value   = _vwap_sum_pv / _vwap_sum_v if _vwap_sum_v > 0 else None

            _real_data_count += 1

            # 지표 계산
            rsi      = calc_rsi(price_history, BOT_RSI_PERIOD)
            ma20     = calc_ma(price_history, 20)
            ma60     = calc_ma(price_history, 60)
            vol      = calc_vol_pct(timed_prices)
            vwap     = _vwap_value
            vol_ratio = calc_vol_ratio(volume_history)

            bot["_last_rsi"] = rsi
            bot["_ma20"]     = ma20
            bot["_ma60"]     = ma60

            # indicator_log 기록
            if rsi is not None:
                drop_log = ((ma20 - price) / ma20 * 100) if ma20 else None
                log_indicator(price, rsi, ma20, ma60, vwap, vol, vol_ratio, drop_log)

            # 콘솔 상태 표시
            if now_ts - _last_status_line_ts >= 60:
                from sys import stdout
                rsi_s = f"{rsi:.1f}" if rsi else "N/A"
                ma_s  = f"MA20:{ma20:,.0f}" if ma20 else "MA20:N/A"
                status_s = "▶️" if bot["running"] else "⏹️"
                hold_s = f"보유:{bot['buy_price']:,.0f}원" if bot["has_stock"] else "미보유"
                print(f"\r[{now_dt.strftime('%H:%M:%S')}] {MARKET_CODE} {price:,.2f} RSI:{rsi_s} {ma_s} {hold_s} {status_s}   ", end="", flush=True)
                globals()["_last_status_line_ts"] = now_ts

            # 보유 중 청산 체크
            if bot["has_stock"] and bot["buy_price"] > 0:
                bp     = bot["buy_price"]
                pnl_p  = (price - bp) / bp * 100
                hp     = highest_profit

                # 최고 수익 갱신
                if pnl_p > hp:
                    globals()["highest_profit"] = pnl_p
                    hp = pnl_p

                # 익절
                if pnl_p >= bot["target"]:
                    do_sell(price, "목표 익절")
                    time.sleep(LOOP_INTERVAL)
                    continue

                # 트레일링 스탑
                if hp >= bot["trail_start"] and (hp - pnl_p) >= bot["trail_gap"]:
                    do_sell(price, "트레일링 스탑")
                    time.sleep(LOOP_INTERVAL)
                    continue

                # 본절 보호
                if hp >= bot["be_trigger"] and not bot["be_active"]:
                    bot["be_active"] = True
                if bot["be_active"] and pnl_p < 0:
                    do_sell(price, "본절 보호")
                    time.sleep(LOOP_INTERVAL)
                    continue

                # 손절
                if pnl_p <= bot["max_loss"]:
                    do_sell(price, "최대 손절")
                    time.sleep(LOOP_INTERVAL)
                    continue

                # 타임아웃
                if _buy_time > 0 and (now_ts - _buy_time) / 60 >= POS_TIMEOUT_MIN:
                    if pnl_p > -POS_TIMEOUT_LOSS_PCT:
                        do_sell(price, "포지션 타임아웃")
                    time.sleep(LOOP_INTERVAL)
                    continue

                # 일일 손실 한도 도달 시 청산
                if is_daily_loss_exceeded():
                    do_sell(price, "일일 손실 셧다운")
                    time.sleep(LOOP_INTERVAL)
                    continue

                time.sleep(LOOP_INTERVAL)
                continue

            # ── 매수 신호 탐지 ───────────────────────────────
            if not bot["running"]:
                time.sleep(LOOP_INTERVAL)
                continue

            if _weekly_stop or is_daily_loss_exceeded() or is_weekly_loss_exceeded():
                if not _weekly_stop and is_weekly_loss_exceeded():
                    _weekly_stop = True
                    send_msg(
                        f"🚨 주간 손실 한도 초과! ({weekly_pnl_krw:+,.0f}원)\n"
                        f"→ 다음 주 월요일까지 자동 정지합니다.",
                        level="critical"
                    )
                    save_state()
                time.sleep(LOOP_INTERVAL)
                continue

            if _real_data_count < REAL_DATA_MIN:
                time.sleep(LOOP_INTERVAL)
                continue

            # 쿨다운 / 최대거래 체크 (알림 1회)
            cooldown_ok   = (now_ts - _last_sell_time) >= COOLDOWN_SEC
            max_trade_ok  = trade_count < MAX_TRADE_COUNT
            if not cooldown_ok:
                if not _cooldown_alert_sent:
                    cprint(f"[쿨다운] {int(COOLDOWN_SEC - (now_ts - _last_sell_time))}초 남음", Fore.YELLOW)
                    _cooldown_alert_sent = True
                time.sleep(LOOP_INTERVAL)
                continue
            else:
                _cooldown_alert_sent = False

            if not max_trade_ok:
                if not _max_trade_alert_sent:
                    send_msg(f"📊 오늘 최대 거래 횟수 도달 ({trade_count}회). 내일 재개.", level="normal")
                    _max_trade_alert_sent = True
                time.sleep(LOOP_INTERVAL)
                continue

            # 지표 분석
            if ma20 and ma60 and rsi is not None and vol is not None:
                drop  = ((ma20 - price) / ma20) * 100
                prev1 = bot["prev_rsi"]
                prev2 = bot["prev_rsi2"]

                # RSI V-Turn 판단
                rsi_v_turn = (
                    prev2 is not None and prev1 is not None and
                    prev2 <= bot["rsi_buy"] and prev1 > prev2 and rsi > prev1
                )

                # 5분 다이버전스 업데이트 — 실제 최저점 기록
                if prev1 is not None and rsi <= bot["rsi_buy"] and rsi < prev1:
                    update_5m_trough(now_ts, price, rsi)
                elif prev1 is not None and prev1 <= bot["rsi_buy"] and rsi > prev1:
                    update_5m_trough(now_ts, price, prev1)
                divergence_ok = check_5m_divergence()

                # 변동성 필터 (VOL_MIN + VOL_MAX)
                if vol < VOL_MIN_PCT or vol > VOL_MAX_PCT:
                    bot["prev_rsi2"], bot["prev_rsi"] = bot["prev_rsi"], rsi
                    time.sleep(LOOP_INTERVAL)
                    continue

                vwap_ok = (not VWAP_FILTER) or (not vwap) or (price <= vwap)
                volr_ok = (vol_ratio is None) or (vol_ratio >= VOL_RATIO_MIN)
                ma_ok   = bool(ma20 and ma60 and ma20 > ma60)
                drop_ok = drop >= bot["drop"]

                # 동적 파라미터 조정
                update_dynamic_parameters(price)

                # 최종 매수 승인
                if rsi_v_turn and divergence_ok and vwap_ok and volr_ok and ma_ok and drop_ok:
                    if do_buy(price, "RSI-V-Turn + Div"):
                        globals()["highest_profit"] = 0.0
                        bot["be_active"] = False

            bot["prev_rsi2"], bot["prev_rsi"] = bot["prev_rsi"], rsi
            time.sleep(LOOP_INTERVAL)

        except Exception as e:
            tb = traceback.format_exc()
            cprint(f"\n[봇 오류]\n{tb}", Fore.RED, bright=True)
            try:
                requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                    json={
                        "chat_id": CHAT_ID,
                        "text": f"🚨 봇 오류 발생\n→ {e}\n→ 5초 후 자동 재시도",
                        "disable_notification": False
                    },
                    timeout=5
                )
            except:
                pass
            time.sleep(5)

if __name__ == "__main__":
    run_bot()
