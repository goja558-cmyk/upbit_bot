"""
==============================================================
  통합 매니저 v1.0
  - 코인 봇(업비트) 다종목 + 주식 봇(KIS) 통합 실행
  - 텔레그램 알림 통합 (매수/매도만 개별, 나머지는 요약)
  - 전체 자금 한도 공유 및 합산 손익 관리
  - 종목별 예산 비율 자동 배분 (향후 확장 가능)

  사용법:
    python3 manager.py

  manager_cfg.yaml 예시:
    telegram_token: "봇토큰"
    chat_id: "채팅ID"

    total_budget: 100000        # 전체 운용 예산 (원)
    total_daily_loss: -10000    # 전체 일일 손실 한도 (원)
    total_weekly_loss: -50000   # 전체 주간 손실 한도 (원)

    coins:
      - market: "KRW-XRP"
        budget_ratio: 0.4       # 전체 예산의 40%
        enabled: true
      - market: "KRW-DOGE"
        budget_ratio: 0.3
        enabled: true
      - market: "KRW-SOL"
        budget_ratio: 0.3
        enabled: false          # false면 실행 안 함

    stock:
      enabled: true
      budget_ratio: 0.0         # 주식봇은 자체 예산 사용 (0이면 total에서 배분 안 함)
      script: "sector_bot.py"   # 주식봇 파일명

    alert:
      summary_interval: 3600    # 요약 알림 주기 (초, 기본 1시간)
      trade_alert: true         # 매수/매도 즉시 알림
      heartbeat: true           # 하트비트 알림
==============================================================
"""

MANAGER_VERSION = "1.1"

import sys, os, time, json, yaml, csv, threading, subprocess, traceback, requests, shutil, hashlib
from datetime import datetime, date, timedelta
from collections import defaultdict

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

def cprint(text, color="", bright=False):
    from datetime import datetime as _dt
    ts     = _dt.now().strftime("%H:%M:%S")
    prefix = (Style.BRIGHT if bright else "") + color if COLOR_OK else ""
    print(f"{prefix}[{ts}] {text}{Style.RESET_ALL if COLOR_OK else ''}")

# ============================================================
# [1] 경로 설정
# ============================================================
BASE_DIR    = os.path.dirname(os.path.abspath(__file__))  # /home/trade/upbit_bot
KIS_BOT_DIR = "/home/trade/kis_bot"                       # 주식봇 분리 경로
CFG_FILE    = os.path.join(BASE_DIR, "manager_cfg.yaml")
SHARED_DIR  = os.path.join(BASE_DIR, "shared")
os.makedirs(SHARED_DIR,  exist_ok=True)
os.makedirs(KIS_BOT_DIR, exist_ok=True)

MANAGER_STATE_FILE = os.path.join(SHARED_DIR, "manager_state.json")
MANAGER_PID_FILE   = os.path.join(SHARED_DIR, "manager.pid")

# ============================================================
# [2] 설정 로드
# ============================================================
_cfg = {}
TELEGRAM_TOKEN = ""
CHAT_ID        = ""

# 전체 자금 한도
TOTAL_BUDGET        = 100_000
TOTAL_DAILY_LOSS    = -10_000
TOTAL_WEEKLY_LOSS   = -50_000

# 알림 설정
SUMMARY_INTERVAL    = 3600   # 요약 알림 주기 (1시간)
TRADE_ALERT         = True
HEARTBEAT_ALERT     = True

def load_config():
    global _cfg, TELEGRAM_TOKEN, CHAT_ID
    global TOTAL_BUDGET, TOTAL_DAILY_LOSS, TOTAL_WEEKLY_LOSS
    global SUMMARY_INTERVAL, TRADE_ALERT, HEARTBEAT_ALERT

    if not os.path.exists(CFG_FILE):
        # 기본 설정 파일 자동 생성
        _create_default_config()
        cprint(f"✅ {CFG_FILE} 기본 파일 생성됨. 수정 후 다시 실행하세요.", Fore.YELLOW)
        sys.exit(0)

    with open(CFG_FILE, encoding="utf-8") as f:
        _cfg = yaml.safe_load(f) or {}

    TELEGRAM_TOKEN = _cfg.get("telegram_token", "")
    CHAT_ID        = str(_cfg.get("chat_id", ""))

    if not TELEGRAM_TOKEN or not CHAT_ID:
        cprint("❌ manager_cfg.yaml에 telegram_token / chat_id 없음", Fore.RED)
        sys.exit(1)

    TOTAL_BUDGET      = int(_cfg.get("total_budget", 100_000))
    TOTAL_DAILY_LOSS  = int(_cfg.get("total_daily_loss", -10_000))
    TOTAL_WEEKLY_LOSS = int(_cfg.get("total_weekly_loss", -50_000))

    alert_cfg         = _cfg.get("alert", {})
    SUMMARY_INTERVAL  = int(alert_cfg.get("summary_interval", 3600))
    TRADE_ALERT       = bool(alert_cfg.get("trade_alert", True))
    HEARTBEAT_ALERT   = bool(alert_cfg.get("heartbeat", True))

    cprint(f"✅ 매니저 설정 로드 완료 (예산:{TOTAL_BUDGET:,}원)", Fore.GREEN)


def _create_default_config():
    default = """# 통합 매니저 설정 파일
telegram_token: "여기에_봇_토큰"
chat_id: "여기에_채팅_ID"

total_budget: 100000         # 전체 운용 예산 (원)
total_daily_loss: -10000     # 전체 일일 손실 한도 (원)
total_weekly_loss: -50000    # 전체 주간 손실 한도 (원)

# GitHub 자동 업데이트 설정 (세 파일 모두 이 값을 공유)
github_token: "ghp_여기에_토큰_입력"
github_repo:  "깃헙아이디/레포이름"   # 예) myname/trading-bot

coins:
  - market: "KRW-XRP"
    budget_ratio: 0.5        # 전체 예산의 50%
    enabled: true
  - market: "KRW-DOGE"
    budget_ratio: 0.5
    enabled: false

stock:
  enabled: false
  script: "sector_bot.py"       # 섹터봇 파일명 (manager.py와 같은 폴더)

alert:
  summary_interval: 3600     # 요약 알림 주기 (초, 기본 1시간)
  trade_alert: true          # 매수/매도 즉시 알림
  heartbeat: true            # 하트비트 알림
"""
    with open(CFG_FILE, "w", encoding="utf-8") as f:
        f.write(default)


# ============================================================
# [3] 텔레그램 통합 알림
#   - 매수/매도: 즉시 개별 알림
#   - 상태/지표: SUMMARY_INTERVAL 마다 요약 1건
#   - 알림 큐를 통해 중복/도배 방지
# ============================================================
_tg_lock       = threading.Lock()
_summary_queue = []           # (source, text) 요약 대기
_last_summary_ts = 0.0
_last_mgr_error_ts = 0.0  # 매니저 오류 알림 쿨다운

def send_msg(text, level="normal", source="매니저", force=False, keyboard=None):
    """통합 텔레그램 전송 — 실패 시 1회 재시도, 4096자 초과 시 분할."""
    if not TELEGRAM_TOKEN or not CHAT_ID:
        return

    h = datetime.now().hour
    if 2 <= h < 7 and level == "normal" and not force:
        return

    tagged = f"[{source}]\n{text}"
    chunks = [tagged[i:i+4000] for i in range(0, len(tagged), 4000)]

    with _tg_lock:
        for i, chunk in enumerate(chunks):
            payload = {
                "chat_id": CHAT_ID,
                "text":    chunk,
                "disable_notification": (level == "silent"),
            }
            if keyboard and i == len(chunks) - 1:
                payload["reply_markup"] = {"inline_keyboard": keyboard}
            # 전송 실패 시 1회 재시도
            for attempt in range(2):
                try:
                    res = requests.post(
                        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                        json=payload, timeout=5
                    )
                    if res.status_code == 200:
                        break
                    cprint(f"[텔레그램 오류] {res.status_code} (시도 {attempt+1})", Fore.YELLOW)
                except Exception as e:
                    cprint(f"[텔레그램 예외] {e} (시도 {attempt+1})", Fore.YELLOW)
                if attempt == 0:
                    time.sleep(1)


# ── Reply Keyboard & 상단 고정 메시지 ────────────────────────
_mgr_pinned_msg_id      = 0
_mgr_pinned_last_update = 0.0
MGR_PINNED_INTERVAL     = 30

MGR_REPLY_KEYBOARD = {
    "keyboard": [
        ["📊 전체현황",  "🪙 코인봇",   "📊 섹터봇"],
        ["⏯ 시작/정지", "🔴 전체정지", "💰 예산"],
        ["⚙️ 설정",      "📋 메뉴",     "🔍 왜안사?"],
    ],
    "resize_keyboard": True,
    "persistent":      True,
    "input_field_placeholder": "명령어 입력 또는 버튼 클릭",
}

MGR_REPLY_CMD_MAP = {
    "📊 전체현황":  "/s status",
    "🪙 코인봇":    "/bot_menu coin",
    "📊 섹터봇":    "/bot_menu stock",
    "⏯ 시작/정지": "/s start",
    "🔴 전체정지":  "/stop_all",
    "💰 예산":      "/budget_menu",
    "⚙️ 설정":      "/sys_menu",
    "📋 메뉴":      "/menu",
    "🔍 왜안사?":   "/s why",
}


def _tg_api(method: str, **kwargs) -> dict:
    """텔레그램 API 단순 호출 헬퍼."""
    try:
        res = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}",
            json=kwargs, timeout=5
        )
        return res.json()
    except Exception as e:
        cprint(f"[TG API 오류] {method}: {e}", Fore.YELLOW)
        return {}


def setup_manager_reply_keyboard():
    """매니저 시작 시 하단 Reply Keyboard 전송."""
    if not TELEGRAM_TOKEN or not CHAT_ID:
        return
    res = _tg_api(
        "sendMessage",
        chat_id=CHAT_ID,
        text="🔘",
        reply_markup=MGR_REPLY_KEYBOARD,
        disable_notification=True,
    )
    # 삭제 안 함 — 키보드 유지


def _read_bot_status(filename):
    try:
        path = os.path.join(SHARED_DIR, filename)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _build_mgr_pinned_text() -> str:
    """매니저 고정 메시지 수치 텍스트."""
    with _state_lock:
        states = dict(_worker_states)
    lines = [
        f"🤖 매니저 v{MANAGER_VERSION}  {'🟢' if not _global_stop else '🔴'}",
        f"━━━━━━━━━━━━━━",
        f"💰 오늘 {daily_total_pnl:+,}원  |  이번주 {weekly_total_pnl:+,}원",
        f"━━━━━━━━━━━━━━",
    ]
    for wid, st in states.items():
        hold_s = "📦" if st.get("holding") else "⏳"
        lines.append(f"{hold_s} {wid}: {st.get('pnl_today', 0):+,}원 ({st.get('trades', 0)}회)")
    lines.append(f"🕐 {datetime.now().strftime('%H:%M:%S')} 업데이트")
    return "\n".join(lines)


def init_mgr_pinned_message():
    global _mgr_pinned_msg_id
    if not TELEGRAM_TOKEN or not CHAT_ID:
        return
    res = _tg_api(
        "sendMessage",
        chat_id=CHAT_ID,
        text=_build_mgr_pinned_text(),
        disable_notification=True,
    )
    msg_id = res.get("result", {}).get("message_id")
    if msg_id:
        _mgr_pinned_msg_id = msg_id
        _tg_api("pinChatMessage", chat_id=CHAT_ID,
                message_id=msg_id, disable_notification=True)
        cprint(f"✅ 매니저 고정 메시지 설정 (msg_id={msg_id})", Fore.GREEN)


