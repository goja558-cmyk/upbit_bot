"""
==============================================================
  나스닥 → 코스피 인버스 자동매매 봇 v1.0
  
  전략:
  - 매일 06:00 Yahoo Finance로 나스닥 전일 등락률 확인
  - threshold 이하 하락 시 09:00~09:05 114800 매수
  - TP / SL / 15:20 강제 청산
  
  데이터 근거 (2024~2026, 492일):
  - 나스닥 -1.5% 이하: 승률 79%, 평균 +1.49%
  - 나스닥 -2.0% 이하: 승률 82%, 평균 +1.59%
  - 연속 2일 -1.0% 이하: 승률 78%, 평균 +2.23%

  설정 파일: inverse_cfg.yaml
==============================================================
"""

BOT_VERSION = "1.0"
BOT_NAME    = "인버스봇"
BOT_TAG     = "📉 인버스"

import os, sys, time, json, yaml, requests, threading, traceback
from datetime import datetime, date, timedelta
from colorama import Fore, Style, init as colorama_init
colorama_init(autoreset=True)

# ============================================================
# [1] 경로
# ============================================================
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
CFG_FILE   = os.path.join(BASE_DIR, "inverse_cfg.yaml")
STATE_FILE = os.path.join(BASE_DIR, "inverse_state.json")
LOG_FILE   = os.path.join(BASE_DIR, "inverse_trade_log.csv")
SHARED_DIR = os.path.join(BASE_DIR, "shared")
os.makedirs(SHARED_DIR, exist_ok=True)

def cprint(msg, color=Fore.WHITE, bright=False):
    prefix = Style.BRIGHT if bright else ""
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"{prefix}{color}[{ts}] {msg}{Style.RESET_ALL}")

# ============================================================
# [2] 설정
# ============================================================
_cfg = {}
TELEGRAM_TOKEN = ""
CHAT_ID        = ""

# 전략 파라미터 (기본값)
ENABLED          = True
STOCK_CODE       = "114800"   # KODEX 인버스
ORDER_BUDGET_KRW = 50_000
TP_PCT           = 1.2        # 익절 %
SL_PCT           = -0.8       # 손절 %
THRESHOLD        = -1.5       # 나스닥 하락 기준 %
STRONG_THRESHOLD = -1.0       # 연속 2일 기준 %
IS_MOCK          = False

PAPER_PROD_URL = "https://openapivts.koreainvestment.com:29443"
REAL_PROD_URL  = "https://openapi.koreainvestment.com:9443"

def load_config():
    global _cfg, TELEGRAM_TOKEN, CHAT_ID
    global ENABLED, ORDER_BUDGET_KRW, TP_PCT, SL_PCT
    global THRESHOLD, STRONG_THRESHOLD, IS_MOCK, STOCK_CODE

    # inverse_cfg.yaml 없으면 sector_cfg.yaml에서 KIS 인증만 가져옴
    kis_cfg_file = os.path.join(BASE_DIR, "sector_cfg.yaml")
    if not os.path.exists(CFG_FILE):
        cprint(f"⚠️ {CFG_FILE} 없음 → sector_cfg.yaml에서 인증 정보 로드", Fore.YELLOW)
        if not os.path.exists(kis_cfg_file):
            cprint(f"❌ sector_cfg.yaml도 없음", Fore.RED); sys.exit(1)
        with open(kis_cfg_file, encoding="utf-8") as f:
            _cfg = yaml.safe_load(f) or {}
    else:
        with open(CFG_FILE, encoding="utf-8") as f:
            _cfg = yaml.safe_load(f) or {}
        # KIS 인증은 sector_cfg.yaml에서 병합
        if os.path.exists(kis_cfg_file):
            with open(kis_cfg_file, encoding="utf-8") as f:
                kis_cfg = yaml.safe_load(f) or {}
            for k in ["app_key","app_secret","paper_app_key","paper_app_secret",
                      "account_no","account_suffix","mock"]:
                if k in kis_cfg and k not in _cfg:
                    _cfg[k] = kis_cfg[k]

    TELEGRAM_TOKEN   = _cfg.get("telegram_token", "")
    CHAT_ID          = str(_cfg.get("chat_id", ""))
    ENABLED          = _cfg.get("enabled", True)
    ORDER_BUDGET_KRW = int(_cfg.get("budget", 50_000))
    TP_PCT           = float(_cfg.get("tp", 1.2))
    SL_PCT           = float(_cfg.get("sl", -0.8))
    THRESHOLD        = float(_cfg.get("threshold", -1.5))
    STRONG_THRESHOLD = float(_cfg.get("strong_threshold", -1.0))
    IS_MOCK          = _cfg.get("mock", False)
    STOCK_CODE       = _cfg.get("stock_code", "114800")
    cprint(f"✅ 설정 로드 완료 | 예산:{ORDER_BUDGET_KRW:,}원 TP:{TP_PCT}% SL:{SL_PCT}% 기준:{THRESHOLD}%", Fore.GREEN)