def update_mgr_pinned_message():
    global _mgr_pinned_msg_id, _mgr_pinned_last_update
    if not _mgr_pinned_msg_id:
        return
    now = time.time()
    if now - _mgr_pinned_last_update < MGR_PINNED_INTERVAL:
        return
    _mgr_pinned_last_update = now
    res = _tg_api("editMessageText", chat_id=CHAT_ID,
                  message_id=_mgr_pinned_msg_id, text=_build_mgr_pinned_text())
    if not res.get("ok"):
        _mgr_pinned_msg_id = 0
        init_mgr_pinned_message()


# ── 텔레그램 인라인 키보드 ─────────────────────────────────────
# 메인 메뉴: 조회/조작 2단 구조로 직관적 분리
KB_MAIN = [
    [{"text": "📊 전체현황",    "callback_data": "/s status"},
     {"text": "📋 오늘요약",    "callback_data": "/summary"},
     {"text": "💼 예산배분",    "callback_data": "/alloc"}],
    [{"text": "🪙 코인봇 →",   "callback_data": "/bot_menu coin"},
     {"text": "📈 주식봇 →",   "callback_data": "/bot_menu stock"}],
    [{"text": "⚙️ 시스템",      "callback_data": "/sys_menu"},
     {"text": "💰 예산변경",    "callback_data": "/budget_menu"},
     {"text": "🔴 전체정지",    "callback_data": "/stop_all"}],
]

# 코인봇 메뉴: 상태조회 먼저, 조작 다음
KB_COIN_BOT = [
    [{"text": "📊 상태조회",    "callback_data": "/bot_cmd coin status"},
     {"text": "🔍 왜 안사/팔?", "callback_data": "/s why"}],
    [{"text": "⏯ 시작/정지",   "callback_data": "/bot_cmd coin start"},
     {"text": "🔴 즉시매도",    "callback_data": "/bot_cmd coin sell"},
     {"text": "⚡ 공격모드",    "callback_data": "/bot_cmd coin aggressive"}],
    [{"text": "🔁 종목변경",    "callback_data": "/coin_switch_menu"},
     {"text": "⚙️ 수치설정",    "callback_data": "/coin_set_menu"},
     {"text": "🧪 분석",        "callback_data": "/analyze_menu"}],
    [{"text": "🧪 테스트 ON",   "callback_data": "/bot_cmd coin test on"},
     {"text": "테스트 OFF",     "callback_data": "/bot_cmd coin test off"}],
    [{"text": "◀️ 메인",        "callback_data": "/menu"}],
]

# 섹터봇 메뉴
KB_STOCK_BOT = [
    [{"text": "📊 상태조회",    "callback_data": "/bot_cmd stock status"},
     {"text": "🔍 스코어순위",  "callback_data": "/bot_cmd stock scores"}],
    [{"text": "⏯ 시작/정지",   "callback_data": "/bot_cmd stock start"},
     {"text": "🚨 KOFR대피",    "callback_data": "/bot_cmd stock kofr"},
     {"text": "🔄 리밸런싱",    "callback_data": "/bot_cmd stock rebalance"}],
    [{"text": "🔴 킬스위치",    "callback_data": "/bot_cmd stock kill"},
     {"text": "🟢 킬해제",      "callback_data": "/bot_cmd stock unkill"},
     {"text": "◀️ 메인",        "callback_data": "/menu"}],
]

# 코인봇 수치 설정 메뉴
KB_COIN_SET = [
    [{"text": "📊 현재 수치", "callback_data": "/bot_cmd coin status"}],
    [{"text": "RSI ➖1",    "callback_data": "/setdelta coin rsi_buy -1"},
     {"text": "── RSI ──",  "callback_data": "/bot_cmd coin status"},
     {"text": "RSI ➕1",    "callback_data": "/setdelta coin rsi_buy +1"}],
    [{"text": "익절 ➖0.1", "callback_data": "/setdelta coin target -0.1"},
     {"text": "── 익절 ──", "callback_data": "/bot_cmd coin status"},
     {"text": "익절 ➕0.1", "callback_data": "/setdelta coin target +0.1"}],
    [{"text": "손절 ➖0.1", "callback_data": "/setdelta coin max_loss -0.1"},
     {"text": "── 손절 ──", "callback_data": "/bot_cmd coin status"},
     {"text": "손절 ➕0.1", "callback_data": "/setdelta coin max_loss +0.1"}],
    [{"text": "눌림 ➖0.1", "callback_data": "/setdelta coin drop -0.1"},
     {"text": "── 눌림 ──", "callback_data": "/bot_cmd coin status"},
     {"text": "눌림 ➕0.1", "callback_data": "/setdelta coin drop +0.1"}],
    [{"text": "◀️ 코인봇", "callback_data": "/bot_menu coin"}],
]

# 주식봇 수치 설정 메뉴
KB_STOCK_SET = [
    [{"text": "📊 현재 수치", "callback_data": "/bot_cmd stock status"}],
    [{"text": "RSI ➖1",    "callback_data": "/setdelta stock rsi_buy -1"},
     {"text": "── RSI ──",  "callback_data": "/bot_cmd stock status"},
     {"text": "RSI ➕1",    "callback_data": "/setdelta stock rsi_buy +1"}],
    [{"text": "익절 ➖0.1", "callback_data": "/setdelta stock target -0.1"},
     {"text": "── 익절 ──", "callback_data": "/bot_cmd stock status"},
     {"text": "익절 ➕0.1", "callback_data": "/setdelta stock target +0.1"}],
    [{"text": "손절 ➖0.1", "callback_data": "/setdelta stock max_loss -0.1"},
     {"text": "── 손절 ──", "callback_data": "/bot_cmd stock status"},
     {"text": "손절 ➕0.1", "callback_data": "/setdelta stock max_loss +0.1"}],
    [{"text": "눌림 ➖0.1", "callback_data": "/setdelta stock drop -0.1"},
     {"text": "── 눌림 ──", "callback_data": "/bot_cmd stock status"},
     {"text": "눌림 ➕0.1", "callback_data": "/setdelta stock drop +0.1"}],
    [{"text": "◀️ 주식봇", "callback_data": "/bot_menu stock"}],
]

# 종목 전환 메뉴
KB_COIN_SWITCH = [
    [{"text": "🔁 XRP",    "callback_data": "/bot_cmd coin switch KRW-XRP"},
     {"text": "🔁 DOGE",   "callback_data": "/bot_cmd coin switch KRW-DOGE"},
     {"text": "🔁 SOL",    "callback_data": "/bot_cmd coin switch KRW-SOL"}],
    [{"text": "🔁 ETH",    "callback_data": "/bot_cmd coin switch KRW-ETH"},
     {"text": "🔁 BTC",    "callback_data": "/bot_cmd coin switch KRW-BTC"},
     {"text": "🔁 TRUMP",  "callback_data": "/bot_cmd coin switch KRW-TRUMP"}],
    [{"text": "🔁 PEPE",   "callback_data": "/bot_cmd coin switch KRW-PEPE"},
     {"text": "🔁 BONK",   "callback_data": "/bot_cmd coin switch KRW-BONK"},
     {"text": "🔁 SHIB",   "callback_data": "/bot_cmd coin switch KRW-SHIB"}],
    [{"text": "✏️ 직접입력", "callback_data": "/coin_switch_input"}],
    [{"text": "◀️ 코인봇", "callback_data": "/bot_menu coin"}],
]

# 자금관리 메뉴
KB_FUND = [
    [{"text": "💼 배분현황",    "callback_data": "/alloc"},
     {"text": "💰 예산변경",    "callback_data": "/budget_menu"}],
    [{"text": "🪙 종목추가",    "callback_data": "/coin"},
     {"text": "📋 종목목록",    "callback_data": "/coin list"}],
    [{"text": "◀️ 메인",        "callback_data": "/menu"}],
]

# 시스템 메뉴
KB_SYS = [
    [{"text": "⬆️ 업데이트",    "callback_data": "/update"},
     {"text": "🔖 버전확인",    "callback_data": "/version"}],
    [{"text": "🔄 전체재시작",  "callback_data": "/restart all"},
     {"text": "🪙 코인재시작",  "callback_data": "/restart coin"},
     {"text": "📈 주식재시작",  "callback_data": "/restart stock"}],
    [{"text": "⚙️ 매니저재시작","callback_data": "/restart manager"},
     {"text": "⏹ 전체정지",    "callback_data": "/stop"}],
    [{"text": "◀️ 메인",        "callback_data": "/menu"}],
]

# 구버전 호환
KB_COIN    = KB_COIN_SWITCH
KB_RESTART = KB_SYS


def queue_summary(source, text):
    """요약 큐에 추가 (일정 주기마다 한 번에 전송)."""
    _summary_queue.append((datetime.now().strftime("%H:%M"), source, text))


def flush_summary():
    """큐에 쌓인 요약 알림을 한 번에 전송."""
    global _last_summary_ts, _summary_queue
    if not _summary_queue:
        return
    now_ts = time.time()
    if now_ts - _last_summary_ts < SUMMARY_INTERVAL:
        return
    _last_summary_ts = now_ts

    lines = [f"📋 요약 알림 [{datetime.now().strftime('%H:%M')}]",
             "━━━━━━━━━━━━━━━━━━━━"]
    for ts, src, txt in _summary_queue[-20:]:   # 최대 20건
        lines.append(f"[{ts}] {src}: {txt}")
    _summary_queue.clear()
    send_msg("\n".join(lines), level="silent", source="매니저", force=True)


# ============================================================
# [4] 전체 자금 상태
# ============================================================
_state_lock    = threading.Lock()
_workers_lock  = threading.Lock()   # _workers 리스트 보호
_worker_states = {}   # {worker_id: {"pnl": 0, "trades": 0, ...}}

daily_total_pnl  = 0
weekly_total_pnl = 0
_last_reset_day  = None
_last_reset_week = None
_global_stop     = False   # 전체 손실 한도 초과 시 True


def register_worker(worker_id):
    with _state_lock:
        _worker_states[worker_id] = {
            "pnl_today":  0,
            "pnl_weekly": 0,
            "trades":     0,
            "wins":       0,
            "losses":     0,
            "holding":    False,
            "market":     worker_id,
            "last_pnl":   0,
        }


def report_trade(worker_id, pnl_krw, is_buy=False):
    """워커가 매매 완료 후 호출. 전체 손익 누적 및 한도 체크."""
    global daily_total_pnl, weekly_total_pnl, _global_stop

    with _state_lock:
        st = _worker_states.get(worker_id, {})
        if not is_buy:
            st["pnl_today"]  = st.get("pnl_today",  0) + pnl_krw
            st["pnl_weekly"] = st.get("pnl_weekly", 0) + pnl_krw
            st["trades"]     = st.get("trades", 0) + 1
            if pnl_krw >= 0:
                st["wins"]   = st.get("wins", 0) + 1
            else:
                st["losses"] = st.get("losses", 0) + 1
            st["last_pnl"]   = pnl_krw
            daily_total_pnl  += pnl_krw
            weekly_total_pnl += pnl_krw

        _worker_states[worker_id] = st

    # 전체 손실 한도 체크
    if daily_total_pnl <= TOTAL_DAILY_LOSS and not _global_stop:
        _global_stop = True
        send_msg(
            f"🚨 전체 일일 손실 한도 초과!\n"
            f"손실: {daily_total_pnl:+,}원 / 한도: {TOTAL_DAILY_LOSS:,}원\n"
            f"→ 모든 봇 신규 매수 정지",
            level="critical", source="매니저", force=True
        )

    if weekly_total_pnl <= TOTAL_WEEKLY_LOSS and not _global_stop:
        _global_stop = True
        send_msg(
            f"🚨 전체 주간 손실 한도 초과!\n"
            f"손실: {weekly_total_pnl:+,}원 / 한도: {TOTAL_WEEKLY_LOSS:,}원\n"
            f"→ 모든 봇 이번 주 정지",
            level="critical", source="매니저", force=True
        )

    _save_state()