# ============================================================
# [3] 텔레그램
# ============================================================
def send_msg(text, force=False):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        return
    # IPC 모드면 result 파일로만 전달
    if _ap_args.config is not None:
        _write_ipc_result(f"[normal] {text}")
        return
    try:
        tagged = f"[📉 인버스]\n{text}"
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": tagged},
            timeout=5
        )
    except Exception as e:
        cprint(f"[TG 오류] {e}", Fore.YELLOW)

# ============================================================
# [4] IPC
# ============================================================
import argparse as _argparse
_ap = _argparse.ArgumentParser(add_help=False)
_ap.add_argument("--config", default=None)
_ap_args, _ = _ap.parse_known_args()

_IPC_CMD_FILE    = os.path.join(SHARED_DIR, "cmd_inverse.json")
_IPC_RESULT_FILE = os.path.join(SHARED_DIR, "result_inverse.json")
_IPC_REQ_ID      = ""

def _write_ipc_result(text):
    try:
        tmp = _IPC_RESULT_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"result": text, "ts": time.time()}, f)
        os.replace(tmp, _IPC_RESULT_FILE)
    except Exception as e:
        cprint(f"[IPC 결과 오류] {e}", Fore.YELLOW)

def poll_ipc():
    """매니저 명령 수신"""
    if not os.path.exists(_IPC_CMD_FILE):
        return
    try:
        with open(_IPC_CMD_FILE, encoding="utf-8") as f:
            data = json.load(f)
        os.remove(_IPC_CMD_FILE)
        cmd  = data.get("cmd", "").strip()
        global _IPC_REQ_ID
        _IPC_REQ_ID = data.get("req_id", "")
        handle_command(cmd)
    except Exception as e:
        cprint(f"[IPC 폴링 오류] {e}", Fore.YELLOW)

# ============================================================
# [5] KIS API
# ============================================================
_token      = None
_token_time = 0

_BUCKET_CAPACITY  = 4
_BUCKET_RATE      = 3.5
_bucket_tokens    = float(_BUCKET_CAPACITY)
_bucket_last_time = time.time()

def _acquire_token():
    global _bucket_tokens, _bucket_last_time
    now = time.time()
    _bucket_tokens = min(_BUCKET_CAPACITY,
        _bucket_tokens + (now - _bucket_last_time) * _BUCKET_RATE)
    _bucket_last_time = now
    if _bucket_tokens < 1.0:
        time.sleep((1.0 - _bucket_tokens) / _BUCKET_RATE)
        _bucket_tokens = 0.0
    else:
        _bucket_tokens -= 1.0

def api_call(method, url, **kwargs):
    _acquire_token()
    for attempt in range(3):
        try:
            r = getattr(requests, method)(url, timeout=10, **kwargs)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                time.sleep(int(r.headers.get("Retry-After", 2)))
                continue
        except Exception as e:
            cprint(f"[API 오류 {attempt+1}] {e}", Fore.YELLOW)
            time.sleep(2)
    return {}

def _prod_url():
    return PAPER_PROD_URL if IS_MOCK else REAL_PROD_URL

def _acnt():
    return _cfg.get("account_no",""), _cfg.get("account_suffix","01")

def get_token():
    global _token, _token_time
    if _token and (time.time() - _token_time) < 3500:
        return _token
    key = _cfg.get("paper_app_key") if IS_MOCK else _cfg.get("app_key","")
    sec = _cfg.get("paper_app_secret") if IS_MOCK else _cfg.get("app_secret","")
    res = api_call("post", f"{_prod_url()}/oauth2/tokenP", json={
        "grant_type": "client_credentials",
        "appkey": key, "appsecret": sec
    })
    if res and "access_token" in res:
        _token = res["access_token"]
        _token_time = time.time()
        return _token
    cprint("[토큰 발급 실패]", Fore.RED)
    return None

def kis_headers(tr_id):
    token = get_token()
    if not token:
        return None
    key = _cfg.get("paper_app_key") if IS_MOCK else _cfg.get("app_key","")
    sec = _cfg.get("paper_app_secret") if IS_MOCK else _cfg.get("app_secret","")
    return {
        "authorization": f"Bearer {token}",
        "appkey": key, "appsecret": sec,
        "tr_id": tr_id, "custtype": "P",
        "content-type": "application/json",
    }

def get_tick_size(price):
    if price >= 500000: return 1000
    if price >= 100000: return 500
    if price >= 50000:  return 100
    if price >= 10000:  return 50
    if price >= 5000:   return 10
    if price >= 1000:   return 5
    return 1

def get_price(code):
    h = kis_headers("FHKST01010100")
    if not h: return 0
    res = api_call("get", f"{_prod_url()}/uapi/domestic-stock/v1/quotations/inquire-price",
        headers=h, params={"fid_cond_mrkt_div_code":"J","fid_input_iscd":code})
    try:
        return int(res["output"]["stck_prpr"])
    except:
        return 0

def get_cash():
    tr = "VTTC8908R" if IS_MOCK else "TTTC8908R"
    h = kis_headers(tr)
    if not h: return 0
    acnt, suffix = _acnt()
    res = api_call("get", f"{_prod_url()}/uapi/domestic-stock/v1/trading/inquire-psbl-order",
        headers=h, params={
            "CANO": acnt, "ACNT_PRDT_CD": suffix,
            "PDNO": STOCK_CODE, "ORD_UNPR": "0", "ORD_DVSN": "02",
            "CMA_EVLU_AMT_ICLD_YN": "Y", "OVRS_ICLD_YN": "N"
        })
    try:
        out = res["output"]
        # nrcvb_buy_amt: 당일 매수가능금액 (전일매도 미결제 포함)
        # TOTAL_BUDGET으로 상한 제한
        available = int(out.get("nrcvb_buy_amt") or out.get("ord_psbl_cash") or 0)
        return min(available, TOTAL_BUDGET)
    except:
        return 0

def send_order(side, qty, price):
    tr = ("VTTC0802U" if IS_MOCK else "TTTC0802U") if side == "BUY" else \
         ("VTTC0801U" if IS_MOCK else "TTTC0801U")
    h = kis_headers(tr)
    if not h: return False
    acnt, suffix = _acnt()
    res = api_call("post", f"{_prod_url()}/uapi/domestic-stock/v1/trading/order-cash",
        headers=h, json={
            "CANO": acnt, "ACNT_PRDT_CD": suffix,
            "PDNO": STOCK_CODE, "ORD_DVSN": "00",
            "ORD_QTY": str(qty), "ORD_UNPR": str(price),
        })
    rt = res.get("rt_cd","")
    if rt == "0":
        cprint(f"[주문 성공] {side} {qty}주 @ {price:,}원", Fore.GREEN)
        return True
    cprint(f"[주문 실패] {res.get('msg1','')}", Fore.RED)
    return False

# ============================================================
# [6] 나스닥 데이터
# ============================================================
def get_nasdaq_returns(days=3):
    """Yahoo Finance에서 나스닥 최근 N일 일간 수익률 반환"""
    try:
        r = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/%5EIXIC?interval=1d&range=10d",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=10
        )
        data = r.json()["chart"]["result"][0]
        closes = [c for c in data["indicators"]["quote"][0]["close"] if c is not None]
        if len(closes) < 2:
            return []
        rets = [(closes[i] - closes[i-1]) / closes[i-1] * 100
                for i in range(1, len(closes))]
        return rets[-days:]
    except Exception as e:
        cprint(f"[나스닥 조회 오류] {e}", Fore.YELLOW)
        return []