def _save_state():
    try:
        with open(MANAGER_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "daily_total_pnl":  daily_total_pnl,
                "weekly_total_pnl": weekly_total_pnl,
                "workers":          _worker_states,
                "global_stop":      _global_stop,
                "saved_at":         datetime.now().isoformat(),
            }, f, ensure_ascii=False, indent=2)
    except Exception as e:
        cprint(f"[상태 저장 오류] {e}", Fore.YELLOW)


def _load_state():
    global daily_total_pnl, weekly_total_pnl, _global_stop
    if not os.path.exists(MANAGER_STATE_FILE):
        return
    try:
        with open(MANAGER_STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        daily_total_pnl  = data.get("daily_total_pnl", 0)
        weekly_total_pnl = data.get("weekly_total_pnl", 0)
        _global_stop     = data.get("global_stop", False)
        for wid, st in data.get("workers", {}).items():
            _worker_states[wid] = st
        cprint(f"✅ 이전 상태 복원 (일간:{daily_total_pnl:+,}원)", Fore.CYAN)
    except Exception as e:
        cprint(f"[상태 로드 오류] {e}", Fore.YELLOW)


def check_daily_reset():
    global daily_total_pnl, _last_reset_day, _global_stop
    today = date.today()
    if _last_reset_day != today:
        _last_reset_day  = today
        daily_total_pnl  = 0
        _global_stop     = False
        with _state_lock:
            for st in _worker_states.values():
                st["pnl_today"] = 0
        cprint("🔄 일간 손익 초기화", Fore.CYAN)


def check_weekly_reset():
    global weekly_total_pnl, _last_reset_week
    now  = datetime.now()
    week = now.isocalendar()[:2]
    if _last_reset_week != week and now.weekday() == 0:
        _last_reset_week  = week
        weekly_total_pnl  = 0
        with _state_lock:
            for st in _worker_states.values():
                st["pnl_weekly"] = 0
        cprint("🔄 주간 손익 초기화", Fore.CYAN)


# ============================================================
# [5] 예산 배분 계산
# ============================================================
def calc_budget(ratio):
    """비율에 따라 예산 계산. 최소 5000원."""
    return max(5_000, int(TOTAL_BUDGET * ratio))


# ============================================================
# [6] 코인 워커 (upbit_bot.py 를 subprocess로 실행)
# ============================================================
class CoinWorker:
    """업비트 봇 1종목을 별도 프로세스로 실행하는 워커."""

    def __init__(self, market, budget_ratio, bot_script="upbit_bot.py"):
        self.market       = market
        self.budget       = calc_budget(budget_ratio)
        self.bot_script   = os.path.join(BASE_DIR, bot_script)
        self.cfg_file     = os.path.join(BASE_DIR, f"upbit_cfg_{market.replace('KRW-','').lower()}.yaml")
        self.worker_id    = market
        self.process      = None
        self.thread       = None
        self._stop_event  = threading.Event()

    def _prepare_config(self):
        """종목별 yaml 설정 파일 생성/갱신.
        원본 upbit_cfg.yaml의 모든 키(github_token 포함)를 그대로 보존하고
        market/budget만 덮어씌운다.
        yaml.dump 대신 직접 문자열 조작으로 값 손상 방지.
        """
        base_cfg_file = os.path.join(BASE_DIR, "upbit_cfg.yaml")
        base_cfg = {}
        if os.path.exists(base_cfg_file):
            with open(base_cfg_file, encoding="utf-8") as f:
                base_cfg = yaml.safe_load(f) or {}

        # market / budget 덮어쓰기
        base_cfg["market"] = self.market
        base_cfg["budget"] = self.budget

        # yaml.dump 대신 직접 라인 단위 작성 — 특수문자 포함 토큰 손상 방지
        lines = []
        for key, val in base_cfg.items():
            if isinstance(val, str):
                # 문자열은 항상 쌍따옴표로 감싸서 변형 방지
                escaped = val.replace("\\", "\\\\").replace('"', '\\"')
                lines.append(f'{key}: "{escaped}"')
            elif isinstance(val, bool):
                lines.append(f'{key}: {"true" if val else "false"}')
            elif val is None:
                lines.append(f'{key}: null')
            else:
                lines.append(f'{key}: {val}')

        with open(self.cfg_file, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

        cprint(f"  [{self.market}] 설정 파일 생성: {self.cfg_file} (예산:{self.budget:,}원)", Fore.CYAN)

    def _run(self):
        """봇 프로세스 실행 및 감시."""
        self._prepare_config()
        register_worker(self.worker_id)
        self._last_lines = []

        while not self._stop_event.is_set():
            try:
                cprint(f"▶ [{self.market}] 봇 시작", Fore.GREEN, bright=True)
                self.process = subprocess.Popen(
                    [sys.executable, self.bot_script,
                     "--config", self.cfg_file],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1
                )

                # 로그 실시간 출력
                for line in self.process.stdout:
                    line = line.rstrip()
                    if line:
                        print(f"  [{self.market}] {line}")
                        self._last_lines.append(line)
                        if len(self._last_lines) > 20: self._last_lines.pop(0)
                        self._parse_log_line(line)

                self.process.wait()
                rc = self.process.returncode
                cprint(f"⚠ [{self.market}] 봇 종료 (코드:{rc})", Fore.YELLOW)

                if self._stop_event.is_set():
                    break
                if rc != 0:
                    last_log = "\n".join(getattr(self,"_last_lines",[])[-10:]) or "로그없음"
                    send_msg(f"💀 [{self.market}] 크래시(rc:{rc})\n{last_log[-600:]}",
                             level="critical", source="매니저", force=True)
                cprint(f"  [{self.market}] 10초 후 재시작...", Fore.YELLOW)
                time.sleep(10)

            except Exception as e:
                cprint(f"[{self.market}] 워커 오류: {e}", Fore.RED)
                time.sleep(10)

    def _parse_log_line(self, line):
        """봇 출력에서 매수/매도 이벤트 감지."""
        if "샀어요" in line or "매수 완료" in line:
            if TRADE_ALERT:
                send_msg(line, level="critical", source=f"🪙{self.market}")
        elif "팔았어요" in line or "매도 완료" in line:
            if TRADE_ALERT:
                send_msg(line, level="critical", source=f"🪙{self.market}")
                # 손익 파싱 시도
                self._try_parse_pnl(line)
        elif "손절" in line or "익절" in line or "트레일링" in line:
            if TRADE_ALERT:
                send_msg(line, level="critical", source=f"🪙{self.market}")
        elif "오류" in line or "ERROR" in line:
            send_msg(f"⚠️ {line[:100]}", level="normal", source=f"🪙{self.market}", force=True)

    def _try_parse_pnl(self, line):
        """로그에서 손익 금액 파싱 후 전체 손익에 반영."""
        try:
            import re
            m = re.search(r'이번\s*손익[:\s]*([+-]?[\d,]+)원', line)
            if m:
                pnl = int(m.group(1).replace(",", ""))
                report_trade(self.worker_id, pnl)
        except Exception as e:
            cprint(f"[손익 파싱 오류] {e}", Fore.YELLOW)

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True, name=f"worker-{self.market}")
        self.thread.start()
        cprint(f"✅ [{self.market}] 워커 스레드 시작", Fore.GREEN)

    def stop(self):
        self._stop_event.set()
        if self.process and self.process.poll() is None:
            self.process.terminate()
            cprint(f"⏹ [{self.market}] 봇 정지 신호 전송", Fore.YELLOW)


# ============================================================
# [7] 섹터봇 워커 (sector_bot.py subprocess)
#   - 기존 StockWorker(kis_bot.py) 를 SectorWorker(sector_bot.py) 로 교체
#   - IPC 파일명(cmd_stock / result_stock)은 그대로 유지 → 명령 중계 코드 변경 없음
#   - worker_id = "KIS-STOCK" 유지 → 손익 집계 코드 변경 없음
# ============================================================
class StockWorker:
    """섹터 로테이션 봇을 별도 프로세스로 실행하는 워커.
    클래스명은 하위 호환을 위해 StockWorker 유지."""

    def __init__(self, script="sector_bot.py"):
        self.script      = os.path.join(KIS_BOT_DIR, script)  # /home/trade/kis_bot/
        self.worker_id   = "KIS-STOCK"
        self.process     = None
        self.thread      = None
        self._stop_event = threading.Event()
        self._last_lines = []

    def _run(self):
        register_worker(self.worker_id)
        while not self._stop_event.is_set():
            try:
                cprint(f"▶ [주식봇] {self.script} 시작", Fore.GREEN, bright=True)
                self.process = subprocess.Popen(
                    [sys.executable, self.script],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    cwd=KIS_BOT_DIR                            # /home/trade/kis_bot/
                )
                for line in self.process.stdout:
                    line = line.rstrip()
                    if line:
                        print(f"  [섹터봇] {line}")
                        self._last_lines.append(line)
                        if len(self._last_lines) > 20:
                            self._last_lines.pop(0)
                        self._parse_log_line(line)

                self.process.wait()
                rc = self.process.returncode
                if self._stop_event.is_set():
                    break
                if rc != 0:
                    last_log = "\n".join(self._last_lines[-10:]) or "로그없음"
                    send_msg(f"💀 [섹터봇] 크래시(rc:{rc})\n{last_log[-600:]}",
                             level="critical", source="매니저", force=True)
                cprint("  [섹터봇] 10초 후 재시작...", Fore.YELLOW)
                time.sleep(10)
            except Exception as e:
                cprint(f"[섹터봇] 워커 오류: {e}", Fore.RED)
                time.sleep(10)

    def _parse_log_line(self, line):
        # 섹터봇 매수/매도 키워드
        if any(k in line for k in ["BUY]", "SELL]", "트레일링", "손절", "KOFR 대피", "리밸런싱 완료"]):
            if TRADE_ALERT:
                send_msg(line, level="critical", source="📊섹터봇")
            if "SELL]" in line or "손절" in line or "트레일링" in line:
                self._try_parse_pnl(line)
        elif "오류" in line or "ERROR" in line or "❌" in line:
            send_msg(f"⚠️ {line[:120]}", level="normal", source="📊섹터봇", force=True)

    def _try_parse_pnl(self, line):
        try:
            import re
            m = re.search(r'이번\s*손익[:\s]*([+-]?[\d,]+)원', line)
            if m:
                pnl = int(m.group(1).replace(",", ""))
                report_trade(self.worker_id, pnl)
        except Exception as e:
            cprint(f"[손익 파싱 오류] {e}", Fore.YELLOW)

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True, name="worker-sector")
        self.thread.start()
        cprint("✅ [섹터봇] 워커 스레드 시작", Fore.GREEN)

    def stop(self):
        self._stop_event.set()
        if self.process and self.process.poll() is None:
            self.process.terminate()
            cprint("⏹ [섹터봇] 정지 신호 전송", Fore.YELLOW)