def check_nasdaq_signal():
    """매수 신호 확인. 반환: (신호강도, 메시지)
    0: 없음, 1: 일반, 2: 강함(연속 하락)"""
    rets = get_nasdaq_returns(3)
    if not rets:
        return 0, "나스닥 데이터 없음"

    last   = rets[-1]
    prev   = rets[-2] if len(rets) >= 2 else 0

    msg = f"나스닥 전일 {last:+.2f}% / 전전일 {prev:+.2f}%"

    # 연속 2일 하락
    if last <= STRONG_THRESHOLD and prev <= STRONG_THRESHOLD:
        return 2, f"🔥 연속 2일 하락! {msg}"

    # 단일 하락
    if last <= THRESHOLD:
        return 1, f"📉 하락 신호 {msg}"

    return 0, f"⏸ 신호 없음 {msg}"

# ============================================================
# [7] 매매 상태
# ============================================================
bot = {
    "has_stock":  False,
    "buy_price":  0,
    "qty":        0,
    "high_price": 0,
    "buy_time":   0,
    "signal":     0,      # 0:없음 1:일반 2:강함
    "running":    True,
}
daily_pnl    = 0
trade_count  = 0
_last_reset  = None
_signal_checked_date = None  # 오늘 나스닥 신호 체크했는지

def save_state():
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({**bot, "daily_pnl": daily_pnl,
                       "trade_count": trade_count,
                       "ts": time.time()}, f, ensure_ascii=False)
    except Exception as e:
        cprint(f"[상태 저장 오류] {e}", Fore.YELLOW)

def load_state():
    global bot, daily_pnl, trade_count
    if not os.path.exists(STATE_FILE):
        return
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        bot.update({k: data[k] for k in bot if k in data})
        daily_pnl   = data.get("daily_pnl", 0)
        trade_count = data.get("trade_count", 0)
        cprint(f"✅ 상태 복원 | 보유:{bot['has_stock']} 일간:{daily_pnl:+,}원", Fore.CYAN)
    except Exception as e:
        cprint(f"[상태 로드 오류] {e}", Fore.YELLOW)

def log_trade(side, price, qty, pnl=0, reason=""):
    try:
        exists = os.path.exists(LOG_FILE)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            if not exists:
                f.write("dt,side,code,qty,price,pnl_krw,reason\n")
            f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')},"
                    f"{side},{STOCK_CODE},{qty},{price},{pnl},{reason}\n")
    except Exception as e:
        cprint(f"[로그 오류] {e}", Fore.YELLOW)

def check_daily_reset():
    global daily_pnl, trade_count, _last_reset, _signal_checked_date
    today = date.today()
    if _last_reset != today:
        _last_reset = today
        daily_pnl   = 0
        trade_count = 0
        _signal_checked_date = None
        cprint("🔄 일간 초기화", Fore.CYAN)

# ============================================================
# [8] 매수/매도
# ============================================================
def do_buy(signal_strength):
    global trade_count
    if bot["has_stock"]:
        return False
    if not bot["running"]:
        return False

    price = get_price(STOCK_CODE)
    if price <= 0:
        send_msg("❌ 매수 실패 — 가격 조회 실패")
        return False

    cash  = get_cash()
    budget = min(ORDER_BUDGET_KRW, cash)
    qty   = int(budget / price)
    if qty <= 0:
        send_msg(f"❌ 매수 실패 — 잔고 부족 ({cash:,}원)")
        return False

    ok = send_order("BUY", qty, 0)  # 시장가
    if ok:
        bot.update({
            "has_stock": True,
            "buy_price": order_price,
            "qty": qty,
            "high_price": order_price,
            "buy_time": time.time(),
            "signal": signal_strength,
        })
        save_state()
        log_trade("BUY", order_price, qty, reason=f"나스닥신호{signal_strength}")
        send_msg(
            f"🛒 매수 완료!\n"
            f"종목: {STOCK_CODE} (KODEX 인버스)\n"
            f"가격: {order_price:,}원 × {qty}주\n"
            f"신호 강도: {'🔥강함' if signal_strength==2 else '📉일반'}\n"
            f"TP: +{TP_PCT}%  SL: {SL_PCT}%"
        )
        trade_count += 1
        return True
    return False

def do_sell(reason=""):
    global daily_pnl
    if not bot["has_stock"]:
        return False

    price = get_price(STOCK_CODE)
    if price <= 0:
        return False

    tick  = get_tick_size(price)
    order_price = price - tick
    qty   = bot["qty"]

    ok = send_order("SELL", qty, order_price)
    if ok:
        buy_p = bot["buy_price"]
        pnl   = (order_price - buy_p) * qty
        pnl_pct = (order_price - buy_p) / buy_p * 100
        daily_pnl += pnl

        log_trade("SELL", order_price, qty, pnl=pnl, reason=reason)
        send_msg(
            f"💰 매도 완료! [{reason}]\n"
            f"가격: {order_price:,}원 (매수가: {buy_p:,}원)\n"
            f"손익: {pnl:+,}원 ({pnl_pct:+.2f}%)\n"
            f"오늘 누적: {daily_pnl:+,}원"
        )
        bot.update({
            "has_stock": False, "buy_price": 0,
            "qty": 0, "high_price": 0, "buy_time": 0, "signal": 0,
        })
        save_state()
        return True
    return False

# ============================================================
# [9] 나스닥 신호 체크 루프 (매일 06:00)
# ============================================================
_pending_signal   = 0    # 오늘 매수할 신호 강도
_pending_signal_msg = ""

def nasdaq_check_loop():
    global _signal_checked_date, _pending_signal, _pending_signal_msg
    while True:
        try:
            now = datetime.now()
            today = date.today()
            # 매일 06:00~06:10 사이에 체크 (나스닥 마감 후)
            # 06:00~08:59 사이 매 시 정각에 재시도 (네트워크 오류 대비)
            if 6 <= now.hour <= 8 and now.minute < 10:
                if _signal_checked_date != today:
                    signal, msg = check_nasdaq_signal()
                    _signal_checked_date = today
                    _pending_signal      = signal
                    _pending_signal_msg  = msg
                    cprint(f"[나스닥 신호] {msg}", Fore.CYAN)
                    if signal > 0:
                        send_msg(f"📡 나스닥 신호 감지!\n{msg}\n→ 09:00 매수 예정")
                    else:
                        send_msg(f"📡 오늘 신호 없음\n{msg}", )
        except Exception as e:
            cprint(f"[신호 체크 오류] {e}", Fore.YELLOW)
        time.sleep(60)

# ============================================================
# [10] 메인 매매 루프
# ============================================================
def trading_loop():
    global _pending_signal, _pending_signal_msg

    while True:
        try:
            if not ENABLED or not bot["running"]:
                time.sleep(10); continue

            now  = datetime.now()
            h, m = now.hour, now.minute
            check_daily_reset()
            poll_ipc()

            # 장 시간 외 스킵
            if not ((9 <= h < 15) or (h == 15 and m < 25)):
                time.sleep(10); continue

            # ── 09:00~09:50 변동성 돌파 매수 ─────────────────
            if h == 9 and _pending_signal > 0 and not bot["has_stock"]:
                price_now = get_price(STOCK_CODE)
                if price_now > 0:
                    if not hasattr(trading_loop, '_open_price') or \
                       not hasattr(trading_loop, '_open_date') or \
                       trading_loop._open_date != date.today():
                        trading_loop._open_price = price_now
                        trading_loop._open_date  = date.today()
                        cprint(f'[인버스 시가] {price_now:,}원', Fore.CYAN)
                    open_p    = trading_loop._open_price
                    vb_target = open_p * 1.003
                    if price_now >= vb_target or m < 3:
                        cprint(f'[매수] 신호:{_pending_signal} 현재:{price_now:,} 목표:{vb_target:,.0f}', Fore.CYAN)
                        do_buy(_pending_signal)
                        _pending_signal = 0
                    elif m >= 50:
                        cprint(f'[매수 포기] 돌파 실패', Fore.YELLOW)
                        send_msg(f'⚠️ 돌파 미확인 — 오늘 매수 포기\n목표: {vb_target:,.0f}원 / 현재: {price_now:,}원')
                        _pending_signal = 0

            # ── 보유 중 TP/SL 체크 ──────────────────────────────
            if bot["has_stock"]:
                price = get_price(STOCK_CODE)
                if price > 0:
                    buy_p   = bot["buy_price"]
                    pnl_pct = (price - buy_p) / buy_p * 100

                    # 고점 갱신
                    if price > bot["high_price"]:
                        bot["high_price"] = price

                    # 익절
                    if pnl_pct >= TP_PCT:
                        do_sell(f"익절 {pnl_pct:+.2f}%")

                    # 손절
                    elif pnl_pct <= SL_PCT:
                        do_sell(f"손절 {pnl_pct:+.2f}%")

                    # 15:20 강제 청산
                    elif h == 15 and m >= 20:
                        do_sell("장마감 강제청산")

            time.sleep(5)

        except Exception as e:
            cprint(f"[루프 오류] {e}", Fore.RED)
            cprint(traceback.format_exc(), Fore.RED)
            time.sleep(10)