# ============================================================
# [8] 텔레그램 명령 처리
# ============================================================
_last_tg_poll   = 0.0
_last_update_id  = 0
_tg_poll_lock    = threading.Lock()
_processed_cb    = set()    # 처리된 callback_query id 캐시 (중복 클릭 방지)
_processed_cb_ts = {}       # 타임스탬프 (오래된 것 정리용)


def poll_telegram():
    """메인 루프에서 호출 — 3초 간격 보장, 중복 실행 방지.
    handle_command를 스레드로 실행해서 블로킹 방지."""
    global _last_tg_poll, _last_update_id
    if not _tg_poll_lock.acquire(blocking=False):
        return
    try:
        if time.time() - _last_tg_poll < 3:
            return
        _last_tg_poll = time.time()
        try:
            res = requests.get(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
                params={"offset": _last_update_id + 1, "timeout": 2},
                timeout=6
            ).json()
            for upd in res.get("result", []):
                _last_update_id = upd["update_id"]

                # 일반 메시지
                msg = upd.get("message", {})
                if str(msg.get("chat", {}).get("id", "")) == str(CHAT_ID):
                    text = msg.get("text", "").strip()
                    if text:
                        threading.Thread(
                            target=handle_command, args=(text,), daemon=True
                        ).start()

                # 버튼 콜백 — answerCallbackQuery 먼저 (즉시 응답), 명령은 스레드로
                cb = upd.get("callback_query", {})
                if cb and str(cb.get("message", {}).get("chat", {}).get("id", "")) == str(CHAT_ID):
                    cb_id   = cb.get("id", "")
                    cb_data = cb.get("data", "").strip()

                    # 중복 콜백 필터링 (같은 버튼 연속 클릭)
                    now_ts = time.time()
                    # 30초 이상 된 캐시 정리
                    expired = [k for k, t in _processed_cb_ts.items() if now_ts - t > 30]
                    for k in expired:
                        _processed_cb.discard(k)
                        _processed_cb_ts.pop(k, None)

                    if cb_id in _processed_cb:
                        # 중복 — answerCallbackQuery만 보내고 명령 무시
                        try:
                            requests.post(
                                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery",
                                json={"callback_query_id": cb_id}, timeout=3
                            )
                        except Exception:
                            pass
                        continue

                    _processed_cb.add(cb_id)
                    _processed_cb_ts[cb_id] = now_ts

                    # 즉시 응답 (버튼 로딩 해제)
                    try:
                        requests.post(
                            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery",
                            json={"callback_query_id": cb_id}, timeout=3
                        )
                    except Exception:
                        pass

                    # 명령 처리는 별도 스레드 (블로킹 방지)
                    if cb_data:
                        threading.Thread(
                            target=handle_command, args=(cb_data,), daemon=True
                        ).start()
        except Exception as e:
            cprint(f"[매니저 텔레그램 폴링 오류] {e}", Fore.YELLOW)
    finally:
        _tg_poll_lock.release()


def handle_command(text):
    """텔레그램 명령 처리.
    _cmd_semaphore로 직렬화 — 버튼 빠른 연속 클릭 시 IPC 파일 충돌 방지."""
    if not text:
        return
    # Reply Keyboard 버튼 텍스트 → 명령어 변환
    text = MGR_REPLY_CMD_MAP.get(text.strip(), text)
    print(f"[DEBUG] raw text: {repr(text)}")
    print(f"[DEBUG] raw text: {repr(text)}")
    print(f"[DEBUG] raw text: {repr(text)}")
    print(f"[DEBUG] raw text: {repr(text)}")
    if not _cmd_semaphore.acquire(timeout=15):
        cprint(f"[명령 큐 타임아웃] {text[:30]}", Fore.YELLOW)
        return
    try:
        _handle_command_inner(text)
    finally:
        _cmd_semaphore.release()


def _handle_command_inner(text):
    """handle_command 실제 구현 — 직렬화 래퍼와 분리."""
    text = text.strip().replace("\n","").replace("\r",""); cmd = text.split()

    # ── 메인 메뉴 ─────────────────────────────────────────────
    # /start 는 더 이상 매니저가 가로채지 않음 → 각 봇의 토글로 전달됨
    # 매니저 메뉴는 /menu 또는 /도움말 로만 열림
    if cmd[0] in ("/menu", "/도움말", "/help"):
        active = len([w for w in list(_workers) if w.thread and w.thread.is_alive()])
        send_msg(
            f"🤖 통합 매니저 v{MANAGER_VERSION}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"실행 중: {active}개 봇  |  예산: {TOTAL_BUDGET:,}원\n"
            f"오늘 손익: {daily_total_pnl:+,}원\n"
            f"─────────────────\n"
            f"단축 명령어\n"
            f"─────────────────\n"
            f"📊 조회\n"
            f"/status   전체 현황\n"
            f"/c status 코인봇 상태\n"
            f"/s status 주식봇 상태\n"
            f"/why      매수 안 되는 이유\n"
            f"─────────────────\n"
            f"⚙️ 설정 (코인/주식 공통)\n"
            f"/rsi 45    RSI 매수 기준\n"
            f"/tp  1.2   익절 목표 (%)\n"
            f"/sl -0.8   손절 기준 (%)\n"
            f"/drop 0.5  눌림 기준 (%)\n"
            f"/be  0.3   본절 보호 (%)\n"
            f"─────────────────\n"
            f"🎮 제어\n"
            f"/c sell   코인 즉시매도\n"
            f"/s sell   주식 즉시매도\n"
            f"/c start  코인봇 시작/정지\n"
            f"/s start  주식봇 시작/정지\n"
            f"─────────────────\n"
            f"🔧 시스템\n"
            f"/update   최신 코드 적용\n"
            f"/rollback 이전 버전 복원\n"
            f"─────────────────\n"
            f"버튼으로 조작하세요 👇",
            level="normal", source="매니저", force=True, keyboard=KB_MAIN
        )
        return

    # ── 단축 명령어 → 봇 브로드캐스트 ─────────────────────────
    # /rsi 45, /tp 1.2, /sl -0.8, /drop 0.5 → /set 변환 후 전달
    _shortcuts = {
        "/rsi":   "rsi_buy",
        "/tp":    "target",
        "/sl":    "max_loss",
        "/drop":  "drop",
        "/trail": "trail_start",
        "/gap":   "trail_gap",
        "/be":    "be_trigger",
    }
    if cmd[0] in _shortcuts and len(cmd) == 2:
        set_cmd = f"/set {_shortcuts[cmd[0]]} {cmd[1]}"
        _broadcast_to_all_bots(set_cmd)
        return

    # ── 봇별 메뉴 ────────────────────────────────────────────
    elif cmd[0] == "/bot_menu":
        target = cmd[1] if len(cmd) > 1 else ""
        workers_snap = list(_workers)   # 스냅샷 — iterate 중 변경 방지
        if target == "coin":
            coin_workers = [w for w in workers_snap if isinstance(w, CoinWorker)]
            if not coin_workers:
                send_msg("🪙 실행 중인 코인봇 없음\n/coin add KRW-XRP 0.5 로 추가하세요.",
                         level="normal", source="매니저", force=True, keyboard=KB_COIN_BOT)
                return
            markets = ", ".join(w.market for w in coin_workers)
            with _state_lock:
                states_snap = dict(_worker_states)
            lines = []
            for w in coin_workers:
                st = states_snap.get(w.market, {})
                holding = "📦보유중" if st.get("holding") else "⏳대기중"
                lines.append(f"{w.market}: {st.get('pnl_today',0):+,}원 {holding}")
            send_msg(
                f"🪙 코인봇\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"실행 종목: {markets}\n" +
                "\n".join(lines),
                level="normal", source="매니저", force=True, keyboard=KB_COIN_BOT
            )
        elif target == "stock":
            stock_workers = [w for w in workers_snap if isinstance(w, StockWorker)]
            if not stock_workers:
                send_msg("📊 섹터봇 실행 안 됨\nmanager_cfg.yaml에서 stock.enabled: true 설정하세요.",
                         level="normal", source="매니저", force=True, keyboard=KB_STOCK_BOT)
                return
            with _state_lock:
                st = dict(_worker_states).get("KIS-STOCK", {})
            holding = "📦보유중" if st.get("holding") else "⏳대기중"
            send_msg(
                f"📊 섹터봇\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"오늘 손익: {st.get('pnl_today',0):+,}원\n"
                f"거래: {st.get('trades',0)}회 {holding}",
                level="normal", source="매니저", force=True, keyboard=KB_STOCK_BOT
            )
        return

    # ── 봇 명령 중계 ─────────────────────────────────────────
    elif cmd[0] == "/c":
        # /c → 코인봇으로 명령 전달 (/c status, /c sell 등)
        sub = " ".join(cmd[1:]) if len(cmd) > 1 else "status"
        _forward_to_bot("coin", sub)
        return
    elif cmd[0] == "/s":
        # /s → 주식봇으로 명령 전달 (/s status, /s sell 등)
        sub = " ".join(cmd[1:]) if len(cmd) > 1 else "status"
        _forward_to_bot("stock", sub)
        return
    elif cmd[0] == "/set" and len(cmd) >= 3:
        # /set 명령어 직접 입력 시 코인봇으로 중계
        import uuid as _uuid
        req_id = _uuid.uuid4().hex[:8]
        sub_cmd = " ".join(cmd)
        coin_workers = [w for w in list(_workers) if isinstance(w, CoinWorker)]
        for w in coin_workers:
            _send_ipc_cmd(w.market, sub_cmd, req_id=req_id)
        for w in coin_workers:
            result = _read_ipc_result(w.market, timeout=5.0, req_id=req_id)
            if result:
                clean = result.replace("[critical] ","").replace("[normal] ","").replace("[silent] ","")
                if clean.strip():
                    send_msg(clean, level="normal", source=f"🪙{w.market.replace('KRW-','')}", force=True)
        return

    elif cmd[0] == "/bot_cmd":
        if len(cmd) < 3:
            send_msg("사용법: /bot_cmd coin start", level="normal", source="매니저", force=True)
            return
        import uuid as _uuid
        target  = cmd[1]
        sub_cmd = "/" + " ".join(cmd[2:])
        slow_cmds = ("/analyze", "/why", "/s status", "/balance", "/report", "/weekly", "/train")
        no_kb_cmds = ("/aggressive", "/normal", "/paper", "/test", "/reload", "/s start", "/stop", "/pause")
        timeout = 20.0 if sub_cmd.startswith("/balance") else 12.0 if any(sub_cmd.startswith(c) for c in slow_cmds) else 5.0
        use_kb = not any(sub_cmd.startswith(c) for c in no_kb_cmds)
        req_id  = _uuid.uuid4().hex[:8]
        workers_snap = list(_workers)

        if target == "coin":
            coin_workers = [w for w in workers_snap if isinstance(w, CoinWorker)]
            if not coin_workers:
                send_msg("🪙 실행 중인 코인봇 없음", level="normal", source="매니저", force=True)
                return
            for w in coin_workers:
                _send_ipc_cmd(w.market, sub_cmd, req_id=req_id)
            for w in coin_workers:
                result = _read_ipc_result(w.market, timeout=timeout, req_id=req_id)
                if result:
                    clean = result.replace("[critical] ","").replace("[normal] ","").replace("[silent] ","")
                    # 봇이 이미 [🪙 COIN] 태그를 달아서 보내므로 첫 줄 태그 제거
                    lines = clean.strip().splitlines()
                    if lines and lines[0].startswith("[") and lines[0].endswith("]"):
                        clean = "\n".join(lines[1:]).strip()
                    if clean.strip():
                        send_msg(clean, level="normal",
                                 source=f"🪙{w.market.replace('KRW-','')}",
                                 force=True, keyboard=KB_COIN_BOT if use_kb else None)

        elif target == "stock":
            stock_workers = [w for w in list(_workers) if isinstance(w, StockWorker)]
            if not stock_workers:
                send_msg("📈 주식봇 실행 안 됨", level="normal", source="매니저", force=True,
                         keyboard=KB_STOCK_BOT)
                return
            _send_ipc_cmd("stock", sub_cmd, req_id=req_id)
            result = _read_ipc_result("stock", timeout=timeout, req_id=req_id)
            if result:
                clean = result.replace("[critical] ","").replace("[normal] ","").replace("[silent] ","")
                if clean.strip():
                    send_msg(clean, level="normal", source="📈주식봇",
                             force=True, keyboard=KB_STOCK_BOT if use_kb else None)
        return

    # ── 종목 전환 메뉴 ────────────────────────────────────────
    elif cmd[0] == "/coin_switch_menu":
        send_msg("🔁 종목 전환", level="normal", source="매니저", force=True, keyboard=KB_COIN_SWITCH)
        return

    # ── 코인봇 수치 설정 메뉴 ─────────────────────────────────
    elif cmd[0] == "/coin_set_menu":
        send_msg(
            "⚙️ 코인봇 수치 설정\n버튼을 눌러 변경하거나\n직접 입력: /bot_cmd coin set drop 0.5",
            level="normal", source="매니저", force=True, keyboard=KB_COIN_SET
        )
        return

    # ── 주식봇 수치 설정 메뉴 ─────────────────────────────────
    elif cmd[0] == "/stock_set_menu":
        send_msg(
            "⚙️ 주식봇 수치 설정\n버튼을 눌러 변경하거나\n직접 입력: /bot_cmd stock set drop 0.8",
            level="normal", source="매니저", force=True, keyboard=KB_STOCK_SET
        )
        return

    # ── 자금관리 메뉴 ─────────────────────────────────────────
    elif cmd[0] == "/fund_menu":
        lines = [
            f"💼 자금관리",
            f"━━━━━━━━━━━━━━━━━━━━",
            f"전체 예산: {TOTAL_BUDGET:,}원",
            f"일간 손익: {daily_total_pnl:+,}원 / 한도: {TOTAL_DAILY_LOSS:,}원",
            f"주간 손익: {weekly_total_pnl:+,}원 / 한도: {TOTAL_WEEKLY_LOSS:,}원",
        ]
        send_msg("\n".join(lines), level="normal", source="매니저", force=True, keyboard=KB_FUND)
        return

    # ── 시스템 메뉴 ───────────────────────────────────────────
    elif cmd[0] == "/sys_menu":
        local  = _load_local_version()
        ver_str = f"{local['hash']}" if local else "정보없음"
        active  = len([w for w in list(_workers) if w.thread and w.thread.is_alive()])
        send_msg(
            f"⚙️ 시스템\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"매니저 v{MANAGER_VERSION} / 봇버전: {ver_str}\n"
            f"실행중 워커: {active}개",
            level="normal", source="매니저", force=True, keyboard=KB_SYS
        )
        return

    # ── 예산 메뉴 ────────────────────────────────────────────
    elif cmd[0] == "/budget_menu":
        send_msg(
            f"💰 예산 변경\n"
            f"현재: {TOTAL_BUDGET:,}원\n"
            f"변경하려면 직접 입력하세요:\n"
            f"예) /budget 50000",
            level="normal", source="매니저", force=True, keyboard=KB_FUND
        )
        return

    # ── 현황 ─────────────────────────────────────────────────
    elif cmd[0] in ("/s status", "/상태"):
        # 매니저 전체현황 먼저
        _send_status()
        # 각 봇 상세 상태도 함께 조회
        workers_snap = list(_workers)
        for w in [w for w in workers_snap if isinstance(w, CoinWorker)]:
            _send_ipc_cmd(w.market, "/s status")
            result = _read_ipc_result(w.market, timeout=5.0)
            if result:
                clean = result.replace("[critical] ", "").replace("[normal] ", "").replace("[silent] ", "")
                send_msg(clean, level="normal", source=f"🪙{w.market}", force=True, keyboard=KB_COIN_BOT)
        for w in [w for w in workers_snap if isinstance(w, StockWorker)]:
            _send_ipc_cmd("stock", "/s status")
            result = _read_ipc_result("stock", timeout=5.0)
            if result:
                clean = result.replace("[critical] ", "").replace("[normal] ", "").replace("[silent] ", "")
                send_msg(clean, level="normal", source="📈주식봇", force=True, keyboard=KB_STOCK_BOT)
        return

    # ── 요약 ─────────────────────────────────────────────────
    elif cmd[0] in ("/summary", "/요약"):
        _send_summary()

    # ── 예산 배분 ─────────────────────────────────────────────
    elif cmd[0] == "/alloc":
        _send_alloc()

    # ── 버전 ─────────────────────────────────────────────────
    elif cmd[0] in ("/version", "/버전"):
        local  = _load_local_version()
        latest = _gh_latest_commit()
        local_str  = f"{local['hash']} ({local['time']})" if local else "정보 없음"
        latest_str = f"{latest['hash']} ({latest['time']})" if latest else "조회 실패"
        up_to_date = local and latest and local.get("full") == latest.get("full")
        send_msg(
            f"🔖 버전 정보\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"현재: {local_str}\n"
            f"최신: {latest_str}\n"
            f"상태: {'✅ 최신' if up_to_date else '⬆️ 업데이트 있음'}",
            level="normal", source="매니저", force=True
        )

    # ── 업데이트 ─────────────────────────────────────────────
    elif cmd[0] in ("/rollback", "/롤백"):
        threading.Thread(target=do_rollback, daemon=True).start()
    elif cmd[0] in ("/update", "/업데이트"):
        force = len(cmd) > 1 and cmd[1].lower() == "force"
        holding = [wid for wid, st in _worker_states.items() if st.get("holding")]
        if holding and not force:
            send_msg(
                f"⚠️ 포지션 보유 중: {', '.join(holding)}\n"
                f"→ 청산 후 업데이트 권장\n"
                f"→ 강제 진행: /update force",
                level="critical", source="매니저", force=True
            )
            return
        threading.Thread(target=do_update, args=(force,), daemon=True).start()

    # ── 재시작 ───────────────────────────────────────────────
    elif cmd[0] in ("/restart", "/재시작"):
        if len(cmd) < 2:
            send_msg(
                "🔄 재시작 대상 선택",
                level="normal", source="매니저", force=True, keyboard=KB_RESTART
            )
            return
        target = cmd[1].lower()
        if target not in ("all", "coin", "stock", "manager"):
            send_msg("❌ 대상: all / coin / stock / manager", level="normal", source="매니저", force=True)
            return
        holding = [wid for wid, st in _worker_states.items() if st.get("holding")]
        if holding and target in ("all", "coin"):
            if not (len(cmd) > 2 and cmd[2].lower() == "force"):
                send_msg(
                    f"⚠️ 포지션 보유 중: {', '.join(holding)}\n→ 강제: /restart {target} force",
                    level="critical", source="매니저", force=True
                )
                return
        send_msg(f"🔄 재시작: {target}", level="critical", source="매니저", force=True)
        threading.Thread(target=do_restart, args=(target,), daemon=True).start()

    # ── 종목 관리 ─────────────────────────────────────────────
    elif cmd[0] == "/coin":
        if len(cmd) < 2:
            _send_coin_menu()
            return
        sub = cmd[1].lower()

        if sub == "list":
            _send_coin_list()

        elif sub == "add":
            # /coin add KRW-XRP 0.5
            if len(cmd) < 3:
                send_msg("사용법: /coin add KRW-XRP 0.5", level="normal", source="매니저", force=True)
                return
            market = cmd[2].upper()
            if not market.startswith("KRW-"):
                market = "KRW-" + market
            ratio  = float(cmd[3]) if len(cmd) > 3 else 0.5
            _coin_add(market, ratio)

        elif sub == "remove":
            # /coin remove KRW-XRP
            if len(cmd) < 3:
                send_msg("사용법: /coin remove KRW-XRP", level="normal", source="매니저", force=True)
                return
            market = cmd[2].upper()
            if not market.startswith("KRW-"):
                market = "KRW-" + market
            _coin_remove(market)

        elif sub in ("enable", "disable", "on", "off"):
            # /coin enable KRW-XRP
            if len(cmd) < 3:
                send_msg(f"사용법: /coin {sub} KRW-XRP", level="normal", source="매니저", force=True)
                return
            market  = cmd[2].upper()
            if not market.startswith("KRW-"):
                market = "KRW-" + market
            enabled = sub in ("enable", "on")
            _coin_toggle(market, enabled)

        else:
            send_msg(
                "🪙 종목 관리 명령어\n"
                "/coin list              — 현재 종목 목록\n"
                "/coin add KRW-DOGE 0.3 — 종목 추가 (비율 0.3=30%)\n"
                "/coin remove KRW-DOGE  — 종목 제거\n"
                "/coin enable KRW-DOGE  — 종목 켜기\n"
                "/coin disable KRW-DOGE — 종목 끄기",
                level="normal", source="매니저", force=True, keyboard=KB_COIN
            )

    # ── 전체 예산 변경 ────────────────────────────────────────
    elif cmd[0] == "/budget":
        if len(cmd) < 2:
            send_msg(
                f"💰 현재 전체 예산: {TOTAL_BUDGET:,}원\n"
                f"변경: /budget 50000",
                level="normal", source="매니저", force=True
            )
            return
        try:
            val = int(cmd[1].replace(",", ""))
            if val < 10_000:
                send_msg("❌ 최소 10,000원 이상으로 설정하세요.", level="normal", source="매니저", force=True)
                return
            _update_total_budget(val)
        except:
            send_msg("❌ 숫자로 입력하세요. 예) /budget 50000", level="normal", source="매니저", force=True)

    # ── 분석 메뉴 ────────────────────────────────────────────
    elif cmd[0] == "/analyze_menu":
        workers_snap = list(_workers)
        coin_workers = [w for w in workers_snap if isinstance(w, CoinWorker)]
        if not coin_workers:
            send_msg("🪙 실행 중인 코인봇 없음", level="normal", source="매니저", force=True)
            return
        for w in coin_workers:
            _send_ipc_cmd(w.market, "/analyze")
        send_msg("🔍 분석 시작...", level="normal", source="매니저", force=True)

    elif cmd[0] == "/analyze_menu_stock":
        workers_snap = list(_workers)
        stock_workers = [w for w in workers_snap if isinstance(w, StockWorker)]
        if not stock_workers:
            send_msg("📈 실행 중인 주식봇 없음", level="normal", source="매니저", force=True)
            return
        _send_ipc_cmd("stock", "/analyze")
        send_msg("🔍 분석 시작...", level="normal", source="매니저", force=True)

    elif cmd[0] == "/coin_switch_input":
        send_msg(
            "✏️ 전환할 종목 입력:\n/bot_cmd coin switch KRW-TRUMP",
            level="normal", source="매니저", force=True,
            keyboard=[[{"text": "◀️ 종목변경", "callback_data": "/coin_switch_menu"}]]
        )

    # ── 수치 델타 조정 ────────────────────────────────────────
    elif cmd[0] == "/setdelta":
        if len(cmd) < 4:
            return
        target, key = cmd[1], cmd[2]
        try: delta = float(cmd[3])
        except ValueError: return
        workers_snap = list(_workers)
        targets_w = [w for w in workers_snap if isinstance(w, CoinWorker)] if target=="coin"                     else [w for w in workers_snap if isinstance(w, StockWorker)]
        kb = KB_COIN_SET if target=="coin" else KB_STOCK_SET
        if not targets_w: return
        ipc_id = targets_w[0].market if target=="coin" else "stock"
        import re as _re
        _send_ipc_cmd(ipc_id, "/s status")
        result = _read_ipc_result(ipc_id, timeout=4.0) or ""
        cur_val = None
        m = _re.search(rf"(?:^|\s){_re.escape(key)}[=:\s]+([+-]?[\d.]+)", result, _re.M)
        if m:
            try: cur_val = float(m.group(1))
            except ValueError: pass
        if cur_val is None:
            defaults = {"rsi_buy":38,"target":0.6,"max_loss":-0.5,"drop":0.1}
            cur_val = defaults.get(key, 0)
        new_val = round(cur_val + delta, 3)
        sc = f"/set {key} {new_val}"
        if target == "coin":
            for w in targets_w: _send_ipc_cmd(w.market, sc)
            r = _read_ipc_result(targets_w[0].market, timeout=5.0)
        else:
            _send_ipc_cmd("stock", sc)
            r = _read_ipc_result("stock", timeout=5.0)
        msg = r.replace("[critical] ","").replace("[normal] ","").replace("[silent] ","") if r               else f"✅ {key}: {cur_val} → {new_val}"
        send_msg(msg, level="normal", source="매니저", force=True, keyboard=kb)

    # ── 분석 추천값 적용 ──────────────────────────────────────
    elif cmd[0] == "/apply_recommend":
        if len(cmd) < 2: return
        keys = ["rsi_buy","target","max_loss","drop","vol_min","vol_max","timeout_min"]
        applied = []
        workers_snap  = list(_workers)
        coin_workers  = [w for w in workers_snap if isinstance(w, CoinWorker)]
        stock_workers = [w for w in workers_snap if isinstance(w, StockWorker)]
        for i, k in enumerate(keys):
            if i+1 < len(cmd):
                try:
                    v = float(cmd[i+1])
                    sc = f"/set {k} {v}"
                    for w in coin_workers: _send_ipc_cmd(w.market, sc)
                    if stock_workers: _send_ipc_cmd("stock", sc)
                    applied.append(f"{k}={v}")
                except ValueError: pass
        time.sleep(0.3)
        send_msg("✅ 추천값 적용!\n" + "  ".join(applied) if applied else "⚠️ 적용 실패",
                 level="critical", source="매니저", force=True)

        # ── 전체 정지 (매니저 레벨) ──────────────────────────────
    elif cmd[0] == "/stop_all":
        send_msg("⏹ 전체 봇 프로세스 정지 중...", level="critical", source="매니저", force=True)
        for w in list(_workers):
            w.stop()

    else:
        # ── 브로드캐스트: 매니저가 모르는 명령은 모든 봇에 전달 ──
        _broadcast_to_all_bots(text)