# ============================================================
# [11] 텔레그램 명령 처리
# ============================================================
def handle_command(text):
    global _pending_signal, _pending_signal_msg
    global TP_PCT, SL_PCT, THRESHOLD, STRONG_THRESHOLD, ORDER_BUDGET_KRW, ENABLED
    cmd = text.strip().split()
    if not cmd: return
    c = cmd[0].lower()

    if c in ("/status", "/s", "/상태"):
        now = datetime.now()
        if bot["has_stock"]:
            price   = get_price(STOCK_CODE)
            buy_p   = bot["buy_price"]
            pnl_pct = (price - buy_p) / buy_p * 100 if buy_p > 0 else 0
            hold_m  = int((time.time() - bot["buy_time"]) / 60)
            msg = (
                f"📉 인버스봇 상태\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📦 보유중 {STOCK_CODE}\n"
                f"매수가: {buy_p:,}원  현재가: {price:,}원\n"
                f"손익: {pnl_pct:+.2f}%  보유: {hold_m}분\n"
                f"TP: +{TP_PCT}%  SL: {SL_PCT}%\n"
                f"─────────────────\n"
                f"오늘 손익: {daily_pnl:+,}원  거래: {trade_count}회"
            )
        else:
            signal, smsg = check_nasdaq_signal()
            msg = (
                f"📉 인버스봇 상태\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"⏳ 대기중\n"
                f"오늘 신호: {'있음' if _pending_signal > 0 else '없음'}\n"
                f"나스닥: {smsg}\n"
                f"─────────────────\n"
                f"오늘 손익: {daily_pnl:+,}원  거래: {trade_count}회\n"
                f"TP: +{TP_PCT}%  SL: {SL_PCT}%  기준: {THRESHOLD}%"
            )
        _write_ipc_result(f"[normal] {msg}")
        if _ap_args.config is None:
            send_msg(msg)

    elif c in ("/sell", "/매도"):
        if bot["has_stock"]:
            do_sell("수동매도")
        else:
            _write_ipc_result("[normal] ⚠️ 보유 중인 종목 없음")

    elif c in ("/buy", "/매수"):
        signal, smsg = check_nasdaq_signal()
        if signal > 0:
            do_buy(signal)
        else:
            send_msg(f"⚠️ 현재 신호 없음\n{smsg}\n강제 매수: /buy force")
            if len(cmd) > 1 and cmd[1] == "force":
                do_buy(1)

    elif c in ("/signal", "/신호"):
        signal, smsg = check_nasdaq_signal()
        _pending_signal     = signal
        _pending_signal_msg = smsg
        _signal_checked_date = date.today()
        _write_ipc_result(f"[normal] 📡 나스닥 신호\n{smsg}\n{'✅ 오늘 매수 예정' if signal > 0 else '❌ 신호 없음'}")
        if _ap_args.config is None:
            send_msg(f"📡 나스닥 신호\n{smsg}")

    elif c in ("/start", "/시작"):
        bot["running"] = True
        save_state()
        _write_ipc_result("[normal] ▶️ 인버스봇 시작")

    elif c in ("/stop", "/정지"):
        bot["running"] = False
        save_state()
        _write_ipc_result("[normal] ⏹ 인버스봇 정지")

    elif c == "/set" and len(cmd) >= 3:
        key, val = cmd[1], cmd[2]
        try:
            if key == "tp":
                TP_PCT = float(val)
            elif key == "sl":
                SL_PCT = float(val)
            elif key == "threshold":
                THRESHOLD = float(val)
            elif key == "strong":
                STRONG_THRESHOLD = float(val)
            elif key == "budget":
                ORDER_BUDGET_KRW = int(val)
            elif key == "enabled":
                ENABLED = val.lower() in ("true","1","on")
            _write_ipc_result(f"[normal] ✅ {key} = {val}")
        except ValueError:
            _write_ipc_result(f"[normal] ❌ 잘못된 값: {val}")

    elif c in ("/why", "/왜"):
        signal, smsg = check_nasdaq_signal()
        reasons = []
        if not ENABLED:          reasons.append("❌ 봇 비활성화")
        if not bot["running"]:   reasons.append("❌ 봇 정지 상태")
        if bot["has_stock"]:     reasons.append("❌ 이미 보유 중")
        if _pending_signal == 0: reasons.append("❌ 오늘 나스닥 신호 없음")
        now = datetime.now()
        if not (9 <= now.hour < 15): reasons.append("❌ 장 시간 외")
        if not reasons:          reasons.append("✅ 매수 가능 상태")
        msg = (
            f"🔍 왜 안사?\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"나스닥: {smsg}\n"
            f"─────────────────\n" +
            "\n".join(reasons)
        )
        _write_ipc_result(f"[normal] {msg}")
        if _ap_args.config is None:
            send_msg(msg)

    elif c in ("/help", "/도움말"):
        msg = (
            f"📉 인버스봇 명령어\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"/i status  — 현재 상태\n"
            f"/i signal  — 나스닥 신호 확인\n"
            f"/i sell    — 즉시 매도\n"
            f"/i buy     — 수동 매수\n"
            f"/i why     — 왜 안사?\n"
            f"/i start   — 봇 시작\n"
            f"/i stop    — 봇 정지\n"
            f"/i set tp 1.5      — 익절 변경\n"
            f"/i set sl -0.8     — 손절 변경\n"
            f"/i set threshold -2.0  — 나스닥 기준 변경\n"
            f"/i set budget 50000    — 예산 변경"
        )
        _write_ipc_result(f"[normal] {msg}")
        if _ap_args.config is None:
            send_msg(msg)