def _broadcast_to_all_bots(cmd_text: str):
    """매니저가 모르는 명령을 모든 봇에 IPC로 브로드캐스트.
    고유 request_id를 써서 _poll_ipc_results와 파일 경쟁 방지."""
    import uuid as _uuid
    workers_snap = list(_workers)
    if not workers_snap:
        send_msg("⚠️ 실행 중인 봇이 없습니다.", level="normal", source="매니저", force=True)
        return

    coin_workers  = [w for w in workers_snap if isinstance(w, CoinWorker)]
    stock_workers = [w for w in workers_snap if isinstance(w, StockWorker)]

    # 고유 ID로 결과 파일 구분 (poll_ipc_results와 경쟁 방지)
    req_id = _uuid.uuid4().hex[:8]

    # IPC 전송 (req_id 포함)
    for w in coin_workers:
        _send_ipc_cmd(w.market, cmd_text, req_id=req_id)
    if stock_workers:
        _send_ipc_cmd("stock", cmd_text, req_id=req_id)

    # 결과 수집
    for w in coin_workers:
        result = _read_ipc_result(w.market, timeout=6.0, req_id=req_id)
        if result:
            level = "critical" if "[critical]" in result else "normal"
            clean = (result
                     .replace("[critical] ", "")
                     .replace("[normal] ", "")
                     .replace("[silent] ", ""))
            send_msg(clean, level=level, source=f"🪙{w.market}",
                     force=True, keyboard=KB_COIN_BOT)

    if stock_workers:
        result = _read_ipc_result("stock", timeout=6.0, req_id=req_id)
        if result:
            level = "critical" if "[critical]" in result else "normal"
            clean = (result
                     .replace("[critical] ", "")
                     .replace("[normal] ", "")
                     .replace("[silent] ", ""))
            send_msg(clean, level=level, source="📈주식봇",
                     force=True, keyboard=KB_STOCK_BOT)


def _send_coin_menu():
    """종목 관리 메뉴 전송."""
    coins = _cfg.get("coins", [])
    lines = ["🪙 종목 관리", "━━━━━━━━━━━━━━━━━━━━"]
    for c in coins:
        status = "🟢" if c.get("enabled") else "🔴"
        budget = calc_budget(c.get("budget_ratio", 0))
        lines.append(f"{status} {c['market']} ({c.get('budget_ratio',0)*100:.0f}% = {budget:,}원)")
    send_msg("\n".join(lines), level="normal", source="매니저", force=True, keyboard=KB_COIN)


def _send_coin_list():
    """현재 종목 목록 전송."""
    coins = _cfg.get("coins", [])
    if not coins:
        send_msg("등록된 종목 없음", level="normal", source="매니저", force=True)
        return
    lines = [f"🪙 종목 목록 (전체예산: {TOTAL_BUDGET:,}원)", "━━━━━━━━━━━━━━━━━━━━"]
    for c in coins:
        status = "🟢 실행중" if c.get("enabled") else "🔴 중단"
        budget = calc_budget(c.get("budget_ratio", 0))
        lines.append(f"{status} {c['market']}\n  비율:{c.get('budget_ratio',0)*100:.0f}% 예산:{budget:,}원")

    # 비율 합계 체크
    total_ratio = sum(c.get("budget_ratio", 0) for c in coins if c.get("enabled"))
    lines.append(f"─────────────────")
    lines.append(f"활성 비율 합계: {total_ratio*100:.0f}% {'✅' if abs(total_ratio-1.0)<0.01 else '⚠️ 합계가 100%가 아님'}")
    send_msg("\n".join(lines), level="normal", source="매니저", force=True, keyboard=KB_COIN)


def _coin_add(market, ratio):
    """종목 추가 및 워커 시작."""
    global _workers
    coins = _cfg.get("coins", [])

    # 이미 있으면 업데이트
    for c in coins:
        if c["market"] == market:
            c["enabled"]      = True
            c["budget_ratio"] = ratio
            _save_cfg()
            send_msg(
                f"✅ {market} 설정 업데이트\n비율: {ratio*100:.0f}% = {calc_budget(ratio):,}원\n→ 재시작 중...",
                level="critical", source="매니저", force=True
            )
            # 기존 워커 재시작
            threading.Thread(target=do_restart, args=("coin",), daemon=True).start()
            return

    # 신규 추가
    coins.append({"market": market, "budget_ratio": ratio, "enabled": True})
    _cfg["coins"] = coins
    _save_cfg()

    # 새 워커 시작 (lock 보호)
    new_w = CoinWorker(market=market, budget_ratio=ratio)
    new_w.start()
    with _workers_lock:
        _workers.append(new_w)

    send_msg(
        f"✅ {market} 추가 완료!\n"
        f"비율: {ratio*100:.0f}% = {calc_budget(ratio):,}원\n"
        f"→ 봇 시작됨",
        level="critical", source="매니저", force=True
    )


def _coin_remove(market):
    """종목 제거 및 워커 정지."""
    global _workers
    coins  = _cfg.get("coins", [])
    before = len(coins)
    _cfg["coins"] = [c for c in coins if c["market"] != market]

    if len(_cfg["coins"]) == before:
        send_msg(f"❌ {market} 없음", level="normal", source="매니저", force=True)
        return

    _save_cfg()

    with _workers_lock:
        to_stop = [w for w in _workers if isinstance(w, CoinWorker) and w.market == market]
        for w in to_stop:
            w.stop()
        _workers = [w for w in _workers if w not in to_stop]

    send_msg(f"✅ {market} 제거 완료", level="critical", source="매니저", force=True)


def _coin_toggle(market, enabled):
    """종목 켜기/끄기."""
    coins = _cfg.get("coins", [])
    found = False
    for c in coins:
        if c["market"] == market:
            c["enabled"] = enabled
            found = True
            break

    if not found:
        send_msg(f"❌ {market} 없음. 먼저 /coin add {market} 0.5 로 추가하세요.", level="normal", source="매니저", force=True)
        return

    _save_cfg()
    action = "켜기" if enabled else "끄기"
    send_msg(
        f"✅ {market} {action} 완료\n→ /restart coin 으로 적용하세요.",
        level="critical", source="매니저", force=True
    )


def _update_total_budget(val):
    """전체 예산 변경 및 yaml 저장."""
    global TOTAL_BUDGET
    old = TOTAL_BUDGET
    TOTAL_BUDGET = val
    _cfg["total_budget"] = val
    _save_cfg()
    send_msg(
        f"✅ 전체 예산 변경\n"
        f"{old:,}원 → {val:,}원\n"
        f"→ /restart coin 으로 적용하세요.",
        level="critical", source="매니저", force=True
    )


def _save_cfg():
    """manager_cfg.yaml 저장 — 토큰/키 값 손상 방지."""
    try:
        # Dumper에서 문자열을 항상 쌍따옴표로 감싸도록 커스터마이즈
        class _QuotedDumper(yaml.Dumper):
            pass
        def _str_representer(dumper, data):
            # 특수문자 포함 가능한 문자열은 쌍따옴표 스타일 강제
            if any(c in data for c in (':', '#', '@', '!', '*', '&', '{', '}')):
                return dumper.represent_scalar('tag:yaml.org,2002:str', data, style='"')
            return dumper.represent_scalar('tag:yaml.org,2002:str', data)
        _QuotedDumper.add_representer(str, _str_representer)

        with open(CFG_FILE, "w", encoding="utf-8") as f:
            yaml.dump(_cfg, f, Dumper=_QuotedDumper,
                      allow_unicode=True, default_flow_style=False)
        cprint("✅ manager_cfg.yaml 저장 완료", Fore.GREEN)
    except Exception as e:
        cprint(f"[설정 저장 오류] {e}", Fore.YELLOW)


def _send_status():
    lines = [
        f"📊 통합 현황 [{datetime.now().strftime('%H:%M:%S')}]",
        "━━━━━━━━━━━━━━━━━━━━",
        f"💰 일간 손익: {daily_total_pnl:+,}원 (한도:{TOTAL_DAILY_LOSS:,}원)",
        f"📆 주간 손익: {weekly_total_pnl:+,}원 (한도:{TOTAL_WEEKLY_LOSS:,}원)",
        f"🚦 상태: {'🔴 전체 정지' if _global_stop else '🟢 정상'}",
        "─────────────────",
    ]
    with _state_lock:
        states_snapshot = dict(_worker_states)
    for wid, st in states_snapshot.items():
        holding = "📦보유중" if st.get("holding") else "⏳대기중"
        lines.append(
            f"{wid}: {st.get('pnl_today',0):+,}원 "
            f"({st.get('trades',0)}회 승{st.get('wins',0)}/패{st.get('losses',0)}) {holding}"
        )
    send_msg("\n".join(lines), level="normal", source="매니저", force=True, keyboard=KB_MAIN)


def _send_summary():
    lines = [
        f"📋 오늘 요약 [{date.today()}]",
        "━━━━━━━━━━━━━━━━━━━━",
        f"전체 손익: {daily_total_pnl:+,}원",
    ]
    with _state_lock:
        states_snapshot = dict(_worker_states)
    total_trades = sum(st.get("trades", 0) for st in states_snapshot.values())
    total_wins   = sum(st.get("wins",   0) for st in states_snapshot.values())
    rate = f"{total_wins/total_trades*100:.0f}%" if total_trades > 0 else "N/A"
    lines.append(f"총 거래: {total_trades}회  승률: {rate}")
    lines.append("─────────────────")
    for wid, st in states_snapshot.items():
        lines.append(f"{wid}: {st.get('pnl_today',0):+,}원 ({st.get('trades',0)}회)")
    send_msg("\n".join(lines), level="normal", source="매니저", force=True, keyboard=KB_MAIN)


def _send_alloc():
    lines = [
        f"💼 예산 배분 현황",
        "━━━━━━━━━━━━━━━━━━━━",
        f"전체 예산: {TOTAL_BUDGET:,}원",
        "─────────────────",
    ]
    for cfg in _cfg.get("coins", []):
        if cfg.get("enabled"):
            budget = calc_budget(cfg.get("budget_ratio", 0))
            lines.append(f"{cfg['market']}: {budget:,}원 ({cfg.get('budget_ratio',0)*100:.0f}%)")
    stock_cfg = _cfg.get("stock", {})
    if stock_cfg.get("enabled"):
        lines.append(f"주식봇(KIS): 자체 설정 사용")
    send_msg("\n".join(lines), level="normal", source="매니저", force=True)


# ============================================================
# [IPC] 봇에 명령 전달 / 결과 수신
#   - 원자적 쓰기 (tmp → rename) 로 race condition 방지
#   - 파일당 1개 lock으로 동시 읽기/쓰기 충돌 방지
# ============================================================
_ipc_file_locks: dict = defaultdict(threading.Lock)
_ipc_locks_meta  = threading.Lock()   # defaultdict 동시 접근 보호

# 명령 직렬화 세마포어
# 버튼 빠른 연속 클릭 시 handle_command 스레드가 동시 실행되면
# 같은 IPC 파일을 덮어써서 앞 명령이 유실되는 문제 방지
_cmd_semaphore = threading.Semaphore(1)


def _ipc_lock(filepath: str) -> threading.Lock:
    """파일별 Lock 반환 — thread-safe (defaultdict 동시 접근 보호)."""
    with _ipc_locks_meta:
        return _ipc_file_locks[filepath]


def _atomic_json_write(filepath: str, data: dict):
    """tmp 파일에 쓴 뒤 rename — 원자적 쓰기."""
    tmp = filepath + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, filepath)   # POSIX에서 원자적



def _forward_to_bot(target, sub_cmd_str):
    """코인봇(/c) 또는 주식봇(/s) 으로 명령 전달."""
    import uuid as _uuid
    sub_cmd = "/" + sub_cmd_str
    slow_cmds = ("/analyze", "/why", "/s status", "/balance", "/report", "/weekly")
    no_kb_cmds = ("/aggressive", "/normal", "/test", "/reload", "/s start", "/stop", "/pause")
    timeout = 20.0 if sub_cmd.startswith("/balance") else 12.0 if any(sub_cmd.startswith(c) for c in slow_cmds) else 5.0
    use_kb = not any(sub_cmd.startswith(c) for c in no_kb_cmds)
    req_id = _uuid.uuid4().hex[:8]
    workers_snap = list(_workers)
    if target == "coin":
        coin_workers = [w for w in workers_snap if isinstance(w, CoinWorker)]
        if not coin_workers:
            send_msg("🪙 실행 중인 코인봇 없음", level="normal", source="매니저", force=True)
            return
        for w in coin_workers:
            _send_ipc_cmd(w.market, sub_cmd, req_id=req_id)
        for w in coin_workers:
            result = _read_ipc_result(w.market, timeout=timeout, req_id=req_id)
            if result:
                clean = result.replace("[critical] ","").replace("[normal] ","").replace("[silent] ","")
                if clean.strip():
                    send_msg(clean, level="normal",
                             source=f"🪙{w.market.replace('KRW-','')}",
                             force=True, keyboard=KB_COIN_BOT if use_kb else None)
    elif target == "stock":
        stock_workers = [w for w in workers_snap if isinstance(w, StockWorker)]
        if not stock_workers:
            send_msg("📈 주식봇 실행 안 됨", level="normal", source="매니저", force=True)
            return
        _send_ipc_cmd("stock", sub_cmd, req_id=req_id)
        result = _read_ipc_result("stock", timeout=timeout, req_id=req_id)
        if result:
            clean = result.replace("[critical] ","").replace("[normal] ","").replace("[silent] ","")
            if clean.strip():
                send_msg(clean, level="normal", source="📈주식",
                         force=True, keyboard=KB_STOCK_BOT if use_kb else None)

def _send_ipc_cmd(target, cmd_text, req_id=None):
    """봇에 IPC 명령 전달 — 큐 방식으로 명령 유실 방지.
    기존 파일이 있으면 덮어쓰지 않고 봇이 처리할 때까지 대기 후 전송."""
    if target == "stock":
        cmd_file = os.path.join(SHARED_DIR, "cmd_stock.json")
    else:
        mkt = target.replace("KRW-", "").lower()
        cmd_file = os.path.join(SHARED_DIR, f"cmd_{mkt}.json")
    try:
        # 이전 명령이 아직 처리 안 됐으면 최대 2초 대기
        deadline = time.time() + 2.0
        while os.path.exists(cmd_file) and time.time() < deadline:
            time.sleep(0.1)

        with _ipc_lock(cmd_file):
            _atomic_json_write(cmd_file, {
                "cmd":    cmd_text,
                "req_id": req_id or "",
                "ts":     time.time()
            })
        cprint(f"[IPC→{target}] {cmd_text}", Fore.CYAN)
        return True
    except Exception as e:
        cprint(f"[IPC 오류] {e}", Fore.YELLOW)
        return False


def _read_ipc_result(target, timeout=5.0, req_id=None):
    """봇 결과 파일 대기 후 읽기.
    req_id가 있으면 해당 req_id의 결과 파일만 읽음."""
    if target == "stock":
        base = "result_stock"
    else:
        mkt = target.replace("KRW-", "").lower()
        base = f"result_{mkt}"

    # req_id 있으면 전용 파일, 없으면 기본 파일
    filename     = f"{base}_{req_id}.json" if req_id else f"{base}.json"
    result_file  = os.path.join(SHARED_DIR, filename)

    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(result_file):
            try:
                with _ipc_lock(result_file):
                    with open(result_file, encoding="utf-8") as f:
                        data = json.load(f)
                    os.remove(result_file)
                return data.get("result", "")
            except Exception:
                pass
        time.sleep(0.2)
    return None


def _poll_ipc_results():
    """봇이 자발적으로 보낸 메시지(매수/매도/하트비트 등)를 수집해서 즉시 전달.
    IPC 명령 응답(_read_ipc_result)과는 별개 — 봇이 스스로 쓴 result 파일."""
    targets = []
    for w in list(_workers):
        if isinstance(w, CoinWorker):
            mkt = w.market.replace("KRW-", "").lower()
            targets.append((f"result_{mkt}.json", f"🪙{w.market}", KB_COIN_BOT))
        elif isinstance(w, StockWorker):
            targets.append(("result_stock.json", "📈주식봇", KB_STOCK_BOT))

    for filename, source, kb in targets:
        result_file = os.path.join(SHARED_DIR, filename)
        if not os.path.exists(result_file):
            continue
        try:
            with _ipc_lock(result_file):
                with open(result_file, encoding="utf-8") as f:
                    data = json.load(f)
                os.remove(result_file)
            result_text = data.get("result", "")
            kb_override = data.get("keyboard")  # 봇이 지정한 keyboard 우선
            if result_text:
                level_tag = "silent"
                if "[critical]" in result_text.lower():
                    level_tag = "critical"
                elif "[normal]" in result_text.lower():
                    level_tag = "normal"
                clean = (result_text
                         .replace("[critical] ", "")
                         .replace("[normal] ", "")
                         .replace("[silent] ", ""))
                # silent는 전송 안 함 (하트비트, 쿨다운 안내 등)
                if level_tag != "silent":
                    send_msg(clean, level=level_tag, source=source, force=True,
                             keyboard=kb_override or kb)
        except Exception:
            pass


# ============================================================
# [9] 하트비트
# ============================================================
_last_heartbeat_hour = -1


_bot_crash_alerted: dict = {}   # {worker_id: last_alert_ts}

def _check_bot_health():
    """봇 프로세스 크래시 감지 — 3분마다 체크, 죽어있으면 텔레그램 알림."""
    now = time.time()
    for w in list(_workers):
        wid = w.worker_id
        proc_alive = w.process and w.process.poll() is None
        thread_alive = w.thread and w.thread.is_alive()

        if not proc_alive and thread_alive:
            # 프로세스는 죽었지만 감시 스레드는 살아있음 (재시작 중일 수 있음)
            last = _bot_crash_alerted.get(wid, 0)
            if now - last > 180:   # 3분에 한 번만 알림
                _bot_crash_alerted[wid] = now
                label = f"🪙{w.market}" if isinstance(w, CoinWorker) else "📈주식봇"
                send_msg(
                    f"⚠️ {label} 프로세스 크래시 감지\n"
                    f"감시 스레드가 자동 재시작 시도 중...",
                    level="critical", source="매니저", force=True
                )
        elif proc_alive:
            # 정상 → 알림 초기화
            _bot_crash_alerted.pop(wid, None)


def check_heartbeat():
    global _last_heartbeat_hour
    now = datetime.now()
    if now.hour != _last_heartbeat_hour and now.hour in [9, 12, 18, 21]:
        _last_heartbeat_hour = now.hour
        if HEARTBEAT_ALERT:
            with _state_lock:
                active = len(_worker_states)
            send_msg(
                f"💓 하트비트 [{now.strftime('%H:%M')}]\n"
                f"실행 중인 워커: {active}개\n"
                f"일간 손익: {daily_total_pnl:+,}원",
                level="silent", source="매니저"
            )


# ============================================================
# [10] GitHub 업데이트 / 재시작
# ============================================================
VERSION_FILE = os.path.join(BASE_DIR, ".manager_version.json")

# 관리 대상 파일 목록 (레포에서 다운로드할 파일들)
MANAGED_FILES = ["upbit_bot.py", "sector_bot.py", "manager.py"]


def _gh_headers():
    token = _cfg.get("github_token", "")
    h = {"Accept": "application/vnd.github.v3+json"}
    if token:
        h["Authorization"] = f"token {token}"
    return h