# ============================================================
# [12] 단독 실행 텔레그램 폴링
# ============================================================
_last_update_id = 0

def poll_telegram():
    global _last_update_id
    if _ap_args.config is not None:
        return
    try:
        res = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
            params={"offset": _last_update_id + 1, "timeout": 2},
            timeout=6
        ).json()
        for upd in res.get("result", []):
            _last_update_id = upd["update_id"]
            msg = upd.get("message", {})
            if str(msg.get("chat",{}).get("id","")) == str(CHAT_ID):
                text = msg.get("text","").strip()
                if text:
                    threading.Thread(target=handle_command, args=(text,), daemon=True).start()
    except Exception:
        pass

# ============================================================
# [13] 메인
# ============================================================
def main():
    cprint(f"📉 인버스봇 v{BOT_VERSION} 시작", Fore.CYAN, bright=True)
    load_config()
    load_state()

    # 시작 시 즉시 신호 체크
    signal, msg = check_nasdaq_signal()
    global _pending_signal, _pending_signal_msg, _signal_checked_date
    _pending_signal     = signal
    _pending_signal_msg = msg
    _signal_checked_date = date.today()
    cprint(f'[시작 시 신호 체크] {msg}', Fore.CYAN)
    if signal > 0:
        send_msg(f'📡 신호 감지!\n{msg}\n→ 09:00 매수 예정')
    # 나스닥 신호 체크 스레드
    threading.Thread(target=nasdaq_check_loop, daemon=True, name="nasdaq-check").start()

    send_msg(
        f"📉 인버스봇 v{BOT_VERSION} 시작!\n"
        f"예산: {ORDER_BUDGET_KRW:,}원\n"
        f"TP: +{TP_PCT}%  SL: {SL_PCT}%\n"
        f"나스닥 기준: {THRESHOLD}%\n"
        f"→ 매일 06:00 신호 체크 / 09:00 진입"
    )

    # 메인 루프
    trading_loop()

if __name__ == "__main__":
    main()