def _gh_latest_commit():
    """GitHub 최신 커밋 정보 조회."""
    repo   = _cfg.get("github_repo", "")
    branch = _cfg.get("github_branch", "main")
    if not repo:
        return None
    try:
        for br in ([branch] if branch != "main" else ["main", "master"]):
            res = requests.get(
                f"https://api.github.com/repos/{repo}/commits/{br}",
                headers=_gh_headers(), timeout=10
            )
            if res.status_code == 200:
                d = res.json()
                return {
                    "hash":    d["sha"][:7],
                    "full":    d["sha"],
                    "message": d["commit"]["message"].split("\n")[0],
                    "time":    d["commit"]["author"]["date"][:16].replace("T", " "),
                    "branch":  br,
                }
            if res.status_code == 404:
                continue
        cprint(f"[GitHub 조회 실패] 브랜치 없음", Fore.YELLOW)
    except Exception as e:
        cprint(f"[GitHub 조회 오류] {e}", Fore.YELLOW)
    return None


def _gh_download_file(filename, ref=None):
    """GitHub에서 파일 1개 다운로드. 성공 시 내용(str) 반환."""
    repo   = _cfg.get("github_repo", "")
    branch = ref or _cfg.get("github_branch", "main")
    if not repo:
        return None
    try:
        res = requests.get(
            f"https://api.github.com/repos/{repo}/contents/{filename}",
            headers=_gh_headers(),
            params={"ref": branch},
            timeout=15
        )
        if res.status_code == 200:
            import base64
            return base64.b64decode(res.json()["content"]).decode("utf-8")
        else:
            cprint(f"[GitHub 다운로드 실패] {filename}: {res.status_code}", Fore.YELLOW)
    except Exception as e:
        cprint(f"[GitHub 다운로드 오류] {filename}: {e}", Fore.YELLOW)
    return None


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


def do_update(force=False):
    """
    GitHub에서 최신 파일 다운로드 → 변경된 파일만 교체 → 봇 재시작.
    force=True 면 버전 체크 없이 강제 업데이트.
    """
    # 설정 확인
    if not _cfg.get("github_token") or not _cfg.get("github_repo"):
        send_msg(
            "❌ GitHub 설정 없음\n"
            "manager_cfg.yaml 에 아래 항목을 추가하세요:\n"
            "  github_token: \"ghp_...\"\n"
            "  github_repo:  \"아이디/레포명\"",
            level="critical", source="매니저", force=True
        )
        return

    send_msg("🔍 업데이트 확인 중...", level="normal", source="매니저", force=True)

    latest = _gh_latest_commit()
    if not latest:
        send_msg(
            f"❌ GitHub 연결 실패\n"
            f"레포: {_cfg.get('github_repo', '없음')}\n\n"
            f"확인 사항:\n"
            f"• 토큰 만료 또는 repo 권한 없는지\n"
            f"• 레포 이름 대소문자 정확한지\n"
            f"• 레포가 실제로 존재하는지",
            level="critical", source="매니저", force=True
        )
        return

    local = _load_local_version()
    if not force and local and local.get("full") == latest["full"]:
        send_msg(
            f"✅ 이미 최신 버전이에요.\n커밋: {latest['hash']} ({latest['time']})\n{latest['message']}",
            level="normal", source="매니저", force=True
        )
        return

    send_msg(
        f"⬇️ 업데이트 시작\n"
        f"커밋: {latest['hash']}\n"
        f"{latest['message']} ({latest['time']})",
        level="critical", source="매니저", force=True
    )

    updated = []
    failed  = []

    for filename in MANAGED_FILES:
        filepath = os.path.join(BASE_DIR, filename)
        content  = _gh_download_file(filename)

        if content is None:
            failed.append(filename)
            continue

        # 변경 여부 확인 (해시 비교)
        new_hash = hashlib.md5(content.encode()).hexdigest()
        old_hash = ""
        if os.path.exists(filepath):
            with open(filepath, encoding="utf-8") as f:
                old_hash = hashlib.md5(f.read().encode()).hexdigest()

        if new_hash == old_hash and not force:
            cprint(f"  [{filename}] 변경 없음 — 건너뜀", Fore.CYAN)
            continue

        # 백업
        backup_dir = os.path.join(BASE_DIR, "backups")
        os.makedirs(backup_dir, exist_ok=True)
        if os.path.exists(filepath):
            bak = os.path.join(backup_dir, f"{filename}.bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
            shutil.copy2(filepath, bak)

        # 교체
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        updated.append(filename)
        cprint(f"  [{filename}] ✅ 업데이트 완료", Fore.GREEN)

    if failed:
        send_msg(f"⚠️ 다운로드 실패: {', '.join(failed)}", level="critical", source="매니저", force=True)

    if not updated and not failed:
        send_msg("✅ 변경된 파일 없음. 최신 상태예요.", level="normal", source="매니저", force=True)
        _save_local_version(latest)
        return

    _save_local_version(latest)

    if updated:
        send_msg(
            f"✅ 업데이트 완료!\n"
            f"변경 파일: {', '.join(updated)}\n"
            f"→ 3초 후 재시작...",
            level="critical", source="매니저", force=True
        )
        time.sleep(3)
        do_restart()


def do_restart(target="all"):
    """
    target: "all" | "coin" | "stock" | "manager"
    """
    global _workers

    if target == "manager":
        send_msg("🔄 매니저 재시작 중...", level="critical", source="매니저", force=True)
        time.sleep(1)
        os.execv(sys.executable, [sys.executable] + sys.argv)

    with _workers_lock:
        workers_snap = list(_workers)

    targets = []
    if target in ("all", "coin"):
        targets += [w for w in workers_snap if isinstance(w, CoinWorker)]
    if target in ("all", "stock"):
        targets += [w for w in workers_snap if isinstance(w, StockWorker)]

    if not targets:
        send_msg(f"⚠️ 재시작 대상 없음: {target}", level="normal", source="매니저", force=True)
        return

    names = []
    for w in targets:
        cprint(f"  [{w.worker_id}] 재시작 중...", Fore.YELLOW)
        w.stop()
        names.append(w.worker_id)

    time.sleep(2)

    with _workers_lock:
        # 기존 워커 제거
        _workers = [w for w in _workers if w not in targets]
        # 새 워커 생성 후 추가
        for w in targets:
            if isinstance(w, CoinWorker):
                for coin_cfg in _cfg.get("coins", []):
                    if coin_cfg.get("market") == w.market and coin_cfg.get("enabled"):
                        new_w = CoinWorker(
                            market       = coin_cfg["market"],
                            budget_ratio = coin_cfg.get("budget_ratio", 0.5),
                        )
                        new_w.start()
                        _workers.append(new_w)
            elif isinstance(w, StockWorker):
                stock_cfg = _cfg.get("stock", {})
                new_w = StockWorker(script=stock_cfg.get("script", "sector_bot.py"))
                new_w.start()
                _workers.append(new_w)

    send_msg(
        f"✅ 재시작 완료: {', '.join(names)}",
        level="critical", source="매니저", force=True
    )


# ============================================================
# [11] 메인 실행
# ============================================================
_workers = []


def run_manager():
    global _workers

    cprint(f"\n{'='*50}", Fore.CYAN, bright=True)
    cprint(f"  통합 매니저 v{MANAGER_VERSION} 시작", Fore.CYAN, bright=True)
    cprint(f"{'='*50}\n", Fore.CYAN, bright=True)

    load_config()
    _load_state()

    # 매니저 PID 파일 기록 → 봇들이 매니저 실행 여부 감지에 사용
    try:
        with open(MANAGER_PID_FILE, "w") as f:
            f.write(str(os.getpid()))
        cprint(f"✅ PID 파일 기록: {MANAGER_PID_FILE} (PID={os.getpid()})", Fore.CYAN)
    except Exception as e:
        cprint(f"[경고] PID 파일 기록 실패: {e}", Fore.YELLOW)

    # 코인 워커 생성 (시작 전이라 lock 불필요하지만 일관성을 위해 적용)
    for coin_cfg in _cfg.get("coins", []):
        if not coin_cfg.get("enabled", False):
            cprint(f"  [{coin_cfg['market']}] disabled — 건너뜀", Fore.YELLOW)
            continue
        w = CoinWorker(
            market       = coin_cfg["market"],
            budget_ratio = coin_cfg.get("budget_ratio", 0.5),
        )
        _workers.append(w)

    # 주식 워커 생성
    stock_cfg = _cfg.get("stock", {})
    if stock_cfg.get("enabled", False):
        script = stock_cfg.get("script", "sector_bot.py")
        if os.path.exists(os.path.join(KIS_BOT_DIR, script)):
            _workers.append(StockWorker(script=script))
        else:
            cprint(f"  [주식봇] {KIS_BOT_DIR}/{script} 파일 없음 — 건너뜀", Fore.YELLOW)

    if not _workers:
        cprint("❌ 실행할 봇이 없어요. manager_cfg.yaml에서 enabled: true 확인하세요.", Fore.RED)
        sys.exit(1)

    # 워커 순차 시작 (API 동시 호출 방지)
    for w in list(_workers):
        w.start()
        time.sleep(1)

    init_mgr_pinned_message()
    setup_manager_reply_keyboard()   # 키보드는 마지막에

    send_msg(
        f"🚀 통합 매니저 v{MANAGER_VERSION} 시작!\n"
        f"실행 봇: {len(_workers)}개\n"
        f"전체 예산: {TOTAL_BUDGET:,}원\n"
        f"일간 손실 한도: {TOTAL_DAILY_LOSS:,}원\n"
        f"버튼 메뉴가 하단에 고정됐습니다 👇",
        level="critical", source="매니저", force=True
    )

    cprint("\n매니저 실행 중... (Ctrl+C로 종료)\n", Fore.GREEN)
    try:
        while True:
            check_daily_reset()
            check_weekly_reset()
            check_heartbeat()
            poll_telegram()
            _poll_ipc_results()
            _check_bot_health()
            update_mgr_pinned_message()
            time.sleep(3)

    except KeyboardInterrupt:
        cprint("\n\n⏹ 종료 신호 수신. 모든 봇 정지 중...", Fore.YELLOW, bright=True)
        for w in list(_workers):
            w.stop()
        # PID 파일 삭제 → 봇들이 단독 폴링 모드로 전환
        try:
            if os.path.exists(MANAGER_PID_FILE):
                os.remove(MANAGER_PID_FILE)
        except Exception:
            pass
        send_msg("⏹ 통합 매니저 종료", level="critical", source="매니저", force=True)
        time.sleep(2)
        cprint("✅ 종료 완료", Fore.GREEN)


if __name__ == "__main__":
    run_manager()

def do_rollback():
    """가장 최근 백업으로 롤백."""
    backup_dir = os.path.join(BASE_DIR, "backups")
    if not os.path.exists(backup_dir):
        send_msg("❌ 백업 폴더 없음. 롤백할 버전이 없어요.",
                 level="critical", source="매니저", force=True)
        return
    send_msg("🔄 롤백 시작...", level="critical", source="매니저", force=True)
    restored = []
    failed   = []
    for filename in MANAGED_FILES:
        pattern  = f"{filename}.bak_"
        bak_list = sorted([
            f for f in os.listdir(backup_dir)
            if f.startswith(pattern)
        ], reverse=True)
        if not bak_list:
            continue
        latest_bak = os.path.join(backup_dir, bak_list[0])
        dest       = os.path.join(BASE_DIR, filename)
        try:
            shutil.copy2(latest_bak, dest)
            restored.append(f"{filename} ← {bak_list[0]}")
        except Exception as e:
            failed.append(filename)
    if not restored:
        send_msg("❌ 복원된 파일 없음.", level="critical", source="매니저", force=True)
        return
    send_msg(
        f"✅ 롤백 완료!\n복원:\n" + "\n".join(f"  • {r}" for r in restored) +
        f"\n→ 3초 후 재시작...",
        level="critical", source="매니저", force=True
    )
    time.sleep(3)
    do_restart()
