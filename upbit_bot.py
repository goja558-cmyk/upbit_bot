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

BOT_VERSION  = "2.10"
BOT_NAME     = "트레이딩봇"
BOT_TAG      = "🪙 코인"   # 텔레그램 메시지 앞에 붙는 구분 태그
def _bot_tag():
    coin = MARKET_CODE.replace("KRW-", "")
    return f"🪙 {coin}"

def _coin_name():
    """현재 종목 코드에서 이름만 추출. KRW-XRP → XRP"""
    return MARKET_CODE.replace("KRW-", "") if MARKET_CODE else "코인"

def _coin_name():
    """현재 종목 코드에서 이름만 추출. KRW-XRP → XRP"""
    return MARKET_CODE.replace("KRW-", "") if MARKET_CODE else "코인"

def _coin_name():
    """현재 종목 코드에서 이름만 추출. KRW-XRP → XRP"""
    return MARKET_CODE.replace("KRW-", "") if MARKET_CODE else "코인"

# ============================================================
# [0] 종목별 파라미터 프로파일
#   - MARKET_CODE 는 upbit_cfg.yaml 의 market 키로 지정 (하드코딩 금지)
#   - /switch 명령으로 런타임 중 전환 가능
#   - 각 수치는 해당 코인의 역사적 ATR·변동성 통계 기반 적정값
# ============================================================
COIN_PROFILES = {
    # ── 비트코인 ─────────────────────────────────────────────
    # 변동성 낮음 / 틱 사이즈 큼 / 유동성 최고
    "KRW-BTC": dict(
        target       =  0.8,   # 익절: 낮은 변동성, 수수료 감안 최소 0.8%
        max_loss     = -0.6,   # 손절: 타이트
        drop         =  0.4,   # 눌림: MA20 대비
        trail_start  =  0.5,
        trail_gap    =  0.25,
        be_trigger   =  0.3,
        rsi_buy      =  36,    # BTC RSI는 40 이하가 드물어서 약간 낮게
        vol_min      =  0.2,   # 변동성 낮아서 기준도 낮게
        vol_max      =  4.0,
        cooldown     =  90,
        timeout_min  =  60,
    ),
    # ── 리플(XRP) ─────────────────────────────────────────────
    # 변동성 높음 / 뉴스 민감 / 단타에 유리
    "KRW-XRP": dict(
        target       =  1.2,   # 익절: 변동성 크므로 목표 높게
        max_loss     = -0.9,   # 손절: 조금 더 여유 (노이즈 많음)
        drop         =  0.6,   # 눌림: XRP는 MA 이탈이 잦음
        trail_start  =  0.6,
        trail_gap    =  0.35,
        be_trigger   =  0.4,
        rsi_buy      =  38,    # XRP RSI 반응 빠름
        vol_min      =  0.4,
        vol_max      =  6.0,
        cooldown     =  90,
        timeout_min  =  45,    # XRP는 빠르게 끝나는 편
    ),
    # ── 이더리움 ─────────────────────────────────────────────
    "KRW-ETH": dict(
        target       =  1.0,
        max_loss     = -0.7,
        drop         =  0.5,
        trail_start  =  0.55,
        trail_gap    =  0.3,
        be_trigger   =  0.35,
        rsi_buy      =  37,
        vol_min      =  0.25,
        vol_max      =  5.0,
        cooldown     =  100,
        timeout_min  =  55,
    ),
    # ── 도지코인 ─────────────────────────────────────────────
    # 밈코인 / 변동성 매우 높음
    "KRW-DOGE": dict(
        target       =  1.5,
        max_loss     = -1.0,
        drop         =  0.7,
        trail_start  =  0.7,
        trail_gap    =  0.4,
        be_trigger   =  0.5,
        rsi_buy      =  40,
        vol_min      =  0.5,
        vol_max      =  8.0,
        cooldown     =  80,
        timeout_min  =  40,
    ),
    # ── 트럼프 ────────────────────────────────────────────────
    # 밈코인 계열 / 변동성 높음 / 충분한 보유시간 필요
    "KRW-TRUMP": dict(
        target       =  1.5,   # 익절: 변동성 크므로 목표 높게
        max_loss     = -1.2,   # 손절: 노이즈 많아 여유있게
        drop         =  0.7,   # 눌림: 크게 빠질 때만 진입
        trail_start  =  1.0,   # 트레일: 1% 이상 올라야 추적 시작
        trail_gap    =  0.5,   # 트레일 간격: 0.5% 여유
        be_trigger   =  0.6,   # 본절: 0.6% 이상 올라야 본절 보호
        rsi_buy      =  35,    # RSI: 충분히 과매도 확인
        vol_min      =  0.5,
        vol_max      =  8.0,
        cooldown     =  120,
        timeout_min  =  60,    # 최소 60분 보유
    ),
    # ── 솔라나 ────────────────────────────────────────────────
    "KRW-SOL": dict(
        target       =  1.1,
        max_loss     = -0.8,
        drop         =  0.55,
        trail_start  =  0.6,
        trail_gap    =  0.3,
        be_trigger   =  0.4,
        rsi_buy      =  38,
        vol_min      =  0.35,
        vol_max      =  6.0,
        cooldown     =  90,
        timeout_min  =  50,
    ),
}
# 프로파일에 없는 종목은 이 기본값 사용
COIN_PROFILE_DEFAULT = dict(
    target       =  1.0,
    max_loss     = -0.8,
    drop         =  0.5,
    trail_start  =  0.5,
    trail_gap    =  0.3,
    be_trigger   =  0.35,
    rsi_buy      =  38,
    vol_min      =  0.3,
    vol_max      =  6.0,
    cooldown     =  120,
    timeout_min  =  60,
)

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
    from datetime import datetime as _dt
    ts     = _dt.now().strftime("%H:%M:%S")
    prefix = (Style.BRIGHT if bright else "") + color if COLOR_OK else ""
    print(f"{prefix}[{ts}] {text}{Style.RESET_ALL if COLOR_OK else ''}")

# ============================================================
# [1] 사용자 설정
# ============================================================

# 텔레그램 (upbit_cfg.yaml 에서 자동 설정됨)
TELEGRAM_TOKEN = ""
CHAT_ID        = ""

# ── 거래 종목 ─────────────────────────────────────────────────
# ⚠️ 여기서 직접 수정하지 마세요!
# upbit_cfg.yaml 의 market 키로 지정하세요.
# 예) market: "KRW-XRP"
# 런타임 중 변경: 텔레그램 /switch KRW-XRP
MARKET_CODE    = "KRW-BTC"   # load_config() 에서 yaml값으로 덮어씀
FEE_RATE       = 0.0005      # 업비트 수수료 0.05%

# ── 매매 예산 ────────────────────────────────────────────────
ORDER_BUDGET_KRW   = 10_000   # 1회 주문에 쓸 최대 금액 (원) — yaml: budget
ORDER_BUDGET_RATIO = 0.98     # 예산의 98%까지 사용 (업비트 최소주문 5,000원)

# ── 손실 한도 ────────────────────────────────────────────────
MAX_DAILY_LOSS_KRW  = -5_000    # 하루 최대 손실 (원)
DAILY_LOSS_BASE_KRW = 50_000    # 손실 한도 계산 기준금액
MAX_DAILY_LOSS_PCT  = -2.0      # 기준 대비 최대 손실 %
WEEKLY_LOSS_LIMIT_KRW = -30_000 # 주간 최대 손실 (원)

# ── 매매 횟수 제한 ───────────────────────────────────────────
MAX_TRADE_COUNT = 99999    # 하루 최대 매매 횟수

# ── 전략 수치 ── 아래값은 load_config()에서 COIN_PROFILES로 덮어씀 ──
BOT_TARGET      =  1.0
BOT_MAX_LOSS    = -0.8
BOT_DROP        =  0.5
BOT_TRAIL_START =  0.5
BOT_TRAIL_GAP   =  0.3
BOT_BE_TRIGGER  =  0.3
BOT_RSI_BUY     =  38
BOT_RSI_PERIOD  =  14

# ── VWAP / 거래량 필터 ───────────────────────────────────────
VWAP_FILTER   = True
VOL_RATIO_MIN = 1.0

# ── 변동성 필터 ──────────────────────────────────────────────
VOL_WINDOW_SEC = 300
VOL_MIN_PCT    = 0.3
VOL_MAX_PCT    = 5.0

# ── 슬리피지 / 쿨다운 ────────────────────────────────────────
MAX_SLIPPAGE_PCT = 0.5
COOLDOWN_SEC     = 120

# ── 포지션 타임아웃 ──────────────────────────────────────────
POS_TIMEOUT_MIN      = 60
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
NIGHT_SILENCE_START   = (2,  0)
NIGHT_SILENCE_END     = (7,  0)
NONMARKET_ALERT_HOURS = [9, 21]

# ── 기타 ─────────────────────────────────────────────────────
LOOP_INTERVAL    = 5
HISTORY_PREFILL  = 70
MAX_API_FAIL     = 5
WARMUP_MINUTES   = 0
BACKUP_KEEP_DAYS = 7
CONSEC_LOSS_ALERT = 3

SET_ALLOWED_KEYS = {
    "target", "max_loss", "drop",
    "trail_start", "trail_gap", "be_trigger", "rsi_buy",
    "trade_count", "cooldown", "vol_min", "vol_max",
    "slippage", "timeout_min", "vwap_filter",
    "grid_step", "grid_levels", "grid_budget",
    "train_rsi_min", "train_rsi_max", "train_momentum", "train_vol_ratio",
    "train_target", "train_stop", "train_trail_ratio", "train_gap_ratio",
}

# ============================================================
# [2] 경로 설정
# ============================================================
BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
# 코인별 로그 폴더 자동 생성
def _get_log_files(market=None):
    """종목별 로그 파일 경로 반환. MARKET_CODE 변경 시 자동 반영."""
    m = (market or MARKET_CODE).replace("KRW-", "")
    log_dir = os.path.join(BASE_DIR, "logs", m)
    os.makedirs(log_dir, exist_ok=True)
    return (
        os.path.join(log_dir, "trade_log.csv"),
        os.path.join(log_dir, "indicator_log.csv"),
    )

LOG_FILE           = os.path.join(BASE_DIR, "coin_trade_log.csv")   # 하위 호환
INDICATOR_LOG_FILE = os.path.join(BASE_DIR, "coin_indicator_log.csv")  # 하위 호환
STATE_FILE     = os.path.join(BASE_DIR, "coin_state_v1.json")
# ── 설정 파일 경로 ────────────────────────────────────────
# 매니저가 --config 인자로 종목별 yaml을 지정하면 그것을 사용
# 없으면 기본값 upbit_cfg.yaml 사용
import argparse as _argparse
_ap = _argparse.ArgumentParser(add_help=False)
_ap.add_argument("--config", default=None)
_ap_args, _ = _ap.parse_known_args()

CFG_FILE = (
    os.path.abspath(_ap_args.config)
    if _ap_args.config
    else os.path.join(BASE_DIR, "upbit_cfg.yaml")
)
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
    global MARKET_CODE, ORDER_BUDGET_KRW, DAILY_LOSS_BASE_KRW, WEEKLY_LOSS_LIMIT_KRW
    global BOT_TARGET, BOT_MAX_LOSS, BOT_DROP, BOT_TRAIL_START, BOT_TRAIL_GAP
    global BOT_BE_TRIGGER, BOT_RSI_BUY, RSI_BUY_DEFAULT

    if not os.path.exists(CFG_FILE):
        print(f"❌ {CFG_FILE} 파일이 없습니다.")
        print("upbit_cfg.yaml 파일을 만들고 access_key, secret_key, telegram_token, chat_id 를 입력하세요.")
        sys.exit(1)
    with open(CFG_FILE, encoding="utf-8") as f:
        _cfg = yaml.safe_load(f) or {}
    TELEGRAM_TOKEN = _cfg.get("telegram_token", "")
    CHAT_ID        = str(_cfg.get("chat_id", ""))

    # ── 종목: yaml 우선 (매니저가 종목별 yaml을 생성해서 넘겨줌) ──
    market_from_yaml = _cfg.get("market", "").strip().upper()
    if market_from_yaml:
        MARKET_CODE = market_from_yaml
    cprint(f"✅ 거래 종목: {MARKET_CODE}", Fore.CYAN)

    # ── 예산 / 손실한도 yaml 오버라이드 ──────────────────────
    if _cfg.get("budget"):
        ORDER_BUDGET_KRW = int(_cfg["budget"])
    if _cfg.get("daily_loss_base"):
        DAILY_LOSS_BASE_KRW = int(_cfg["daily_loss_base"])
    if _cfg.get("weekly_loss_limit"):
        WEEKLY_LOSS_LIMIT_KRW = int(_cfg["weekly_loss_limit"])

    # ── 종목 프로파일 자동 적용 ───────────────────────────────
    # yaml 에 profile 섹션 있으면 COIN_PROFILES 에 병합
    yaml_profile = _cfg.get("profile", {})
    if yaml_profile:
        COIN_PROFILES[MARKET_CODE] = {**COIN_PROFILE_DEFAULT, **yaml_profile}
    apply_coin_profile(MARKET_CODE, source="설정파일 로드")
    _init_ipc()   # IPC 파일 경로 초기화

    if not JWT_OK:
        print("❌ PyJWT 라이브러리가 없습니다. pip install PyJWT")
        sys.exit(1)
    cprint("✅ 설정 파일 로드 완료", Fore.GREEN)



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

def _acquire_slot():
    """매수 완료 후 슬롯 점유 신호 전송."""
    if not _is_manager_running(): return
    mkt = MARKET_CODE.replace("KRW-", "").lower()
    acq_file = os.path.join(SHARED_DIR, f"slot_acquired_{mkt}.json")
    try:
        tmp = acq_file + ".tmp"
        with open(tmp, "w") as f:
            import json as _j
            _j.dump({"market": MARKET_CODE, "ts": time.time()}, f)
        os.replace(tmp, acq_file)
        os.chmod(acq_file, 0o664)
        cprint(f"[슬롯] {MARKET_CODE} 점유 신호 전송", Fore.CYAN)
    except Exception as e:
        cprint(f"[슬롯 점유 오류] {e}", Fore.YELLOW)

def fetch_coin_stats(market):
    """실시간 데이터로 보수적 세팅 자동 계산."""
    try:
        res = requests.get(f"{UPBIT_BASE}/candles/minutes/1",
                          params={"market": market, "count": 200}, timeout=5)
        if res.status_code != 200: return None
        candles = res.json()
        if len(candles) < 30: return None
        highs  = [c["high_price"] for c in candles]
        lows   = [c["low_price"]  for c in candles]
        ranges = [(h-l)/l*100 for h,l in zip(highs,lows) if l > 0]
        avg_vol = sum(ranges)/len(ranges) if ranges else 0.5
        max_vol = sorted(ranges)[-10] if len(ranges) >= 10 else avg_vol*2
        target    = round(max(0.3, min(5.0, avg_vol*1.5)), 2)
        max_loss  = round(-target*0.8, 2)
        drop      = round(max(0.1, min(2.0, avg_vol*0.5)), 2)
        vol_min   = round(max(0.05, avg_vol*0.2), 2)
        vol_max   = round(min(20.0, max_vol*1.5), 1)
        cooldown  = max(30, min(120, int(120/max(1, avg_vol))))
        timeout   = max(15, min(60, int(60/max(1, avg_vol))))
        return dict(target=target, max_loss=max_loss, drop=drop,
                    trail_start=round(target*0.5,2), trail_gap=round(target*0.2,2),
                    be_trigger=round(target*0.3,2), rsi_buy=40,
                    vol_min=vol_min, vol_max=vol_max,
                    cooldown=cooldown, timeout_min=timeout)
    except Exception as e:
        cprint(f"[자동 프로파일 오류] {e}", Fore.YELLOW)
        return None


def _auto_profile(market_code):
    """프로파일 없는 종목 — 최근 변동성 기반 자동 수치 생성"""
    try:
        prices = get_ohlcv(market=market_code, count=100, interval=60)
        if not prices or len(prices) < 20:
            return dict(COIN_PROFILE_DEFAULT)
        import numpy as np
        p = np.array(prices)
        diffs = np.abs(np.diff(p)) / p[:-1] * 100
        atr = float(np.mean(diffs[-20:]))
        profile = dict(
            target       = round(max(0.8, min(3.0, atr * 1.5)), 2),
            max_loss     = round(-max(0.6, min(2.5, atr * 2.0)), 2),
            drop         = round(max(0.3, min(1.5, atr * 1.0)), 2),
            trail_start  = round(max(0.5, min(2.0, atr * 1.2)), 2),
            trail_gap    = round(max(0.2, min(1.0, atr * 0.6)), 2),
            be_trigger   = round(max(0.3, min(1.0, atr * 0.8)), 2),
            rsi_buy      = 37,
            vol_min      = round(max(0.2, atr * 0.5), 2),
            vol_max      = round(min(10.0, atr * 8.0), 2),
            cooldown     = 120,
            timeout_min  = 60,
        )
        cprint(f"[자동 프로파일] {market_code} ATR={atr:.3f}% → {profile}", Fore.CYAN)
        return profile
    except Exception as e:
        cprint(f"[자동 프로파일 오류] {e}", Fore.YELLOW)
        return dict(COIN_PROFILE_DEFAULT)

def apply_coin_profile(market_code, source=""):
    """종목에 맞는 파라미터 프로파일을 전역변수 및 bot dict에 적용한다."""
    global BOT_TARGET, BOT_MAX_LOSS, BOT_DROP, BOT_TRAIL_START, BOT_TRAIL_GAP
    global BOT_BE_TRIGGER, BOT_RSI_BUY, RSI_BUY_DEFAULT

    if market_code not in COIN_PROFILES:
        COIN_PROFILES[market_code] = _auto_profile(market_code)
    profile = COIN_PROFILES.get(market_code, COIN_PROFILE_DEFAULT)

    BOT_TARGET      = profile["target"]
    BOT_MAX_LOSS    = profile["max_loss"]
    BOT_DROP        = profile["drop"]
    BOT_TRAIL_START = profile["trail_start"]
    BOT_TRAIL_GAP   = profile["trail_gap"]
    BOT_BE_TRIGGER  = profile["be_trigger"]
    BOT_RSI_BUY     = profile["rsi_buy"]
    RSI_BUY_DEFAULT = profile["rsi_buy"]
    VOL_MIN_PCT     = profile["vol_min"]
    VOL_MAX_PCT     = profile["vol_max"]
    COOLDOWN_SEC    = profile["cooldown"]
    POS_TIMEOUT_MIN = profile["timeout_min"]

    # bot 딕셔너리도 동기화
    bot.update({
        "target":      BOT_TARGET,
        "max_loss":    BOT_MAX_LOSS,
        "drop":        BOT_DROP,
        "trail_start": BOT_TRAIL_START,
        "trail_gap":   BOT_TRAIL_GAP,
        "be_trigger":  BOT_BE_TRIGGER,
        "rsi_buy":     BOT_RSI_BUY,
    })

    tag = "★ 커스텀 프로파일" if market_code in COIN_PROFILES else "◎ 기본 프로파일"
    label = f" ({source})" if source else ""
    cprint(
        f"  {tag}{label}\n"
        f"  익절:{BOT_TARGET}%  손절:{BOT_MAX_LOSS}%  RSI:{BOT_RSI_BUY}\n"
        f"  눌림:{BOT_DROP}%  변동성:{VOL_MIN_PCT}~{VOL_MAX_PCT}%  쿨다운:{COOLDOWN_SEC}s",
        Fore.CYAN
    )

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
    "train_rsi_min":    50,
    "train_rsi_max":    65,
    "train_momentum":   1.5,
    "train_vol_ratio":  1.5,
    "train_target":     1.5,
    "train_stop":       0.8,
    "train_trail_ratio":0.8,
    "train_gap_ratio":  1.2,
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
_order_pending   = False
# ── 테스트 모드 ────────────────────────────────────────────
_test_mode         = False
_test_orig_params  = {}

def _enter_test_mode():
    global _test_mode, _test_orig_params
    if _test_mode: return
    _test_mode = True
    _test_orig_params = {
        "rsi_buy":     bot.get("rsi_buy", 38),
        "target":      bot.get("target",  0.8),
        "max_loss":    bot.get("max_loss",-0.8),
        "drop":        bot.get("drop",    0.4),
        "trail_start": bot.get("trail_start", 0.5),
        "cooldown":    COOLDOWN_SEC,
        "max_trade":   MAX_TRADE_COUNT,
    }
    bot["rsi_buy"]     = 70    # RSI 70 이하면 매수 (거의 항상 진입)
    bot["target"]      = 0.05  # 익절 0.05%
    bot["max_loss"]    = -0.1  # 손절 -0.1%
    bot["drop"]        = 0.01  # 눌림 0.01%
    bot["trail_start"] = 0.03

def _exit_test_mode():
    global _test_mode, COOLDOWN_SEC, MAX_TRADE_COUNT
    if not _test_mode: return
    _test_mode = False
    if _test_orig_params:
        bot["rsi_buy"]     = _test_orig_params["rsi_buy"]
        bot["target"]      = _test_orig_params["target"]
        bot["max_loss"]    = _test_orig_params["max_loss"]
        bot["drop"]        = _test_orig_params["drop"]
        bot["trail_start"] = _test_orig_params["trail_start"]
        COOLDOWN_SEC       = _test_orig_params["cooldown"]
        MAX_TRADE_COUNT    = _test_orig_params["max_trade"]
    _test_orig_params.clear()

_last_error_alert_ts = 0.0   # 오류 알림 쿨다운 (5분)   # 중복 주문 방지

# ── 그리드 트레이딩 설정 ─────────────────────────────────────
GRID_ENABLED    = False
GRID_STEP_PCT   = 0.05
GRID_MAX_LEVELS = 6
GRID_BUDGET_PER = 0
grid_levels     = []
grid_avg_price  = 0.0
grid_total_qty  = 0.0
grid_total_krw  = 0.0

_weekly_stop     = False
# ── 🚂 달리는 기차 모드 ──────────────────────────────────────────
_train_mode         = False
_verify_mode        = False
_verify_orig        = {}
_train_entry_price  = 0.0
_train_signal_count = 0
_train_alert_sent   = False

_last_price      = 0
_vwap_value      = None
_vwap_sum_pv     = 0.0
_vwap_sum_v      = 0.0
_daily_report_sent = False
_real_data_count   = 0
REAL_DATA_MIN      = 60

_api_fail_count    = 0
_api_fail_first_ts = 0.0
last_update_id     = 0
_last_tg_poll      = 0.0
_last_heartbeat_hour = -1
_last_backup_date  = ""
_pause_alert_sent  = ""
_dynamic_mode      = True
_aggressive_mode   = False
_last_status_line_ts = 0.0
_last_detail_log_ts  = 0.0
_indicator_log_counter = 0
INDICATOR_LOG_INTERVAL = 3
_tg_fail_count     = 0
TG_FAIL_ALERT_THRESHOLD = 10

# ── 상단 고정 메시지 ──────────────────────────────────────
_pinned_msg_id       = 0      # 고정된 메시지 ID
_pinned_last_update  = 0.0    # 마지막 업데이트 시각
PINNED_UPDATE_INTERVAL = 30   # 수치 업데이트 주기 (초)

# 쿨다운/최대거래 알림 플래그
_cooldown_alert_sent    = False
_max_trade_alert_sent   = False

# ── IPC: 매니저 ↔ 코인봇 통신 ──────────────────────────────
_IPC_CMD_FILE    = None
_IPC_RESULT_FILE = None
_IPC_STATUS_FILE = None
_IPC_REQ_ID      = ""     # 현재 처리 중인 요청 ID
_is_ipc_context  = False

def _init_ipc():
    global _IPC_CMD_FILE, _IPC_RESULT_FILE, _IPC_STATUS_FILE
    mkt = MARKET_CODE.replace("KRW-", "").lower()
    _IPC_CMD_FILE    = os.path.join(SHARED_DIR, f"cmd_{mkt}.json")
    _IPC_RESULT_FILE = os.path.join(SHARED_DIR, f"result_{mkt}.json")
    _IPC_STATUS_FILE = os.path.join(SHARED_DIR, f"status_{mkt}.json")

def _get_result_file():
    """현재 요청의 결과 파일 경로 반환 (req_id 있으면 전용 파일)."""
    if not _IPC_RESULT_FILE:
        return None
    if _IPC_REQ_ID:
        base = _IPC_RESULT_FILE.replace(".json", "")
        return f"{base}_{_IPC_REQ_ID}.json"
    return _IPC_RESULT_FILE

def _atomic_write_result(data: dict):
    """원자적 결과 파일 쓰기."""
    rf = _get_result_file()
    if not rf:
        return
    tmp = rf + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.chmod(tmp, 0o664)
        os.replace(tmp, rf)
    except Exception as e:
        cprint(f"[IPC 결과 쓰기 오류] {e}", Fore.YELLOW)
        try:
            os.remove(tmp)
        except Exception:
            pass

def _write_result(result_text, keyboard=None):
    """명령 실행 결과를 매니저에게 전달 (IPC 중에만)."""
    if not _is_ipc_context:
        return
    _atomic_write_result({"result": result_text, "keyboard": keyboard, "ts": time.time()})

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

    _write_result(f"[{level}] {text}", keyboard=keyboard)

    if _is_ipc_context:
        return

    if level == "critical" or force:
        tagged_text = f"[{_bot_tag()}]\n{text}"
        # 4096자 초과 시 분할 전송
        chunks = [tagged_text[i:i+4000] for i in range(0, len(tagged_text), 4000)]
        try:
            for i, chunk in enumerate(chunks):
                payload = {
                    "chat_id": CHAT_ID,
                    "text": chunk,
                    "disable_notification": False,
                }
                if keyboard and i == len(chunks) - 1:   # 마지막 청크에만 버튼
                    payload["reply_markup"] = {"inline_keyboard": keyboard}
                requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                    json=payload, timeout=5
                )
        except Exception as e:
            cprint(f"❌ 텔레그램 전송 오류: {e}", Fore.RED)

# ── 사용 빈도 추적 ────────────────────────────────────────
CMD_USAGE_FILE = os.path.join(BASE_DIR, ".cmd_usage.json")

def _load_cmd_usage():
    try:
        with open(CMD_USAGE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def _save_cmd_usage(usage):
    try:
        with open(CMD_USAGE_FILE, "w", encoding="utf-8") as f:
            json.dump(usage, f, ensure_ascii=False)
    except:
        pass

def track_cmd(cmd):
    usage = _load_cmd_usage()
    usage[cmd] = usage.get(cmd, 0) + 1
    _save_cmd_usage(usage)

# ── 메뉴 카테고리 정의 ────────────────────────────────────
MENU_CATEGORIES = {
    "매매": {
        "emoji": "📈",
        "cmds": [
            ("/start",      "⏯ 시작/정지"),
            ("/buy",        "🛒 매수"),
            ("/sell",       "🔴 즉시매도"),
            ("/aggressive", "⚡ 공격적"),
            ("/normal",     "🛡️ 일반"),
            ("/hold",       "📌 포지션등록"),
            ("/pause",      "⏸️ 일시정지"),
        ]
    },
    "조회": {
        "emoji": "📊",
        "cmds": [
            ("/status",   "📊 상태"),
            ("/balance",  "💰 잔고"),
            ("/why",      "🔍 왜 안사?"),
            ("/why sell", "🔍 왜 안팔아?"),
            ("/report",   "📋 리포트"),
            ("/weekly",   "📆 주간"),
            ("/log",      "📋 거래내역"),
            ("/analyze",  "🧪 로그분석"),
        ]
    },
    "설정": {
        "emoji": "⚙️",
        "cmds": [
            ("/set",    "⚙️ 수치변경"),
            ("/budget", "💵 예산변경"),
            ("/risk",   "🛑 손실한도"),
            ("/reload", "🔄 설정재로드"),
            ("/switch", "🔁 종목전환"),
            ("/grid",   "🟩 그리드"),
        ]
    },
    "시스템": {
        "emoji": "🔧",
        "cmds": [
            ("/restart",  "🔄 재시작"),
            ("/update",   "⬆️ 업데이트"),
            ("/version",  "🏷️ 버전"),
            ("/rollback", "⏪ 롤백"),
            ("/sysinfo",  "🖥️ 시스템정보"),
            ("/ip",       "🌐 IP확인"),
            ("/reboot",   "🔁 PC재부팅"),
            ("/screen",   "📸 화면캡처"),
        ]
    },
}

# ============================================================
# [Reply Keyboard] 하단 고정 버튼 + 상단 고정 수치 메시지
# ============================================================

# 하단 고정 Reply Keyboard — 앱 껐다 켜도 유지됨
REPLY_KEYBOARD = {
    "keyboard": [
        ["⏯ 시작/정지", "📊 상태",    "🔍 왜안사?"],
        ["🔴 즉시매도",  "💰 잔고",    "⚙️ 설정"],
        ["⚡ 공격모드",  "🛡️ 일반모드", "📋 메뉴"],
    ],
    "resize_keyboard":  True,   # 버튼 크기 자동 조절
    "persistent":       True,   # 채팅창 항상 표시 (텔레그램 앱 7.8+)
    "input_field_placeholder": "명령어 입력 또는 버튼 클릭",
}

# Reply Keyboard 버튼 텍스트 → 명령어 매핑
REPLY_CMD_MAP = {
    "⏯ 시작/정지":  "/start",
    "📊 상태":       "/status",
    "🔍 왜안사?":    "/why",
    "🔴 즉시매도":   "/sell",
    "💰 잔고":       "/balance",
    "⚙️ 설정":       "/set",
    "⚡ 공격모드":   "/aggressive",
    "🛡️ 일반모드":   "/normal",
    "📋 메뉴":       "/menu",
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


def setup_reply_keyboard():
    """봇 시작 시 하단 고정 Reply Keyboard 전송."""
    if not TELEGRAM_TOKEN or not CHAT_ID:
        return
    res = _tg_api(
        "sendMessage",
        chat_id=CHAT_ID,
        text="🔘 버튼 메뉴가 하단에 고정됩니다.",
        reply_markup=REPLY_KEYBOARD,
        disable_notification=True,
    )
    # 이 메시지 자체는 필요없으므로 바로 삭제
    # 메시지 삭제 안 함 — 삭제하면 Reply Keyboard 해제됨


def _build_pinned_text() -> str:
    """상단 고정 메시지에 표시할 수치 텍스트 생성."""
    price   = _last_price or 0
    rsi     = bot.get("_last_rsi")
    ma20    = bot.get("_ma20")
    ma60    = bot.get("_ma60")
    rsi_s   = f"{rsi:.1f}" if rsi else "N/A"
    mode_s  = "⚡공격" if _aggressive_mode else "🛡️일반"
    run_s   = "▶️" if bot["running"] else "⏹️"

    # 손익
    if bot["has_stock"] and bot["buy_price"] > 0:
        pnl_p  = (price - bot["buy_price"]) / bot["buy_price"] * 100
        hold_m = int((time.time() - _buy_time) / 60) if _buy_time else 0
        fee_adj = FEE_RATE * 2 * 100
        pos_s  = f"📦 {pnl_p:+.2f}% ({hold_m}분)"
        net_s  = f"실질 {pnl_p - fee_adj:+.2f}%"
    else:
        pos_s = "⏳ 미보유"
        net_s = ""

    # MA 괴리
    ma_s = f"MA20({(price/ma20-1)*100:+.2f}%)" if ma20 and price else ""

    lines = [
        f"🪙 {_coin_name()}  {run_s} {mode_s}",
        f"━━━━━━━━━━━━━━",
        f"💵 {price:,.0f}원",
        f"📈 RSI {rsi_s}  {ma_s}",
        f"{pos_s}{'  ' + net_s if net_s else ''}",
        f"━━━━━━━━━━━━━━",
        f"오늘 {daily_pnl_krw:+,}원  |  거래 {trade_count}회",
        f"🕐 {datetime.now().strftime('%H:%M:%S')} 업데이트",
    ]
    return "\n".join(lines)


def init_pinned_message():
    """봇 시작 시 상단 고정 메시지 생성 및 pin."""
    global _pinned_msg_id
    if not TELEGRAM_TOKEN or not CHAT_ID:
        return
    text = _build_pinned_text()
    res  = _tg_api(
        "sendMessage",
        chat_id=CHAT_ID,
        text=text,
        disable_notification=True,
    )
    msg_id = res.get("result", {}).get("message_id")
    if msg_id:
        _pinned_msg_id = msg_id
        _tg_api(
            "pinChatMessage",
            chat_id=CHAT_ID,
            message_id=msg_id,
            disable_notification=True,
        )
        cprint(f"✅ 상단 고정 메시지 설정 (msg_id={msg_id})", Fore.GREEN)


def _write_status_for_manager():
    if not _IPC_STATUS_FILE:
        return
    try:
        price = _last_price or 0
        rsi   = bot.get("_last_rsi")
        pnl_p, hold_m = 0.0, 0
        if bot.get("has_stock") and bot.get("buy_price", 0) > 0 and price:
            pnl_p  = (price - bot["buy_price"]) / bot["buy_price"] * 100
            hold_m = int((time.time() - (_buy_time or time.time())) / 60)
        tmp = _IPC_STATUS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"price": price, "rsi": round(rsi,2) if rsi else None,
                       "holding": bool(bot.get("has_stock")),
                       "pnl_pct": round(pnl_p,2), "hold_min": hold_m,
                       "running": bool(bot.get("running")),
                       "pnl_today": daily_pnl_krw, "trades": trade_count,
                       "ts": time.time()}, f, ensure_ascii=False)
        os.replace(tmp, _IPC_STATUS_FILE)
    except Exception as e:
        cprint(f"[상태보고 오류] {e}", Fore.YELLOW)


def update_pinned_message():
    """30초마다 상단 고정 메시지 수치 업데이트."""
    global _pinned_msg_id, _pinned_last_update
    now = time.time()
    if now - _pinned_last_update < PINNED_UPDATE_INTERVAL:
        return
    _pinned_last_update = now
    _write_status_for_manager()
    if not _pinned_msg_id or _manager_is_running():
        return
    res = _tg_api("editMessageText", chat_id=CHAT_ID,
                  message_id=_pinned_msg_id, text=_build_pinned_text())
    if not res.get("ok") and "message to edit not found" in str(res.get("description", "")):
        cprint("[고정 메시지 재생성]", Fore.YELLOW)
        init_pinned_message()


# 상단 고정 빠른 버튼 (항상 표시)
# ⚠️ /status, /start 등 단순 명령은 매니저가 가로채므로
#    단독 실행 시에만 직접 처리, 매니저 하위에선 /bot_cmd coin * 사용
KB_QUICK = [
    [
        {"text": "⏯ 시작/정지",  "callback_data": "/start"},
        {"text": "📊 상태",       "callback_data": "/status"},
        {"text": "🛒 매수",       "callback_data": "/buy"},
        {"text": "🔴 매도",       "callback_data": "/sell"},
    ],
]

def _build_menu_keyboard(category=None):
    """카테고리 선택 또는 카테고리 내 명령어 버튼 생성"""
    usage = _load_cmd_usage()

    if category is None:
        cat_row = []
        for cat_name, cat_info in MENU_CATEGORIES.items():
            cat_row.append({
                "text": f"{cat_info['emoji']} {cat_name}",
                "callback_data": f"/menu_cat {cat_name}"
            })
        return KB_QUICK + [cat_row]
    else:
        cat_info = MENU_CATEGORIES.get(category)
        if not cat_info:
            return KB_QUICK
        cmds = sorted(cat_info["cmds"], key=lambda x: usage.get(x[0], 0), reverse=True)
        rows = []
        row = []
        for i, (cmd, label) in enumerate(cmds):
            row.append({"text": label, "callback_data": cmd})
            if len(row) == 3:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([{"text": "◀️ 뒤로", "callback_data": "/menu"}])
        return KB_QUICK + rows

KB_MAIN = _build_menu_keyboard()

KB_SETTINGS = [
    [{"text": "📊 현재 수치 보기", "callback_data": "/set"}],
    [{"text": "RSI 35", "callback_data": "/set rsi_buy 35"},
     {"text": "RSI 40", "callback_data": "/set rsi_buy 40"},
     {"text": "RSI 45", "callback_data": "/set rsi_buy 45"},
     {"text": "RSI 50", "callback_data": "/set rsi_buy 50"}],
    [{"text": "익절 0.5%", "callback_data": "/set target 0.5"},
     {"text": "익절 0.8%", "callback_data": "/set target 0.8"},
     {"text": "익절 1.0%", "callback_data": "/set target 1.0"},
     {"text": "익절 1.5%", "callback_data": "/set target 1.5"}],
    [{"text": "손절 0.5%", "callback_data": "/set max_loss -0.5"},
     {"text": "손절 0.8%", "callback_data": "/set max_loss -0.8"},
     {"text": "손절 1.0%", "callback_data": "/set max_loss -1.0"},
     {"text": "손절 1.5%", "callback_data": "/set max_loss -1.5"}],
    [{"text": "🟩 그리드 ON",   "callback_data": "/grid on"},
     {"text": "⬜ 그리드 OFF",  "callback_data": "/grid off"},
     {"text": "📋 그리드 상태", "callback_data": "/grid status"}],
    [{"text": "◀️ 메뉴", "callback_data": "/menu"}],
]


def send_screen(caption_suffix=""):
    """화면 캡처 → JPEG 압축 → 텔레그램 전송 (Wayland: gnome-screenshot)"""
    import subprocess as _sp, io, tempfile, os as _os
    try:
        from PIL import Image
    except ImportError:
        send_msg("❌ Pillow 없음\npip3 install pillow --break-system-packages", level="normal", force=True)
        return False
    try:
        tmp = tempfile.mktemp(suffix=".png")
        env = _os.environ.copy()
        env["DISPLAY"] = ":0"
        # XAUTHORITY 자동 감지
        import glob
        auth_files = glob.glob("/run/user/*/.*auth*") + glob.glob("/run/user/*/.mutter*")
        if auth_files:
            env["XAUTHORITY"] = auth_files[0]
        # gnome-screenshot 시도, 실패 시 scrot
        ret = _sp.run(
            ["gnome-screenshot", "-f", tmp],
            capture_output=True, timeout=10, env=env
        )
        if ret.returncode != 0:
            ret = _sp.run(["scrot", tmp], capture_output=True, timeout=5, env=env)
        if ret.returncode != 0 or not _os.path.exists(tmp):
            send_msg(f"❌ 캡처 실패\n{ret.stderr.decode('utf-8', errors='ignore')}", level="normal", force=True)
            return False
        img = Image.open(tmp)
        w, h = img.size
        img = img.resize((w // 2, h // 2), Image.LANCZOS)
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=50, optimize=True)
        buf.seek(0)
        _os.remove(tmp)
        label   = datetime.now().strftime("%H:%M:%S")
        caption = f"🖥️ [{label}]{' ' + caption_suffix if caption_suffix else ''}"
        res = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
            data={"chat_id": CHAT_ID, "caption": caption},
            files={"photo": ("screen.jpg", buf, "image/jpeg")},
            timeout=20
        )
        return res.status_code == 200
    except Exception as e:
        send_msg(f"❌ 화면 캡처 실패: {e}", level="normal", force=True)
        return False

def log_change(category, detail):
    today    = str(date.today())
    filepath = os.path.join(CHANGELOG_DIR, f"changelog_{today}.txt")
    now_str  = datetime.now().strftime("%H:%M:%S")
    with open(filepath, "a", encoding="utf-8") as f:
        f.write(f"[{now_str}] [{category}] {detail}\n")


# ============================================================
# [MARKET FILTER] 시장 전체 하락 감지
# ============================================================
_market_trend_cache      = {"ts": 0.0, "down": 0, "total": 0, "names": [], "is_down": False}
_MARKET_TREND_TTL        = 300.0  # 5분 캐시
_MARKET_TREND_TOP_N      = 5      # 거래대금 상위 N개
_MARKET_TREND_DOWN_LIMIT = 3      # 이 개수 이상 -1% 하락이면 시장 하락
_MARKET_BLOCK_UNTIL      = 0.0    # 차단 쿨다운 종료 시각

def _get_5m_change(market):
    """5분봉 2개로 변화율 계산. 실패 시 0.0 반환."""
    try:
        _api_throttle()
        res = requests.get(
            f"{UPBIT_BASE}/candles/minutes/5",
            params={"market": market, "count": 2},
            timeout=5
        )
        if res.status_code == 200:
            data = res.json()
            if len(data) >= 2:
                curr = float(data[0]["trade_price"])
                prev = float(data[1]["trade_price"])
                return (curr - prev) / prev * 100
    except Exception as e:
        cprint(f"[5분봉 조회 오류] {market}: {e}", Fore.YELLOW)
    return 0.0


def get_market_trend(force=False):
    """
    거래대금 상위 5개 종목의 5분 변화율 기준으로 시장 하락 판정.
    - 3개 이상이 -1% 이하 하락 시 차단
    - BTC 단독 -0.7% 이하 시 차단
    - 차단 후 5분 쿨다운

    반환: (is_down: bool, avg_rate: float, total: int, names: list[str])
    캐시: 5분
    """
    global _market_trend_cache, _MARKET_BLOCK_UNTIL
    now = time.time()
    if not force and now < _MARKET_BLOCK_UNTIL:
        c = _market_trend_cache
        return True, c.get("down", 0.0), c.get("total", 0), c.get("names", [])
    if not force and now - _market_trend_cache["ts"] < _MARKET_TREND_TTL:
        c = _market_trend_cache
        return c.get("is_down", False), c["down"], c["total"], c["names"]

    try:
        _api_throttle()
        # ① 전체 KRW 마켓 목록
        res = requests.get(
            f"{UPBIT_BASE}/market/all",
            params={"isDetails": "false"},
            timeout=5
        )
        if res.status_code != 200:
            raise RuntimeError(f"market/all {res.status_code}")
        krw_markets = [m["market"] for m in res.json() if m["market"].startswith("KRW-")]

        # ② 배치 시세 조회 (최대 100개씩 — 업비트 제한)
        batch_size = 100
        tickers = []
        for i in range(0, len(krw_markets), batch_size):
            _api_throttle()
            chunk = krw_markets[i:i + batch_size]
            r2 = requests.get(
                f"{UPBIT_BASE}/ticker",
                params={"markets": ",".join(chunk)},
                timeout=5
            )
            if r2.status_code == 200:
                tickers.extend(r2.json())

        if not tickers:
            raise RuntimeError("ticker 조회 실패")

        # ③ 거래대금(acc_trade_price_24h) 기준 상위 N개 추출
        _EXCLUDE_TICKERS = {"KRW-USDT", "KRW-BUSD", "KRW-DAI", "KRW-USDC"}
        tickers.sort(key=lambda x: float(x.get("acc_trade_price_24h", 0)), reverse=True)
        tickers = [t for t in tickers if t["market"] not in _EXCLUDE_TICKERS]
        top = tickers[:_MARKET_TREND_TOP_N]

        # ④ 5분 변화율 계산
        names = [t["market"] for t in top]
        changes = {m: _get_5m_change(m) for m in names}

        # BTC 단독 필터 (top5에 없으면 별도 호출)
        if "KRW-BTC" not in changes:
            changes["KRW-BTC"] = _get_5m_change("KRW-BTC")
        btc_drop = changes["KRW-BTC"]
        btc_block = btc_drop <= -0.7

        # 3개 이상 -1% 이하
        down_count = sum(1 for v in changes.values() if v <= -1.0)
        is_down = down_count >= _MARKET_TREND_DOWN_LIMIT or btc_block

        # 차단 시 쿨다운 설정
        if is_down:
            _MARKET_BLOCK_UNTIL = now + 300

        avg_rate = sum(changes.values()) / len(changes) if changes else 0
        name_list = [m.replace("KRW-", "") for m in names]
        total = len(top)
        _market_trend_cache = {"ts": now, "down": avg_rate, "total": total, "names": name_list, "is_down": is_down}
        return is_down, avg_rate, total, name_list
        return down >= _MARKET_TREND_DOWN_LIMIT, down, total, names

    except Exception as e:
        cprint(f"[시장 트렌드 조회 오류] {e}", Fore.YELLOW)
        c = _market_trend_cache
        return False, c.get("down", 0), c.get("total", 0), c.get("names", [])


def market_trend_msg(avg_rate, total, names):
    """시장 하락 상태 메시지 조각 반환."""
    status = "📉 하락세" if avg_rate < -1.0 else "📈 상승세"
    names_str = ", ".join(names)
    return status + "  거래대금 상위 " + str(total) + "종목 평균 " + f"{avg_rate:+.2f}%" + "\n  (" + names_str + ")"


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

# ── API Rate Limiter ──────────────────────────────────────────
# 업비트 제한: 초당 10회(주문), 분당 600회(조회)
_api_last_call  = 0.0
_API_MIN_INTERVAL = 0.12   # 최소 120ms 간격 (초당 약 8회)

def _api_throttle():
    """API 호출 전 최소 간격 보장 (rate limit 방지)."""
    global _api_last_call
    elapsed = time.time() - _api_last_call
    if elapsed < _API_MIN_INTERVAL:
        time.sleep(_API_MIN_INTERVAL - elapsed)
    _api_last_call = time.time()

def get_price_and_volume(market=None):
    """현재가 + 거래량 조회. 반환: (price, volume) 또는 (None, None)
    [PATCH] 매니저 ticker 파일 우선 → 없거나 오래되면 직접 API fallback"""
    import json as _gj, time as _gt
    if market is None:
        market = MARKET_CODE
    try:
        code = market.replace("KRW-", "").lower()
        tick_path = os.path.join(SHARED_DIR, f"ticker_{code}.json")
        if os.path.exists(tick_path):
            with open(tick_path) as _f:
                _td = _gj.load(_f)
            if _gt.time() - _td.get("ts", 0) < 2.0:
                return float(_td["price"]), float(_td["volume"])
    except Exception:
        pass
    try:
        _api_throttle()
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
        _api_throttle()
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
        _api_throttle()
        h = _upbit_headers()
        res = requests.get(f"{UPBIT_BASE}/accounts", headers=h, timeout=5)
        if res.status_code == 200:
            for acc in res.json():
                if acc["currency"] == currency:
                    return float(acc["balance"])
    except Exception as e:
        cprint(f"[코인 잔고 조회 오류] {e}", Fore.YELLOW)
    return 0.0

def _round_to_tick(price: float, market: str) -> float:
    """업비트 호가 단위(tick size)에 맞게 가격 반올림.
    시장가 주문에는 불필요하지만 지정가 주문 시 필수."""
    if price <= 0:
        return price
    # 업비트 원화 마켓 호가 단위 기준
    if price >= 2_000_000: tick = 1000
    elif price >= 1_000_000: tick = 500
    elif price >= 500_000:  tick = 100
    elif price >= 100_000:  tick = 50
    elif price >= 10_000:   tick = 10
    elif price >= 1_000:    tick = 1
    elif price >= 100:      tick = 0.1
    elif price >= 10:       tick = 0.01
    elif price >= 1:        tick = 0.001
    else:                   tick = 0.0001
    return round(round(price / tick) * tick, 10)


def send_order(side, market, qty_or_price):
    """업비트 시장가 주문.
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
    """주문 체결 확인. 부분 체결 포함 처리.
    반환: (체결수량, 평균체결가) — 미체결이면 (0, 0)"""
    if not order_uuid:
        return 0, 0
    for attempt in range(retry):
        try:
            params = {"uuid": order_uuid}
            h = _upbit_headers(params)
            res = requests.get(f"{UPBIT_BASE}/order", headers=h, params=params, timeout=5)
            if res.status_code == 200:
                data  = res.json()
                state = data.get("state", "")

                # 체결수량/평균가 공통 계산
                trades       = data.get("trades", [])
                total_volume = sum(float(t["volume"]) for t in trades)
                total_value  = sum(float(t["price"]) * float(t["volume"]) for t in trades)
                avg_p        = (total_value / total_volume) if total_volume > 0 else float(data.get("avg_price", 0))
                filled       = float(data.get("executed_volume", total_volume))

                if state == "done":
                    return filled, avg_p

                elif state == "cancel":
                    # 부분 체결 후 취소된 경우 — 체결된 만큼 반환
                    if filled > 0:
                        cprint(f"[부분체결] {filled} @ {avg_p:.2f}", Fore.YELLOW)
                        return filled, avg_p
                    return 0, 0

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
    """분봉 OHLCV 조회 — RSI/MA 계산용.
    현재 진행 중인 캔들(마지막)은 제외 — 미완성 캔들로 신호 왜곡 방지."""
    if market is None:
        market = MARKET_CODE
    try:
        _api_throttle()
        url = f"{UPBIT_BASE}/candles/minutes/{interval}"
        res = requests.get(
            url,
            params={"market": market, "count": count + 1},  # +1: 마지막(진행중) 제외 위해
            timeout=10
        )
        if res.status_code == 200:
            data = res.json()
            # 최신순 → 오래된순 정렬
            data = sorted(data, key=lambda x: x["candle_date_time_utc"])
            # 마지막 캔들(현재 진행 중) 제외 → 완성된 캔들만 사용
            if len(data) > 1:
                data = data[:-1]
            return [float(c["trade_price"]) for c in data]
    except Exception as e:
        cprint(f"[OHLCV 조회 오류] {e}", Fore.YELLOW)
    return []


def get_day_ohlcv(market=None, count=2):
    """일봉 OHLCV 조회 — 변동성 돌파용"""
    if market is None:
        market = MARKET_CODE
    try:
        _api_throttle()
        res = requests.get(
            f"{UPBIT_BASE}/candles/days",
            params={"market": market, "count": count},
            timeout=5
        )
        if res.status_code == 200:
            data = sorted(res.json(), key=lambda x: x["candle_date_time_utc"])
            return data
    except Exception as e:
        cprint(f"[일봉 조회 오류] {e}", Fore.YELLOW)
    return []

_vbreak_k     = 0.5
_vbreak_cache = {}

def calc_vbreak_target(market=None):
    """변동성 돌파 목표가. 반환: 목표가 또는 None"""
    from datetime import date as _date
    if market is None:
        market = MARKET_CODE
    today = str(_date.today())
    if market in _vbreak_cache:
        cached_date, cached_target = _vbreak_cache[market]
        if cached_date == today:
            return cached_target
    candles = get_day_ohlcv(market, count=3)
    if len(candles) < 2:
        return None
    prev       = candles[-2]
    today_c    = candles[-1]
    prev_range = float(prev["high_price"]) - float(prev["low_price"])
    today_open = float(today_c["opening_price"])
    target     = today_open + prev_range * _vbreak_k
    _vbreak_cache[market] = (today, target)
    cprint(f"[변동성돌파] {market} 목표가:{target:,.2f}", Fore.CYAN)
    return target

def check_vbreak_signal(price, market=None):
    """변동성 돌파 신호. 반환: (신호여부, 목표가)"""
    target = calc_vbreak_target(market)
    if target is None:
        return False, 0
    return price >= target, target

# ============================================================
# [7] 지표 계산
# ============================================================
def calc_ma5(prices):
    p = list(prices)
    return float(sum(p[:5]) / 5) if len(p) >= 5 else None

def calc_momentum(prices):
    p = list(prices)
    if len(p) < 11: return None
    old = p[10]
    return ((p[0] - old) / old * 100) if old else None

def detect_train(rsi, ma5, ma20, ma60, prices, vol_ratio):
    count, detail = 0, []
    chk = lambda ok: "✅" if ok else "❌"

    rsi_ok = rsi is not None and bot.get("train_rsi_min",50) <= rsi <= bot.get("train_rsi_max",65)
    count += rsi_ok
    detail.append("%s RSI %s (기준 %s~%s)" % (chk(rsi_ok), ("%.1f" % rsi) if rsi is not None else "N/A", bot.get("train_rsi_min",50), bot.get("train_rsi_max",65)))

    ma_ok = bool(ma5 and ma20 and ma60 and ma5 > ma20 > ma60)
    count += ma_ok
    detail.append(f"{chk(ma_ok)} MA정배열 MA5>{f'{ma5:,.0f}' if ma5 else 'N/A'}>MA20>MA60")

    mom = calc_momentum(prices)
    mom_ok = mom is not None and mom >= bot.get("train_momentum", 1.5)
    count += mom_ok
    detail.append("%s 모멘텀 %s%% (기준 +%s%%)" % (chk(mom_ok), ("%.2f" % mom) if mom is not None else "N/A", bot.get("train_momentum",1.5)))

    vol_ok = vol_ratio is not None and vol_ratio >= bot.get("train_vol_ratio", 1.5)
    count += vol_ok
    detail.append("%s 거래량 %s배 (기준 %s배)" % (chk(vol_ok), ("%.1f" % vol_ratio) if vol_ratio else "N/A", bot.get("train_vol_ratio",1.5)))

    volp_ok = True  # 변동성은 매수 루프에서 이미 필터링됨
    count += volp_ok
    detail.append("%s 변동성 (루프 필터 통과)" % chk(volp_ok))

    return count >= 4, count, detail

def calc_rsi(prices, period=14):
    """Wilder's RSI 계산 — EMA 방식."""
    n = len(prices)
    if n < period + 1:
        return None
    # deque에서 직접 numpy 배열 생성 (list() 변환 최소화)
    needed = min(n, period + 100)
    p      = np.fromiter((prices[n - needed + i] for i in range(needed)), dtype=float, count=needed)
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
    n = len(prices)
    if n < period:
        return None
    # 마지막 period개만 평균 (list 변환 없이)
    return float(np.mean([prices[n - period + i] for i in range(period)]))

def calc_vol_pct(timed_buf, window_sec=300):
    now = time.time()
    pts = [p for t, p in timed_buf if now - t <= window_sec]
    if len(pts) < 2:
        return None
    return (max(pts) - min(pts)) / min(pts) * 100

def calc_vwap(prices_vol):
    """VWAP 계산 유틸: prices_vol = [(price, volume), ...]
    현재 봇은 실시간 누적 VWAP(_vwap_sum_pv/_vwap_sum_v)를 사용하므로
    이 함수는 백테스트/분석용으로 보존."""
    if not prices_vol:
        return None
    total_pv = sum(p * v for p, v in prices_vol)
    total_v  = sum(v for _, v in prices_vol)
    return total_pv / total_v if total_v > 0 else None

def get_atr(prices, period=14):
    """ATR 계산 — True Range: max(고-저, |고-전종가|, |저-전종가|).
    price_history는 종가만 있으므로 근사값으로 계산."""
    if len(prices) < period + 1:
        return None
    p   = list(prices)
    # 종가만 있을 때: TR ≈ |현재가 - 전일가| (단순 근사)
    # 더 정확하게는 OHLCV 캔들이 필요하나 실시간 틱에서는 이 방식 사용
    trs = [abs(p[i] - p[i-1]) for i in range(1, len(p))]
    if not trs:
        return None
    # 지수이동평균(EMA) 방식 ATR (Wilder's smoothing)
    atr = float(np.mean(trs[:period]))   # 초기값: 단순평균
    alpha = 1.0 / period
    for tr in trs[period:]:
        atr = atr * (1 - alpha) + tr * alpha
    return atr

def calc_vol_ratio(volume_history):
    n = len(volume_history)
    if n < 10:
        return None
    vh = volume_history   # deque slicing via islice or direct indexing
    recent   = [volume_history[i] for i in range(max(0, n-5), n)]
    baseline = [volume_history[i] for i in range(max(0, n-30), n-5)] if n >= 30 \
               else [volume_history[i] for i in range(0, n-5)]
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
    """상태 저장 — tmp 파일에 먼저 쓰고 rename (중간 오류 시 파일 손상 방지)."""
    from datetime import datetime as _dt
    now = _dt.now()
    data = {
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
        "last_update_id":      last_update_id,
        "target":              bot["target"],
        "max_loss":            bot["max_loss"],
        "drop":                bot["drop"],
        "trail_start":         bot["trail_start"],
        "trail_gap":           bot["trail_gap"],
        "be_trigger":          bot["be_trigger"],
        "rsi_buy":             bot["rsi_buy"],
        "running":             bot["running"],
        "aggressive_mode":     _aggressive_mode,
        "dynamic_mode":        _dynamic_mode,
        "date":                str(date.today()),
    }
    tmp = STATE_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, STATE_FILE)
    except Exception as e:
        cprint(f"[상태 저장 오류] {e}", Fore.YELLOW)
        try:
            os.remove(tmp)
        except Exception:
            pass

def load_state():
    global daily_pnl_krw, weekly_pnl_krw, trade_count, highest_profit, _last_sell_time
    global DAILY_LOSS_BASE_KRW, ORDER_BUDGET_KRW
    global win_count, loss_count, consecutive_loss, consecutive_win
    global _buy_time, _weekly_stop, MARKET_CODE, last_update_id
    global _aggressive_mode, _dynamic_mode
    if not os.path.exists(STATE_FILE):
        return
    # 파일 손상 시 백업에서 복구 시도
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            s = json.load(f)
        # 성공하면 백업 갱신
        try:
            import shutil as _sh
            _sh.copy2(STATE_FILE, STATE_FILE + ".bak")
        except Exception:
            pass
    except (json.JSONDecodeError, OSError) as e:
        cprint(f"[상태 파일 손상] {e} → 백업 복구 시도", Fore.YELLOW)
        bak = STATE_FILE + ".bak"
        if os.path.exists(bak):
            try:
                with open(bak, encoding="utf-8") as f:
                    s = json.load(f)
                cprint("✅ 백업에서 복구 성공", Fore.GREEN)
            except Exception:
                cprint("❌ 백업도 손상 — 초기값 사용", Fore.RED)
                return
        else:
            cprint("❌ 백업 없음 — 초기값 사용", Fore.RED)
            return
    bot.update({
        "target":      s.get("target",      bot["target"]),
        "max_loss":    s.get("max_loss",     bot["max_loss"]),
        "drop":        s.get("drop",         bot["drop"]),
        "trail_start": s.get("trail_start",  bot["trail_start"]),
        "trail_gap":   s.get("trail_gap",    bot["trail_gap"]),
        "be_trigger":  s.get("be_trigger",   bot["be_trigger"]),
        "rsi_buy":     s.get("rsi_buy",      bot["rsi_buy"]),
        "running":     s.get("running",      bot["running"]),
    })
    # 모드 복원 (재시작해도 공격적/동적 설정 유지)
    if "aggressive_mode" in s:
        _aggressive_mode = s["aggressive_mode"]
    if "dynamic_mode" in s:
        _dynamic_mode    = s["dynamic_mode"]
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
    # update_id 복원 — 전용 파일 우선, 없으면 state에서
    _uid_file = os.path.join(BASE_DIR, ".last_update_id")
    try:
        with open(_uid_file) as _f:
            last_update_id = int(_f.read().strip())
    except:
        last_update_id = s.get("last_update_id", 0)

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
    _lf, _ = _get_log_files()
    write_header = not os.path.exists(_lf)
    with open(_lf, "a", newline="", encoding="utf-8") as f:
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

def run_analyze(days=7):
    import csv as _csv
    from datetime import datetime as _dt, timedelta as _td

    send_msg(f"🔍 최근 {days}일 로그 분석 중...\n잠깐만 기다려주세요!", level="normal", force=True)

    cutoff = _dt.now() - _td(days=days)
    result_lines = [f"📊 로그 분석 결과 (최근 {days}일)\n━━━━━━━━━━━━━━━━━━━━"]

    # ── indicator_log 읽기 ───────────────────────────────────
    if not os.path.exists(INDICATOR_LOG_FILE):
        send_msg(
            "❌ 분석 데이터가 아직 없어요\n"
            "봇을 최소 하루 이상 실행한 후 다시 시도해주세요.",
            level="normal", force=True
        )
        return

    rows = []
    try:
        with open(INDICATOR_LOG_FILE, encoding="utf-8") as f:
            for row in _csv.DictReader(f):
                try:
                    ts = _dt.strptime(row["datetime"][:19], "%Y-%m-%d %H:%M:%S")
                    if ts >= cutoff:
                        rows.append(row)
                except:
                    pass
    except Exception as e:
        send_msg(f"❌ 로그 읽기 실패: {e}", level="normal", force=True)
        return

    if len(rows) < 30:
        send_msg(
            f"❌ 데이터가 너무 적어요 ({len(rows)}개)\n"
            f"최소 30개 이상 필요해요. 조금 더 기다려주세요.",
            level="normal", force=True
        )
        return

    result_lines.append(f"분석한 데이터: {len(rows):,}개")

    # ── RSI 분석 ────────────────────────────────────────────
    rsi_vals   = [float(r["rsi"]) for r in rows if r.get("rsi")]
    rsi_u30    = sum(1 for v in rsi_vals if v <= 30)
    rsi_u38    = sum(1 for v in rsi_vals if v <= 38)
    rsi_u45    = sum(1 for v in rsi_vals if v <= 45)
    rsi_pct38  = rsi_u38 / len(rsi_vals) * 100 if rsi_vals else 0
    rsi_pct45  = rsi_u45 / len(rsi_vals) * 100 if rsi_vals else 0

    result_lines.append(f"\n📉 RSI (매수 타이밍 지표)")
    result_lines.append(f"  RSI가 낮을수록 가격이 많이 떨어진 상태예요")
    result_lines.append(f"  30 이하(많이 떨어짐): {rsi_u30}회 ({rsi_u30/len(rsi_vals)*100:.1f}%)")
    result_lines.append(f"  38 이하: {rsi_u38}회 ({rsi_pct38:.1f}%)")
    result_lines.append(f"  45 이하: {rsi_u45}회 ({rsi_pct45:.1f}%)")

    # RSI 추천
    if rsi_pct38 < 3:
        rec_rsi = 45
        rsi_reason = "신호가 너무 적어요 → 기준을 올려서 더 자주 매수"
    elif rsi_pct38 > 25:
        rec_rsi = 32
        rsi_reason = "신호가 너무 많아요 → 기준을 낮춰서 더 좋은 타이밍만"
    elif rsi_pct38 < 8:
        rec_rsi = 40
        rsi_reason = "신호가 약간 적어요 → 살짝 올려보세요"
    else:
        rec_rsi = bot["rsi_buy"]
        rsi_reason = "지금이 적당해요 👍"
    result_lines.append(f"  → 추천 RSI 기준: {rec_rsi} ({rsi_reason})")

    # ── 눌림(drop) 분석 ─────────────────────────────────────
    drop_vals = [float(r["drop_pct"]) for r in rows if r.get("drop_pct") and r["drop_pct"]]
    drop_pos  = sorted([v for v in drop_vals if v > 0])
    rec_drop  = bot.get("drop", 0.5)

    if drop_pos:
        p25  = drop_pos[int(len(drop_pos) * 0.25)]
        p50  = drop_pos[int(len(drop_pos) * 0.50)]
        p75  = drop_pos[int(len(drop_pos) * 0.75)]
        result_lines.append(f"\n📉 눌림 (평균가 대비 얼마나 떨어졌는지)")
        result_lines.append(f"  눌림이 커야 저점에서 매수할 수 있어요")
        result_lines.append(f"  평균: {p50:.2f}%  자주 보이는 범위: {p25:.2f}~{p75:.2f}%")
        rec_drop = round(max(0.1, min(p25, 0.8)), 2)
        drop_reason = f"실제 데이터 하위 25% 기준"
        result_lines.append(f"  → 추천 눌림 기준: {rec_drop}% ({drop_reason})")

    # ── 변동성 분석 ─────────────────────────────────────────
    vol_vals = [float(r["vol_pct"]) for r in rows if r.get("vol_pct") and r["vol_pct"]]
    rec_vol_min = VOL_MIN_PCT
    rec_vol_max = VOL_MAX_PCT

    if vol_vals:
        vol_sorted = sorted(vol_vals)
        vol_p10    = vol_sorted[int(len(vol_sorted) * 0.10)]
        vol_p50    = vol_sorted[int(len(vol_sorted) * 0.50)]
        vol_p90    = vol_sorted[int(len(vol_sorted) * 0.90)]
        too_quiet  = sum(1 for v in vol_vals if v < VOL_MIN_PCT)
        too_wild   = sum(1 for v in vol_vals if v > VOL_MAX_PCT)
        result_lines.append(f"\n📊 변동성 (가격이 얼마나 움직이는지)")
        result_lines.append(f"  너무 조용하면 수익 없고, 너무 격하면 손실 위험")
        result_lines.append(f"  낮은편: {vol_p10:.2f}%  중간: {vol_p50:.2f}%  높은편: {vol_p90:.2f}%")
        result_lines.append(f"  현재 기준으로 차단된 시간: {too_quiet+too_wild}회")
        rec_vol_min = round(max(0.05, vol_p10 * 0.8), 2)
        rec_vol_max = round(min(10.0, vol_p90 * 1.2), 1)
        result_lines.append(f"  → 추천 범위: {rec_vol_min}~{rec_vol_max}%")

    # ── 매매 성과 분석 ──────────────────────────────────────
    trades = []
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, encoding="utf-8") as f:
                for row in _csv.DictReader(f):
                    try:
                        ts = _dt.strptime(row["datetime"][:19], "%Y-%m-%d %H:%M:%S")
                        if ts >= cutoff:
                            trades.append(row)
                    except:
                        pass
        except:
            pass

    sells = [r for r in trades if r.get("side") == "SELL"]
    rec_target   = bot["target"]
    rec_max_loss = bot["max_loss"]
    rec_timeout  = POS_TIMEOUT_MIN

    if sells:
        pnls    = [float(r["pnl_krw"]) for r in sells if r.get("pnl_krw")]
        wins    = [v for v in pnls if v > 0]
        losses  = [v for v in pnls if v <= 0]
        win_rate = len(wins) / len(pnls) * 100 if pnls else 0
        total_pnl = sum(pnls)

        result_lines.append(f"\n🏆 매매 성과")
        result_lines.append(
            f"  총 {len(pnls)}번 거래  "
            f"{'😊' if win_rate >= 50 else '😢'} 승률: {win_rate:.0f}%"
        )
        result_lines.append(
            f"  총 손익: {total_pnl:+,.0f}원  "
            f"({'이익' if total_pnl > 0 else '손실'})"
        )

        if wins:
            avg_win = sum(wins) / len(wins)
            result_lines.append(f"  이긴 거래 평균: +{avg_win:,.0f}원")
        if losses:
            avg_loss = sum(losses) / len(losses)
            result_lines.append(f"  진 거래 평균: {avg_loss:,.0f}원")

        # 보유 시간
        hold_times = []
        for r in sells:
            if r.get("pos_hold_time"):
                try:
                    hold_times.append(float(r["pos_hold_time"]))
                except:
                    pass
        if hold_times:
            avg_hold = sum(hold_times) / len(hold_times)
            max_hold = max(hold_times)
            result_lines.append(f"  평균 보유시간: {avg_hold:.0f}분  최대: {max_hold:.0f}분")
            if avg_hold > POS_TIMEOUT_MIN * 0.8:
                rec_timeout = int(avg_hold * 1.5)
                result_lines.append(f"  → 타임아웃을 {rec_timeout}분으로 늘리면 좋을 것 같아요")

        # 익절/손절 추천
        timeout_sells = [r for r in sells if "타임아웃" in r.get("reason","")]
        if timeout_sells and len(timeout_sells) / len(sells) > 0.4:
            result_lines.append(f"  ⚠️ 타임아웃 청산이 {len(timeout_sells)}번 ({len(timeout_sells)/len(sells)*100:.0f}%)")
            result_lines.append(f"     → 익절 목표를 낮추거나 타임아웃을 늘려보세요")
            rec_target = round(bot["target"] * 0.8, 2)

        # RSI 매수 시점 분석
        buy_rsi_vals = []
        for r in trades:
            if r.get("side") == "BUY" and r.get("rsi"):
                try:
                    buy_rsi_vals.append(float(r["rsi"]))
                except:
                    pass
        if buy_rsi_vals:
            avg_buy_rsi = sum(buy_rsi_vals) / len(buy_rsi_vals)
            result_lines.append(f"\n  실제 매수 시 RSI 평균: {avg_buy_rsi:.1f}")
            if avg_buy_rsi > bot["rsi_buy"] + 3:
                result_lines.append(f"  → 실제로는 RSI {avg_buy_rsi:.0f} 근처에서 많이 샀어요")
                rec_rsi = int(avg_buy_rsi + 2)
    else:
        result_lines.append(f"\n🏆 매매 성과: 아직 거래 내역이 없어요")

    # ── 추천 요약 ────────────────────────────────────────────
    result_lines.append(f"\n✨ 추천 세팅 요약")
    result_lines.append(f"━━━━━━━━━━━━━━━━━━━━")
    result_lines.append(f"  RSI 기준  : {bot['rsi_buy']} → {rec_rsi}")
    result_lines.append(f"  익절 목표  : {bot['target']}% → {rec_target}%")
    result_lines.append(f"  손절 기준  : {bot['max_loss']}% → {rec_max_loss}%")
    result_lines.append(f"  눌림 기준  : {bot.get('drop',0.5)}% → {rec_drop}%")
    result_lines.append(f"  변동성 범위: {VOL_MIN_PCT}~{VOL_MAX_PCT}% → {rec_vol_min}~{rec_vol_max}%")
    result_lines.append(f"  타임아웃   : {POS_TIMEOUT_MIN}분 → {rec_timeout}분")
    result_lines.append(f"\n아래 버튼으로 한 번에 적용할 수 있어요 👇")

    # 추천값 적용 버튼
    kb_apply = [
        [{"text": f"✅ RSI {rec_rsi} 적용",
          "callback_data": f"/set rsi_buy {rec_rsi}"},
         {"text": f"✅ 익절 {rec_target}% 적용",
          "callback_data": f"/set target {rec_target}"}],
        [{"text": f"✅ 눌림 {rec_drop}% 적용",
          "callback_data": f"/set drop {rec_drop}"},
         {"text": f"✅ 타임아웃 {rec_timeout}분 적용",
          "callback_data": f"/set timeout_min {rec_timeout}"}],
        [{"text": f"✅ 변동성 {rec_vol_min}~{rec_vol_max}% 적용",
          "callback_data": f"/set vol_min {rec_vol_min}"}],
        [{"text": "🔄 전체 추천값 한번에 적용",
          "callback_data": f"/apply_recommend {rec_rsi} {rec_target} {rec_max_loss} {rec_drop} {rec_vol_min} {rec_vol_max} {rec_timeout}"}],
    ]

    send_msg("\n".join(result_lines), level="normal", force=True, keyboard=kb_apply)


def _analyze_period(days):
    """단일 기간 분석. 모든 오류를 내부에서 처리."""
    import csv as _csv
    from datetime import datetime as _dt, timedelta as _td
    label = "24시간" if days == 1 else f"{days}일"
    cutoff = _dt.now() - _td(days=days)
    empty_result = {
        "label": label, "days": days, "n_rows": 0, "n_trades": 0,
        "rec_rsi": bot["rsi_buy"], "rec_target": bot["target"],
        "rec_max_loss": bot["max_loss"], "rec_drop": bot.get("drop", 0.5),
        "rec_vol_min": VOL_MIN_PCT, "rec_vol_max": VOL_MAX_PCT,
        "rec_timeout": POS_TIMEOUT_MIN,
        "win_rate": None, "total_pnl": None, "detail_lines": [],
        "rec_grid_step": GRID_STEP_PCT, "rec_grid_levels": GRID_MAX_LEVELS,
    }
    try:
        rows = []
        if os.path.exists(INDICATOR_LOG_FILE):
            with open(INDICATOR_LOG_FILE, encoding="utf-8") as f:
                for row in _csv.DictReader(f):
                    try:
                        if _dt.strptime(row["datetime"][:19], "%Y-%m-%d %H:%M:%S") >= cutoff:
                            rows.append(row)
                    except Exception:
                        pass
        trades = []
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, encoding="utf-8") as f:
                for row in _csv.DictReader(f):
                    try:
                        if _dt.strptime(row["datetime"][:19], "%Y-%m-%d %H:%M:%S") >= cutoff:
                            trades.append(row)
                    except Exception:
                        pass
        result = dict(empty_result)
        result["n_rows"] = len(rows)
        result["n_trades"] = len(trades)
        if len(rows) < 10:
            result["detail_lines"] = [f"  데이터 부족 ({len(rows)}개)"]
            return result
        result["detail_lines"].append(
            f"  📅 {rows[0]['datetime'][:16]} ~ {rows[-1]['datetime'][:16]}  ({len(rows):,}개)"
        )
        # RSI
        rsi_vals = [float(r["rsi"]) for r in rows if r.get("rsi")]
        if rsi_vals:
            rsi_pct38 = sum(1 for v in rsi_vals if v <= 38) / len(rsi_vals) * 100
            if rsi_pct38 < 3:    rec_rsi = 45
            elif rsi_pct38 > 25: rec_rsi = 32
            elif rsi_pct38 < 8:  rec_rsi = 40
            else:                rec_rsi = bot["rsi_buy"]
            result["rec_rsi"] = rec_rsi
            result["detail_lines"].append(
                f"  RSI≤38: {rsi_pct38:.1f}%  →  추천 RSI {rec_rsi}"
                + (" ✓" if rec_rsi == bot["rsi_buy"] else f"  (현재 {bot['rsi_buy']})")
            )
        # 눌림
        drop_pos = sorted([float(r["drop_pct"]) for r in rows
                           if r.get("drop_pct") and float(r["drop_pct"]) > 0])
        if drop_pos:
            p25 = drop_pos[int(len(drop_pos)*0.25)]
            p50 = drop_pos[int(len(drop_pos)*0.50)]
            rec_drop = round(max(0.1, min(p25, 0.8)), 2)
            result["rec_drop"] = rec_drop
            result["detail_lines"].append(f"  눌림 중앙값: {p50:.2f}%  →  추천 눌림 {rec_drop}%")
        # 변동성
        vol_vals = [float(r["vol_pct"]) for r in rows if r.get("vol_pct") and r["vol_pct"]]
        if vol_vals:
            vs = sorted(vol_vals)
            rec_vol_min = round(max(0.05, vs[int(len(vs)*0.10)]*0.8), 2)
            rec_vol_max = round(min(10.0, vs[int(len(vs)*0.90)]*1.2), 1)
            result["rec_vol_min"] = rec_vol_min
            result["rec_vol_max"] = rec_vol_max
            result["detail_lines"].append(f"  변동성 범위: {rec_vol_min}~{rec_vol_max}%")
        # 그리드 추천 — 가격 낙폭 분포 기반
        price_vals = [float(r["price"]) for r in rows if r.get("price")]
        if len(price_vals) >= 20:
            # 연속 봉 간 낙폭(%) 계산
            drops_seq = []
            for i in range(1, len(price_vals)):
                d = (price_vals[i] - price_vals[i-1]) / price_vals[i-1] * 100
                if d < 0:
                    drops_seq.append(abs(d))
            if drops_seq:
                ds = sorted(drops_seq)
                p25 = ds[int(len(ds) * 0.25)]
                p75 = ds[int(len(ds) * 0.75)]
                # step: 하위 25% 낙폭 기준, 0.2~2.0% 클램프
                rec_gs = round(max(0.2, min(p25 * 3, 2.0)), 1)
                # levels: |max_loss| / step, 3~10층 클램프
                ml = abs(result.get("rec_max_loss", bot["max_loss"]))
                rec_gl = max(3, min(int(ml / rec_gs), 10)) if rec_gs > 0 else GRID_MAX_LEVELS
                result["rec_grid_step"]   = rec_gs
                result["rec_grid_levels"] = rec_gl
                result["detail_lines"].append(
                    f"  📐 그리드 추천  간격: {rec_gs}%  층수: {rec_gl}층"
                    f"  (낙폭 하위25%={p25:.2f}%  상위75%={p75:.2f}%)"
                )

        # 매매 성과
        sells = [r for r in trades if r.get("side") == "SELL"]
        if sells:
            pnls = [float(r["pnl_krw"]) for r in sells if r.get("pnl_krw")]
            wins = [v for v in pnls if v > 0]
            win_rate = len(wins)/len(pnls)*100 if pnls else 0
            total = sum(pnls)
            result["win_rate"]  = win_rate
            result["total_pnl"] = total
            result["detail_lines"].append(
                f"  거래 {len(pnls)}회  승률 {win_rate:.0f}%  손익 {total:+,.0f}원"
            )
            timeout_rate = sum(1 for r in sells if "타임아웃" in r.get("reason","")) / len(sells)
            if timeout_rate > 0.4:
                rec_target = round(bot["target"]*0.8, 2)
                result["rec_target"] = rec_target
                result["detail_lines"].append(f"  ⚠️ 타임아웃 {timeout_rate*100:.0f}% → 익절 {rec_target}% 추천")
        return result
    except Exception as e:
        cprint(f"[분석 오류 {days}일] {e}", Fore.YELLOW)
        empty_result["detail_lines"] = [f"  ❌ 오류: {e}"]
        return empty_result


def run_analyze_multi(periods):
    """여러 기간 분석 — 직접 텔레그램 전송 (IPC context 무관)."""
    # 직접 텔레그램으로 전송 (스레드에서 실행되므로)
    def _send_direct(text, keyboard=None):
        try:
            tagged = f"[{_bot_tag()}]\n{text}"
            payload = {"chat_id": CHAT_ID, "text": tagged[:4000]}
            if keyboard:
                payload["reply_markup"] = {"inline_keyboard": keyboard}
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json=payload, timeout=10
            )
        except Exception as e:
            cprint(f"[분석 전송 오류] {e}", Fore.YELLOW)

    label_map = {1: "24h", 7: "7일", 30: "30일"}
    _send_direct("🔍 분석 중...")

    results = []
    for d in periods:
        results.append(_analyze_period(d))

    if not results:
        _send_direct("❌ 분석 결과 없음")
        return

    lines = ["📊 분석 요약", "━━━━━━━━━━━━━━━━━━━━"]
    period_labels = [label_map.get(r["days"], f"{r['days']}일") for r in results]
    lines.append("         " + "   ".join(f"[{l}]" for l in period_labels))
    lines.append("─"*32)
    lines.append(f"현재 RSI  : {bot['rsi_buy']}")
    lines.append(f"현재 익절  : {bot['target']}%")
    lines.append(f"현재 손절  : {bot['max_loss']}%")
    lines.append(f"현재 눌림  : {bot.get('drop',0.5)}%")
    lines.append("─"*32)
    lines.append("추천 RSI  : " + "  /  ".join(str(r["rec_rsi"]) for r in results))
    lines.append("추천 눌림  : " + "  /  ".join(str(r["rec_drop"]) for r in results))
    lines.append("추천 변동  : " + "  /  ".join(
        f"{r['rec_vol_min']}~{r['rec_vol_max']}" for r in results))
    lines.append("추천 그리드: " + "  /  ".join(
        f"간격{r['rec_grid_step']}%/{r['rec_grid_levels']}층" for r in results))
    lines.append("─"*32)
    for r in results:
        lbl = label_map.get(r["days"], f"{r['days']}일")
        if r["win_rate"] is not None:
            lines.append(f"[{lbl}] 거래{r['n_trades']}회  승률{r['win_rate']:.0f}%  {r['total_pnl']:+,.0f}원")
        else:
            lines.append(f"[{lbl}] 거래 없음")

    best = results[0]
    apply_cmd = (f"/apply_recommend {best['rec_rsi']} {best['rec_target']} "
                 f"{best['rec_max_loss']} {best['rec_drop']} "
                 f"{best['rec_vol_min']} {best['rec_vol_max']} {best['rec_timeout']}")
    kb = [
        [{"text": f"✅ 추천값 한번에 적용", "callback_data": apply_cmd}],
        [{"text": f"📋 {label_map.get(r['days'],f'{r["days"]}일')} 상세",
          "callback_data": f"/analyze_detail {r['days']}"}
         for r in results],
    ]
    _send_direct("\n".join(lines), keyboard=kb)


def run_analyze(days=7):
    """단일 기간 상세 분석."""
    def _send_direct(text, keyboard=None):
        try:
            tagged = f"[{_bot_tag()}]\n{text}"
            payload = {"chat_id": CHAT_ID, "text": tagged[:4000]}
            if keyboard:
                payload["reply_markup"] = {"inline_keyboard": keyboard}
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json=payload, timeout=10
            )
        except Exception as e:
            cprint(f"[분석 전송 오류] {e}", Fore.YELLOW)

    r = _analyze_period(days)
    if r["n_rows"] < 10:
        _send_direct(f"❌ [{r['label']}] 데이터 부족 ({r['n_rows']}개)")
        return
    lines = [f"📋 상세 분석 [{r['label']}]", "━━━━━━━━━━━━━━━━━━━━"]
    lines += r["detail_lines"]
    lines += [
        "", "✨ 추천 세팅",
        f"  RSI   : {bot['rsi_buy']} → {r['rec_rsi']}",
        f"  익절  : {bot['target']}% → {r['rec_target']}%",
        f"  손절  : {bot['max_loss']}% → {r['rec_max_loss']}%",
        f"  눌림  : {bot.get('drop',0.5)}% → {r['rec_drop']}%",
        f"  변동성: {VOL_MIN_PCT}~{VOL_MAX_PCT}% → {r['rec_vol_min']}~{r['rec_vol_max']}%",
    ]
    lines += [
        f"  그리드 간격: {GRID_STEP_PCT}% → {r['rec_grid_step']}%",
        f"  그리드 층수: {GRID_MAX_LEVELS}층 → {r['rec_grid_levels']}층",
    ]
    apply_cmd = (f"/apply_recommend {r['rec_rsi']} {r['rec_target']} "
                 f"{r['rec_max_loss']} {r['rec_drop']} "
                 f"{r['rec_vol_min']} {r['rec_vol_max']} {r['rec_timeout']}")
    grid_cmd = f"/set grid_step {r['rec_grid_step']}"
    kb = [
        [{"text": f"✅ [{r['label']}] 추천값 전체 적용", "callback_data": apply_cmd}],
        [{"text": f"🟩 그리드 간격 {r['rec_grid_step']}% 적용",
          "callback_data": grid_cmd},
         {"text": f"🟩 그리드 {r['rec_grid_levels']}층 적용",
          "callback_data": f"/set grid_levels {r['rec_grid_levels']}"}],
    ]
    _send_direct("\n".join(lines), keyboard=kb)


def log_indicator(price, rsi, ma20, ma60, vwap, vol_pct, vol_ratio, drop_pct):
    global _indicator_log_counter
    _indicator_log_counter += 1
    if _indicator_log_counter % INDICATOR_LOG_INTERVAL != 0:
        return
    _, _ilf = _get_log_files()
    write_header = not os.path.exists(_ilf)
    try:
        with open(_ilf, "a", newline="", encoding="utf-8") as f:
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
    # ── 시장 전체 하락 필터 ─────────────────────────────────
    _mkt_down, _mkt_d, _mkt_t, _mkt_names = get_market_trend()
    if _mkt_down:
        send_msg(
            f"🚫 시장 하락으로 매수 차단\n"
            f"{market_trend_msg(_mkt_d, _mkt_t, _mkt_names)}",
            level="normal"
        )
        return False

    if _order_pending:
        cprint("[중복 주문 방지] 이미 주문 진행 중입니다.", Fore.YELLOW)
        return False
    if bot["has_stock"]:
        cprint("[중복 주문 방지] 이미 보유 중입니다.", Fore.YELLOW)
        return False
    if _is_manager_running() and not _request_slot():
        cprint(f"[슬롯] {MARKET_CODE} 슬롯 거절 — 매수 건너뜀", Fore.YELLOW)
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

        # ── 주문은 딱 1회만 전송 ────────────────────────────
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
            save_state()
            _acquire_slot()  # 즉시 상태 저장 (재시작 시 중복 방지)

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

        # 주문했는데 체결 미확인 시 — 업비트 앱 확인 요청
        send_msg(
            f"🚨 매수 주문 체결 미확인! [{reason}]\n"
            f"→ 업비트 앱에서 직접 확인하세요.\n"
            f"→ 체결됐다면 /hold 명령어로 수동 등록하세요.\n"
            f"현재가: {price:,.2f}원",
            level="critical"
        )
        return False
    finally:
        _order_pending = False


# ============================================================
# [GRID] 그리드 트레이딩
# ============================================================
def grid_budget_per_level():
    if GRID_BUDGET_PER > 0:
        return GRID_BUDGET_PER
    return max(5000, ORDER_BUDGET_KRW // GRID_MAX_LEVELS)

def grid_reset():
    global grid_levels, grid_avg_price, grid_total_qty, grid_total_krw
    grid_levels    = []
    grid_avg_price = 0.0
    grid_total_qty = 0.0
    grid_total_krw = 0.0

def grid_next_buy_price():
    if not grid_levels:
        return None
    lowest = min(l["price"] for l in grid_levels)
    return lowest * (1 - GRID_STEP_PCT / 100)

def grid_check_buy(price):
    if not GRID_ENABLED or not bot["has_stock"]:
        return False
    if len(grid_levels) >= GRID_MAX_LEVELS:
        return False
    next_p = grid_next_buy_price()
    if next_p is None:
        return False
    return price <= next_p

def grid_check_sell(price):
    if not GRID_ENABLED:
        return []
    return [lv for lv in grid_levels if price >= lv["target"]]

def grid_do_buy(price, level_idx):
    global _order_pending, grid_levels, grid_avg_price, grid_total_qty, grid_total_krw
    global _buy_time
    if _order_pending:
        return False
    _order_pending = True
    try:
        budget = grid_budget_per_level()
        filled, avg_p = send_order("BUY", MARKET_CODE, budget)
        if filled > 0 and avg_p > 0:
            target_price = avg_p * (1 + bot["target"] / 100)
            grid_levels.append({"level": level_idx, "price": avg_p,
                                 "qty": filled, "target": target_price})
            grid_total_krw += avg_p * filled
            grid_total_qty += filled
            grid_avg_price  = grid_total_krw / grid_total_qty
            if level_idx == 0:
                _buy_time = time.time()
            bot.update({"has_stock": True, "buy_price": grid_avg_price,
                        "filled_qty": grid_total_qty, "be_active": False})
            save_state()
            send_msg(
                f"🟩 그리드 {level_idx+1}층 매수\n"
                f"매수가: {avg_p:,.2f}원  목표: {target_price:,.2f}원\n"
                f"평균가: {grid_avg_price:,.2f}원  층수: {len(grid_levels)}/{GRID_MAX_LEVELS}",
                level="critical"
            )
            return True
        return False
    finally:
        _order_pending = False

def grid_do_sell_level(price, lv):
    global _order_pending, grid_levels, grid_avg_price, grid_total_qty, grid_total_krw
    global daily_pnl_krw, weekly_pnl_krw, trade_count, win_count, loss_count, _last_sell_time
    if _order_pending:
        return False
    _order_pending = True
    try:
        filled, avg_p = send_order("SELL", MARKET_CODE, lv["qty"])
        if filled > 0:
            actual_sell = avg_p if avg_p > 0 else price
            pnl_krw = net_diff_krw(lv["price"], actual_sell, filled)
            daily_pnl_krw  += pnl_krw
            weekly_pnl_krw += pnl_krw
            trade_count    += 1
            if pnl_krw >= 0: win_count += 1
            else: loss_count += 1
            grid_levels    = [l for l in grid_levels if l["level"] != lv["level"]]
            grid_total_qty -= filled
            grid_total_krw -= lv["price"] * filled
            grid_avg_price  = grid_total_krw / grid_total_qty if grid_total_qty > 0 else 0
            _last_sell_time = time.time()
            if grid_total_qty <= 0:
                grid_reset()
                bot.update({"has_stock": False, "filled_qty": 0.0,
                            "buy_price": 0, "be_active": False})
            else:
                bot.update({"buy_price": grid_avg_price, "filled_qty": grid_total_qty})
            save_state()
            send_msg(
                f"✅ 그리드 {lv['level']+1}층 익절\n"
                f"매도가: {actual_sell:,.2f}원  손익: {pnl_krw:+,.0f}원\n"
                f"잔여층: {len(grid_levels)}/{GRID_MAX_LEVELS}  오늘: {daily_pnl_krw:+,.0f}원",
                level="critical"
            )
            return True
        return False
    finally:
        _order_pending = False

def grid_do_sell_all(price, reason="전체 청산"):
    global _order_pending, daily_pnl_krw, weekly_pnl_krw, trade_count
    global win_count, loss_count, _last_sell_time, highest_profit, _buy_time
    if grid_total_qty <= 0:
        return False
    if _order_pending:
        return False
    _order_pending = True
    try:
        filled, avg_p = send_order("SELL", MARKET_CODE, grid_total_qty)
        if filled > 0:
            actual_sell = avg_p if avg_p > 0 else price
            pnl_krw = net_diff_krw(grid_avg_price, actual_sell, filled)
            daily_pnl_krw  += pnl_krw
            weekly_pnl_krw += pnl_krw
            trade_count    += 1
            if pnl_krw >= 0: win_count += 1
            else: loss_count += 1
            _last_sell_time = time.time()
            _buy_time = 0.0
            highest_profit = 0.0
            grid_reset()
            bot.update({"has_stock": False, "filled_qty": 0.0,
                        "buy_price": 0, "be_active": False})
            save_state()
            send_msg(
                f"🔴 그리드 전체 청산 [{reason}]\n"
                f"평균매수: {grid_avg_price:,.2f}원  매도가: {actual_sell:,.2f}원\n"
                f"손익: {pnl_krw:+,.0f}원  오늘: {daily_pnl_krw:+,.0f}원",
                level="critical"
            )
            return True
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
            _release_slot()
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
    """ATR 기반 전략 수치 자동 조정.
    히스테리시스 적용 — 변화가 10% 미만이면 업데이트 안 함 (진동 방지)."""
    if not _dynamic_mode:
        return
    if len(price_history) < 14:
        return
    atr = get_atr(price_history, 14)
    if not atr:
        return
    atr_pct = (atr / price) * 100

    new_target   = round(max(0.6, min(3.0, atr_pct * 1.5)), 2)
    new_max_loss = -round(max(0.5, min(3.0, atr_pct * 2.0)), 2)
    new_drop     = round(max(0.3, min(2.0, atr_pct * 1.0)), 2)

    def _should_update(old, new, threshold=0.1):
        """변화율이 threshold(10%) 이상일 때만 업데이트."""
        if old == 0:
            return True
        return abs(new - old) / abs(old) >= threshold

    if _should_update(bot["target"],   new_target):
        bot["target"]   = new_target
    if _should_update(bot["max_loss"], new_max_loss):
        bot["max_loss"] = new_max_loss
    if _should_update(bot["drop"],     new_drop):
        bot["drop"]     = new_drop

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
    """GitHub 최신 커밋 정보 조회. 실패 시 (None, 오류메시지) 반환."""
    try:
        # main 브랜치 먼저 시도, 없으면 master
        for branch in ("main", "master"):
            res = requests.get(
                f"https://api.github.com/repos/{repo}/commits/{branch}",
                headers={"Authorization": f"token {token}",
                         "Accept": "application/vnd.github.v3+json"},
                timeout=10
            )
            if res.status_code == 200:
                data = res.json()
                return {
                    "hash":    data["sha"][:7],
                    "full":    data["sha"],
                    "message": data["commit"]["message"].split("\n")[0],
                    "time":    data["commit"]["author"]["date"][:16].replace("T", " "),
                    "branch":  branch,
                }, None
            if res.status_code == 404:
                continue   # 브랜치 없으면 다음 시도
            # 그 외 오류 (401, 403 등)
            try:
                err_msg = res.json().get("message", res.text[:100])
            except Exception:
                err_msg = res.text[:100]
            return None, f"HTTP {res.status_code}: {err_msg}"
        return None, "브랜치를 찾을 수 없습니다 (main/master 둘 다 없음)"
    except Exception as e:
        return None, f"네트워크 오류: {e}"


def _github_download(repo, token, filename, ref=None, branch="main"):
    """GitHub에서 파일 다운로드. 실패 시 (None, 오류메시지) 반환."""
    try:
        params = {"ref": ref or branch}
        res = requests.get(
            f"https://api.github.com/repos/{repo}/contents/{filename}",
            headers={"Authorization": f"token {token}",
                     "Accept": "application/vnd.github.v3.raw"},
            params=params, timeout=30
        )
        if res.status_code == 200:
            return res.text, None
        try:
            err_msg = res.json().get("message", res.text[:100])
        except Exception:
            err_msg = res.text[:100]
        return None, f"HTTP {res.status_code}: {err_msg}"
    except Exception as e:
        return None, f"네트워크 오류: {e}"

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

def _get_service_name():
    """현재 프로세스가 systemd 서비스로 실행 중인지 확인 후 서비스명 반환"""
    try:
        import subprocess as _sp
        pid = os.getpid()
        result = _sp.run(
            ["systemctl", "status", str(pid)],
            capture_output=True, text=True, timeout=3
        )
        for line in result.stdout.splitlines():
            if ".service" in line:
                for word in line.split():
                    if word.endswith(".service"):
                        return word.strip("●").strip()
    except:
        pass
    return None

def _restart():
    """systemd 서비스면 그냥 종료 (systemd가 자동 재시작)
    직접 실행 중이면 subprocess로 재시작"""
    import subprocess as _sp, shutil
    svc = _get_service_name()
    if svc:
        # systemd가 Restart=always 로 자동 재시작해줌
        os._exit(0)
    # 직접 실행 중일 때만 subprocess 재시작
    _sp.Popen([sys.executable] + sys.argv)
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
            "  github_repo:  \"아이디/레포명\"",
            level="normal", force=True
        )
        return

    send_msg("🔄 업데이트 확인 중...", level="normal", force=True)
    latest, err = _github_latest(github_repo, github_token)
    if not latest:
        send_msg(
            f"❌ GitHub 연결 실패\n"
            f"원인: {err}\n\n"
            f"확인 사항:\n"
            f"• 토큰이 만료됐거나 권한(repo)이 없는지\n"
            f"• 레포 이름이 정확한지 (대소문자 포함)\n"
            f"• 레포가 존재하는지",
            level="critical", force=True
        )
        return

    current      = _load_local_version()
    current_hash = current["hash"] if current else "없음"
    branch       = latest.get("branch", "main")

    if current and current["hash"] == latest["hash"]:
        send_msg(
            f"✅ 이미 최신 버전입니다\n"
            f"버전: v{BOT_VERSION}  커밋: {latest['hash']}\n"
            f"메시지: {latest['message']}",
            level="normal", force=True
        )
        return

    new_code, err = _github_download(github_repo, github_token, bot_filename, branch=branch)
    if not new_code:
        send_msg(
            f"❌ 파일 다운로드 실패\n"
            f"파일명: {bot_filename}\n"
            f"원인: {err}\n\n"
            f"확인 사항:\n"
            f"• 레포에 '{bot_filename}' 파일이 있는지\n"
            f"• 파일명 대소문자가 정확한지",
            level="critical", force=True
        )
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
        new_code, err = _github_download(github_repo, github_token, bot_filename, ref=target_hash)
        if not new_code:
            send_msg(f"❌ 커밋 {target_hash} 다운로드 실패\n원인: {err}", level="critical", force=True)
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


_last_direct_tg_poll = 0.0
_direct_tg_update_id = 0

# 매니저 감지: shared/manager.pid 파일이 있으면 매니저가 실행 중
_MANAGER_PID_FILE = os.path.join(SHARED_DIR, "manager.pid")

def _manager_is_running() -> bool:
    """매니저가 실행 중인지 확인.
    manager.pid 파일이 있고 해당 PID 프로세스가 살아있으면 True."""
    if not os.path.exists(_MANAGER_PID_FILE):
        return False
    try:
        with open(_MANAGER_PID_FILE) as f:
            pid = int(f.read().strip())
        # /proc/{pid} 존재 여부로 확인 (Linux)
        return os.path.exists(f"/proc/{pid}")
    except Exception:
        return False


_IPC_THREAD_STARTED = False

def _start_ipc_thread():
    """IPC 명령을 0.3초마다 독립적으로 폴링하는 스레드.
    메인 루프(5초)와 무관하게 빠르게 응답."""
    global _IPC_THREAD_STARTED
    if _IPC_THREAD_STARTED:
        return
    _IPC_THREAD_STARTED = True

    def _ipc_loop():
        while True:
            try:
                if _IPC_CMD_FILE and os.path.exists(_IPC_CMD_FILE):
                    tmp = _IPC_CMD_FILE + ".read"
                    try:
                        os.rename(_IPC_CMD_FILE, tmp)
                    except OSError:
                        time.sleep(0.3)
                        continue
                    try:
                        with open(tmp, encoding="utf-8") as f:
                            data = json.load(f)
                        os.remove(tmp)
                        cmd_text = data.get("cmd", "")
                        req_id   = data.get("req_id", "")
                        if cmd_text:
                            cprint(f"[IPC] {cmd_text}", Fore.CYAN)
                            globals()["_IPC_REQ_ID"]      = req_id
                            globals()["_is_ipc_context"]  = True
                            try:
                                handle_command(cmd_text)
                            finally:
                                globals()["_is_ipc_context"] = False
                                globals()["_IPC_REQ_ID"]     = ""
                    except Exception as e:
                        import traceback as _tb
                        tb = "\n".join(_tb.format_exc().split("\n")[-5:])
                        cprint(f"[IPC 처리 오류] {e}\n{tb}", Fore.YELLOW)
                        try:
                            requests.post(
                                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                                json={"chat_id": CHAT_ID,
                                      "text": f"🚨 IPC 오류\n{e}\n{tb}"},
                                timeout=5
                            )
                        except Exception:
                            pass
                        try:
                            os.remove(tmp)
                        except Exception:
                            pass
            except Exception as e:
                cprint(f"[IPC 루프 오류] {e}", Fore.YELLOW)
            time.sleep(0.3)

    import threading as _th
    t = _th.Thread(target=_ipc_loop, daemon=True, name="ipc-poller")
    t.start()
    cprint("✅ IPC 폴링 스레드 시작", Fore.GREEN)


def poll_telegram():
    # [PATCH] 매니저 하위 프로세스(IPC 모드)면 폴링 스킵
    # 매니저가 텔레그램 폴링을 전담하고 IPC로 명령 전달
    if _ap_args.config is not None:
        return
    """단독 실행 시 텔레그램 직접 폴링.
    매니저 실행 중이면 건너뜀 (IPC는 _start_ipc_thread가 담당)."""
    global _last_direct_tg_poll, _direct_tg_update_id

    # 매니저가 살아있으면 텔레그램 직접 폴링 안 함
    if _manager_is_running():
        return

    now = time.time()
    if now - _last_direct_tg_poll < 3:
        return
    _last_direct_tg_poll = now
    if not TELEGRAM_TOKEN or not CHAT_ID:
        return
    try:
        res = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
            params={"offset": _direct_tg_update_id + 1, "timeout": 1},
            timeout=5
        ).json()
        for upd in res.get("result", []):
            _direct_tg_update_id = upd["update_id"]
            msg = upd.get("message", {})
            if str(msg.get("chat", {}).get("id", "")) == str(CHAT_ID):
                text = msg.get("text", "").strip()
                if text:
                    handle_command(text)
            cb = upd.get("callback_query", {})
            if cb and str(cb.get("from", {}).get("id", "")) == str(CHAT_ID):
                # answerCallbackQuery 먼저 — 버튼 로딩 즉시 해제
                try:
                    requests.post(
                        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery",
                        json={"callback_query_id": cb["id"]}, timeout=3
                    )
                except Exception:
                    pass
                data_cb = cb.get("data", "").strip()
                if data_cb:
                    handle_command(data_cb)
    except Exception as e:
        cprint(f"[텔레그램 폴링 오류] {e}", Fore.YELLOW)

def _do_set(key, raw_val):
    """단일 /set 항목 처리 — handle_command 재귀 없이 직접 적용."""
    global MAX_TRADE_COUNT, COOLDOWN_SEC, VOL_MIN_PCT, VOL_MAX_PCT
    global MAX_SLIPPAGE_PCT, POS_TIMEOUT_MIN, VWAP_FILTER, _dynamic_mode
    global GRID_STEP_PCT, GRID_MAX_LEVELS, GRID_BUDGET_PER
    LIMITS = {
        "target":      (0.01,  20.0),
        "max_loss":    (-20.0, -0.01),
        "drop":        (0.001, 10.0),
        "trail_start": (0.01,  20.0),
        "trail_gap":   (0.01,  10.0),
        "be_trigger":  (0.0,   10.0),
        "rsi_buy":     (5.0,   95.0),
        "trade_count": (1,     200),
        "cooldown":    (0,     7200),
        "vol_min":     (0.0,   20.0),
        "vol_max":     (0.1,   50.0),
        "timeout_min": (1,     300),
        "grid_step":   (0.1,   5.0),
        "grid_levels": (2,     20),
        "grid_budget": (0,     1000000),
    }
    try:
        val = float(raw_val)
    except (ValueError, TypeError):
        send_msg(f"❌ {key}: 숫자 아님", level="normal", force=True)
        return
    if key in LIMITS:
        lo, hi = LIMITS[key]
        if not (lo <= val <= hi):
            send_msg(f"❌ {key} 범위 초과 ({lo}~{hi})", level="normal", force=True)
            return
    if key == "trade_count":
        MAX_TRADE_COUNT = int(val)
        send_msg(f"✅ trade_count = {MAX_TRADE_COUNT}", level="normal", force=True)
    elif key == "cooldown":
        COOLDOWN_SEC = int(val)
        send_msg(f"✅ cooldown = {COOLDOWN_SEC}s", level="normal", force=True)
    elif key == "vol_min":
        VOL_MIN_PCT = val
        send_msg(f"✅ vol_min = {val}%", level="normal", force=True)
    elif key == "vol_max":
        VOL_MAX_PCT = val
        send_msg(f"✅ vol_max = {val}%", level="normal", force=True)
    elif key == "slippage":
        MAX_SLIPPAGE_PCT = val
        send_msg(f"✅ slippage = {val}%", level="normal", force=True)
    elif key == "timeout_min":
        POS_TIMEOUT_MIN = int(val)
        send_msg(f"✅ timeout_min = {int(val)}분", level="normal", force=True)
    elif key == "vwap_filter":
        VWAP_FILTER = bool(int(val))
        send_msg(f"✅ vwap_filter = {VWAP_FILTER}", level="normal", force=True)
    elif key == "grid_step":
        GRID_STEP_PCT = val
        send_msg(f"✅ grid_step = {val}%", level="normal", force=True)
    elif key == "grid_levels":
        GRID_MAX_LEVELS = int(val)
        send_msg(f"✅ grid_levels = {int(val)}층", level="normal", force=True)
    elif key == "grid_budget":
        GRID_BUDGET_PER = int(val)
        send_msg(f"✅ grid_budget = {int(val)}원", level="normal", force=True)
    elif key in SET_ALLOWED_KEYS:
        bot[key] = val
        if key in ("target","max_loss","drop","trail_start","trail_gap","be_trigger"):
            _dynamic_mode = False
        send_msg(f"✅ {key} = {val}", level="normal", force=True)
    else:
        send_msg(f"❌ 변경 불가 항목: {key}", level="normal", force=True)
        return
    log_change("설정변경", f"{key} = {raw_val}")
    save_state()


def _do_set_silent(key, raw_val, results):
    """다중 set용 — send_msg 없이 적용 후 results 리스트에 추가."""
    global MAX_TRADE_COUNT, COOLDOWN_SEC, VOL_MIN_PCT, VOL_MAX_PCT
    global MAX_SLIPPAGE_PCT, POS_TIMEOUT_MIN, VWAP_FILTER, _dynamic_mode
    global GRID_STEP_PCT, GRID_MAX_LEVELS, GRID_BUDGET_PER
    LIMITS = {
        "target":      (0.01,  20.0),
        "max_loss":    (-20.0, -0.01),
        "drop":        (0.001, 10.0),
        "trail_start": (0.01,  20.0),
        "trail_gap":   (0.01,  10.0),
        "be_trigger":  (0.0,   10.0),
        "rsi_buy":     (5.0,   95.0),
        "trade_count": (1,     200),
        "cooldown":    (0,     7200),
        "vol_min":     (0.0,   20.0),
        "vol_max":     (0.1,   50.0),
        "timeout_min": (1,     300),
        "grid_step":   (0.1,   5.0),
        "grid_levels": (2,     20),
        "grid_budget": (0,     1000000),
    }
    try:
        val = float(raw_val)
    except (ValueError, TypeError):
        results.append(f"❌ {key}: 숫자 아님")
        return
    if key in LIMITS:
        lo, hi = LIMITS[key]
        if not (lo <= val <= hi):
            results.append(f"❌ {key} 범위초과 ({lo}~{hi})")
            return
    if key == "trade_count":
        MAX_TRADE_COUNT = int(val)
    elif key == "cooldown":
        COOLDOWN_SEC = int(val)
    elif key == "vol_min":
        VOL_MIN_PCT = val
    elif key == "vol_max":
        VOL_MAX_PCT = val
    elif key == "slippage":
        MAX_SLIPPAGE_PCT = val
    elif key == "timeout_min":
        POS_TIMEOUT_MIN = int(val)
    elif key == "vwap_filter":
        VWAP_FILTER = bool(int(val))
    elif key == "grid_step":
        GRID_STEP_PCT = val
    elif key == "grid_levels":
        GRID_MAX_LEVELS = int(val)
    elif key == "grid_budget":
        GRID_BUDGET_PER = int(val)
    elif key in SET_ALLOWED_KEYS:
        bot[key] = val
        if key in ("target","max_loss","drop","trail_start","trail_gap","be_trigger"):
            _dynamic_mode = False
    else:
        results.append(f"❌ {key}: 변경불가")
        return
    results.append(f"{key}={raw_val}")
    log_change("설정변경", f"{key} = {raw_val}")


def handle_command(text):
    global _verify_mode, _verify_orig
    global VOL_MIN_PCT, VOL_MAX_PCT, MARKET_CODE
    global _weekly_stop, _dynamic_mode, _aggressive_mode, _buy_time
    global DAILY_LOSS_BASE_KRW, ORDER_BUDGET_KRW, MARKET_CODE
    global MAX_TRADE_COUNT, COOLDOWN_SEC, VOL_MIN_PCT, VOL_MAX_PCT
    global MAX_SLIPPAGE_PCT, POS_TIMEOUT_MIN, VWAP_FILTER
    global _vwap_sum_pv, _vwap_sum_v, _vwap_value, _real_data_count, _last_price
    global price_history, volume_history, timed_prices, _price_trough_5m
    global GRID_ENABLED

    if not text:
        return

    # Reply Keyboard 버튼 텍스트 → 명령어 변환
    text = REPLY_CMD_MAP.get(text.strip(), text)

    cmd = text.strip().split()

    # 한글 별칭 정규화
    _alias = {
        "/분석": "/analyze", "/로그분석": "/analyze",
        "/왜": "/why", "/왜안사": "/why", "/왜안팔아": "/why sell",
        "/공격적": "/aggressive", "/공격모드": "/aggressive",
        "/일반": "/normal", "/일반모드": "/normal",
        "/시작": "/start", "/정지": "/stop", "/멈춰": "/stop",
        "/매도": "/sell", "/즉시매도": "/sell",
        "/매수": "/buy",
        "/잔고": "/balance",
        "/상태": "/status",
        "/도움": "/help",
        "/종목변경": "/switch", "/전환": "/switch",
        # coin 명시 별칭 (매니저 없이 직접 실행 시)
        "/coin": "/status", "/coin status": "/status",
        "/coin start": "/start", "/coin stop": "/stop",
        "/coin buy": "/buy", "/coin sell": "/sell",
        "/coin why": "/why", "/coin balance": "/balance",
        "/coin aggressive": "/aggressive", "/coin normal": "/normal",
    }
    if cmd[0] in _alias:
        cmd[0] = _alias[cmd[0]]

    # ── 단축 명령어 변환 (/rsi 45 → /set rsi_buy 45 등) ─────
    _shortcuts = {
        "/rsi":     "rsi_buy",
        "/tp":      "target",
        "/sl":      "max_loss",
        "/drop":    "drop",
        "/trail":   "trail_start",
        "/gap":     "trail_gap",
        "/be":      "be_trigger",
    }
    if cmd[0] in _shortcuts and len(cmd) == 2:
        cmd = ["/set", _shortcuts[cmd[0]], cmd[1]]

    # 사용 빈도 추적 (시스템 내부 명령 제외)
    if cmd[0].startswith("/") and cmd[0] not in ("/menu_cat",):
        track_cmd(cmd[0])

    # ── 카테고리 메뉴 ─────────────────────────────────────────
    if cmd[0] == "/menu_cat":
        cat = cmd[1] if len(cmd) > 1 else None
        kb  = _build_menu_keyboard(cat)
        send_msg(
            f"{MENU_CATEGORIES[cat]['emoji']} {cat} 메뉴" if cat else "📋 메뉴",
            level="normal", keyboard=kb, force=True
        )
        return

    # ── 시작 / 정지 (토글) ───────────────────────────────────
    if cmd[0] in ("/start", "/켜줘", "/stop", "/멈춰"):
        if cmd[0] in ("/stop", "/멈춰"):
            want_run = False
        else:
            want_run = not bot["running"]   # 토글

        bot["running"] = want_run
        save_state()

        if want_run:
            bot["rsi_buy"] = RSI_BUY_DEFAULT
            msg = (
                f"▶️ 매매 시작!\n"
                f"종목: {MARKET_CODE}\n"
                f"예산: {ORDER_BUDGET_KRW:,.0f}원\n"
                f"RSI: {bot['rsi_buy']} / 익절: {bot['target']}% / 손절: {bot['max_loss']}%"
            )
            send_msg(msg, level="critical", keyboard=KB_MAIN, force=True)
            _write_result("[critical] " + msg)
        else:
            msg = "⏹️ 매매 정지. 버튼을 다시 누르면 시작해요."
            send_msg(msg, level="critical", force=True)
            _write_result("[critical] " + msg)

    # ── 일시정지 (/pause N분 후 자동 재개) ──────────────────
    elif cmd[0] in ("/pause", "/잠깐", "/일시정지"):
        if len(cmd) < 2:
            send_msg(
                "⏸️ 일시정지 방법\n/pause 분수\n\n예) /pause 30 → 30분 후 자동 재개\n예) /pause 0  → 수동 정지",
                level="normal"
            )
        else:
            try:
                minutes = int(cmd[1])
                bot["running"] = False
                if minutes > 0:
                    import threading as _th
                    def _auto_resume():
                        time.sleep(minutes * 60)
                        if not bot["running"]:
                            bot["running"] = True
                            send_msg(f"▶️ {minutes}분 경과! 자동으로 매매를 재개해요.", level="normal", force=True)
                    _th.Thread(target=_auto_resume, daemon=True).start()
                    send_msg(
                        f"⏸️ {minutes}분간 일시정지해요.\n→ {minutes}분 후 자동으로 재개돼요.\n→ 지금 바로 재개하려면 버튼을 누르세요.",
                        level="normal",
                        keyboard=[[{"text": "⏯ 지금 재개", "callback_data": "/start"}]],
                        force=True
                    )
                else:
                    send_msg("⏸️ 수동 정지. /start 로 재개하세요.",
                             level="normal", force=True)
            except ValueError:
                send_msg("❌ 숫자로 입력하세요. 예) /pause 30", level="normal")

    # ── 수동 포지션 등록 (/hold 가격 수량) ───────────────────
    elif cmd[0] == "/hold":
        if len(cmd) < 3:
            send_msg(
                "사용법: /hold 매수가격 수량\n"
                "예) /hold 135000000 0.00007407\n"
                "→ 체결 미확인 시 수동으로 포지션 등록",
                level="normal", force=True
            )
        else:
            try:
                buy_p = float(cmd[1].replace(",", ""))
                qty   = float(cmd[2])
                bot.update({
                    "has_stock":  True,
                    "buy_price":  buy_p,
                    "filled_qty": qty,
                    "be_active":  False,
                })
                global _buy_time
                _buy_time = time.time()
                save_state()
                send_msg(
                    f"✅ 포지션 수동 등록\n"
                    f"종목  : {MARKET_CODE}\n"
                    f"매수가: {buy_p:,.2f}원\n"
                    f"수량  : {qty:.8f}",
                    level="normal", force=True
                )
            except:
                send_msg("❌ 입력 오류. 예) /hold 135000000 0.00007407", level="normal", force=True)

    # ── 즉시 매도 (Kill Switch) ───────────────────────────────
    elif cmd[0] in ("/clear", "/초기화", "/sync"):
        bot.update({"has_stock": False, "buy_price": 0, "filled_qty": 0.0, "be_active": False})
        _buy_time = 0
        save_state()
        send_msg("✅ 포지션 초기화 완료", level="critical", force=True)

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

    # ── 수동 매수 ─────────────────────────────────────────────
    elif cmd[0] in ("/buy", "/매수"):
        if bot["has_stock"]:
            send_msg("⚠️ 이미 코인을 보유 중입니다.\n→ 매도 후 다시 시도하세요.", level="normal", force=True)
            return
        if _weekly_stop:
            send_msg("⛔ 주간 손실 한도 초과로 매수 불가합니다.", level="normal", force=True)
            return
        if is_daily_loss_exceeded():
            send_msg("⛔ 일일 손실 한도 초과로 매수 불가합니다.", level="normal", force=True)
            return
        # 예산 지정 가능: /buy 50000 → 5만원어치
        custom_budget = None
        if len(cmd) > 1:
            try:
                custom_budget = int(cmd[1].replace(",", ""))
                if custom_budget < 5000:
                    send_msg("❌ 최소 5,000원 이상 입력하세요.", level="normal", force=True)
                    return
            except:
                send_msg("❌ 숫자로 입력하세요. 예) /buy  또는  /buy 50000", level="normal", force=True)
                return
        price = get_price()
        if not price:
            send_msg("❌ 현재가 조회 실패. 잠시 후 다시 시도하세요.", level="critical", force=True)
            return
        # 예산 임시 교체
        original_budget = ORDER_BUDGET_KRW
        if custom_budget:
            ORDER_BUDGET_KRW = custom_budget
        send_msg(
            f"🛒 수동 매수 실행 중...\n"
            f"종목: {MARKET_CODE}\n"
            f"현재가: {price:,.2f}원\n"
            f"예산: {ORDER_BUDGET_KRW:,.0f}원",
            level="critical", force=True
        )
        _mkt_down2, _mkt_d2, _mkt_t2, _mkt_names2 = get_market_trend()
        if _mkt_down2:
            send_msg(
                "⚠️ 시장 하락 경고! 수동매수를 진행하지만 주의하세요.\n"
                f"{market_trend_msg(_mkt_d2, _mkt_t2, _mkt_names2)}",
                level="critical", force=True
            )
        _mkt_down2, _mkt_d2, _mkt_t2, _mkt_names2 = get_market_trend()
        if _mkt_down2:
            send_msg(
                "⚠️ 시장 하락 경고! 수동매수를 진행하지만 주의하세요.\n"
                + market_trend_msg(_mkt_d2, _mkt_t2, _mkt_names2),
                level="critical", force=True
            )
        result = do_buy(price, "수동 매수")
        if custom_budget:
            ORDER_BUDGET_KRW = original_budget  # 원래 예산 복원

    # ── 상태 조회 ─────────────────────────────────────────────
    elif cmd[0] in ("/status", "/s", "/상태"):
        price = get_price() or _last_price
        rsi   = bot.get("_last_rsi")
        ma20  = bot.get("_ma20")
        ma60  = bot.get("_ma60")
        vol   = calc_vol_pct(timed_prices)
        chk   = lambda ok: "✅" if ok else "❌"

        lines = [
            f"📊 [{MARKET_CODE}] 상태  {'▶️ 실행중 (누르면 정지)' if bot['running'] else '⏹️ 정지 (누르면 시작)'}  "
            f"{'⚡공격적' if _aggressive_mode else '🛡️일반'}",
            f"━━━━━━━━━━━━━━━━━━━━",
        ]

        # ── 보유 중 ──────────────────────────────────────────
        if bot["has_stock"] and price and bot["buy_price"] > 0:
            pnl     = net_diff_krw(bot["buy_price"], price, bot["filled_qty"])
            pnl_pct = (price - bot["buy_price"]) / bot["buy_price"] * 100
            hold_min = int((time.time() - _buy_time) / 60) if _buy_time else 0
            target_p = bot["buy_price"] * (1 + bot["target"] / 100)
            stop_p   = bot["buy_price"] * (1 + bot["max_loss"] / 100)
            lines += [
                f"💰 보유중  {bot['filled_qty']:.6f} 개",
                f"매수가 : {bot['buy_price']:,.2f}원  ({hold_min}분 전)",
                f"현재가 : {price:,.2f}원  ({pnl_pct:+.2f}%)",
                f"평가손익: {pnl:+,.0f}원",
                f"목표가 : {target_p:,.2f}원  |  손절가: {stop_p:,.2f}원",
                f"─────────────────",
            ]
        else:
            # ── 대기 중: 매수 조건 표시 ──────────────────────
            drop_now = ((ma20 - price) / ma20 * 100) if ma20 and price else 0
            rsi_ok   = rsi is not None and rsi <= bot["rsi_buy"]
            ma_ok    = bool(ma20 and ma60 and ma20 > ma60)
            drop_ok  = drop_now >= bot["drop"]
            vol_ok   = vol is not None and VOL_MIN_PCT <= vol <= VOL_MAX_PCT
            vwap_ok  = (not VWAP_FILTER) or (not _vwap_value) or (price <= _vwap_value)
            lines += [
                f"⏳ 매수 대기중",
                f"─────────────────",
                f"현재가 : {price:,.2f}원",
                f"{chk(rsi_ok)} RSI      : {(f'{rsi:.1f}') if rsi is not None else 'N/A'} (기준 {bot['rsi_buy']} 이하)",
                f"{chk(ma_ok)} 상승추세 : MA20={(f'{ma20:,.0f}') if ma20 else 'N/A'} > MA60={(f'{ma60:,.0f}') if ma60 else 'N/A'}",
                f"{chk(drop_ok)} 눌림     : {drop_now:.2f}% (기준 {bot['drop']}% 이상)",
                f"{chk(vol_ok)} 변동성   : {(f'{vol:.2f}') if vol else 'N/A'}% (기준 {VOL_MIN_PCT}~{VOL_MAX_PCT}%)",
                f"{chk(vwap_ok)} VWAP    : {(f'{_vwap_value:,.0f}') if _vwap_value else 'N/A'}원",
                f"─────────────────",
            ]

        # ── 내 설정 수치 ─────────────────────────────────────
        lines += [
            f"⚙️ 현재 설정",
            f"RSI {bot['rsi_buy']}  익절 {bot['target']}%  손절 {bot['max_loss']}%",
            f"눌림 {bot['drop']}%  트레일 {bot['trail_start']}%/{bot['trail_gap']}%",
            f"변동성 {VOL_MIN_PCT}~{VOL_MAX_PCT}%  타임아웃 {POS_TIMEOUT_MIN}분",
            f"그리드 {'ON' if GRID_ENABLED else 'OFF'}"
            + (f" {len(grid_levels)}층/{GRID_MAX_LEVELS}" if GRID_ENABLED else ""),
            f"─────────────────",
            f"오늘 {daily_pnl_krw:+,.0f}원  주간 {weekly_pnl_krw:+,.0f}원",
            f"거래 {trade_count}회  승{win_count}/패{loss_count}",
            f"─────────────────",
            market_trend_msg(*get_market_trend()[1:]),
        ]

        send_msg("\n".join(lines), level="normal", force=True)

    # ── 잔고 조회 ─────────────────────────────────────────────
    elif cmd[0] in ("/balance", "/b", "/잔고", "/balance coin", "/코인잔고"):
        krw  = get_balance_krw()
        coin = get_coin_balance()
        send_msg(
            f"💰 잔고\n"
            f"원화  : {krw:,.0f}원\n"
            f"코인  : {coin:.8f} {MARKET_CODE.replace('KRW-','')}",
            level="normal", force=True
        )

    # ── /analyze : 로그 분석 및 파라미터 추천 ──────────────
    elif cmd[0] == "/analyze":
        days = int(cmd[1]) if len(cmd) > 1 and cmd[1].isdigit() else 7
        send_msg(f"🔍 최근 {days}일 로그 분석 중...", level="normal", force=True)
        run_analyze(days)

    # ── /why : 매수/매도 미발생 이유 ────────────────────────
    elif cmd[0] in ("/train_status", "/train", "/기차"):
        _ma5_s = calc_ma5(price_history)
        _vr_s = locals().get("vol_ratio")
        _it, _tc, _td = detect_train(bot.get('_last_rsi'), _ma5_s, bot.get('_ma20'), bot.get('_ma60'), price_history, _vr_s)
        mode_str = "🚂 기차ON" if _train_mode else "🛤 역추세 대기"
        msg = mode_str + " 조건 %d/5\n" % _tc
        msg += "\n".join(_td) + "\n"
        msg += "RSI %s~%s / 모멘텀 +%s%% / 거래량 %s배\n" % (
            bot.get("train_rsi_min",50), bot.get("train_rsi_max",65),
            bot.get("train_momentum",1.5), bot.get("train_vol_ratio",1.5))
        msg += "익절 x%s / 손절 -%s%%" % (bot.get("train_target",1.5), bot.get("train_stop",0.8))
        send_msg(msg, level="normal", force=True,
            keyboard=[[{"text": "🔍 분석", "callback_data": "/analyze 7"},
                       {"text": "📊 상태", "callback_data": "/status"}]])

    elif cmd[0] == "/why":
        sub = cmd[1].lower() if len(cmd) > 1 else ("sell" if bot["has_stock"] else "buy")

        if sub == "sell":
            if not bot["has_stock"]:
                send_msg("ℹ️ 현재 보유 중인 코인이 없습니다.", level="normal", force=True)
                return
            price  = _last_price
            bp     = bot["buy_price"]
            pnl_p  = (price - bp) / bp * 100 if bp > 0 else 0
            hp     = highest_profit
            hold_m = int((time.time() - _buy_time) / 60) if _buy_time else 0
            timeout_left = max(0, POS_TIMEOUT_MIN - hold_m)
            chk = lambda ok: "✅" if ok else "❌"
            sell_lines = [
                f"{chk(pnl_p >= bot['target'])} 익절 미도달  현재 {pnl_p:+.2f}% / 목표 +{bot['target']}%",
                f"{chk(hp >= bot['trail_start'] and (hp-pnl_p) >= bot['trail_gap'])} 트레일링 미발동  최고 {hp:+.2f}% / 발동 +{bot['trail_start']}%  간격 {bot['trail_gap']}%",
                f"{chk(bot['be_active'] and pnl_p < 0)} 본절 보호  {'활성' if bot['be_active'] else '비활성'} (최고 {hp:+.2f}% / 트리거 +{bot['be_trigger']}%)",
                f"{chk(pnl_p <= bot['max_loss'])} 손절 미도달  현재 {pnl_p:+.2f}% / 손절 {bot['max_loss']}%",
                f"{chk(timeout_left == 0)} 타임아웃  {timeout_left}분 남음 ({POS_TIMEOUT_MIN}분 설정)",
            ]
            msg = (
                f"🔍 왜 안 팔아?\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"현재가: {price:,.0f}원  매수가: {bp:,.0f}원\n"
                f"평가손익: {pnl_p:+.2f}%  보유: {hold_m}분\n"
                f"─────────────────\n" +
                "\n".join(sell_lines)
            )
            send_msg(msg, level="normal", force=True)
            _write_result("[normal] " + msg)

        else:  # buy
            price = _last_price
            rsi   = bot.get("_last_rsi")
            ma20  = bot.get("_ma20")
            ma60  = bot.get("_ma60")
            vol   = calc_vol_pct(timed_prices)
            chk   = lambda ok: "✅" if ok else "❌"

            # ── 진입 불가 사전 차단 조건 ──────────────────────
            reasons = []
            if not bot["running"]:             reasons.append("❌ 봇 정지 상태 (버튼을 눌러 시작)")
            if bot["has_stock"]:               reasons.append("❌ 이미 보유 중")
            if _weekly_stop:                   reasons.append("❌ 주간 손실 한도 초과")
            if is_daily_loss_exceeded():       reasons.append("❌ 일일 손실 한도 초과")
            if trade_count >= MAX_TRADE_COUNT: reasons.append(f"❌ 최대 거래 횟수 ({trade_count}/{MAX_TRADE_COUNT})")
            cooldown_left = COOLDOWN_SEC - (time.time() - _last_sell_time)
            if cooldown_left > 0:              reasons.append(f"❌ 쿨다운 {int(cooldown_left)}초 남음")
            if _real_data_count < REAL_DATA_MIN: reasons.append(f"❌ 데이터 수집 중 ({_real_data_count}/{REAL_DATA_MIN})")

            # ── 매수 신호 조건 상세 ───────────────────────────
            rsi_str  = f"{rsi:.1f}" if rsi is not None else "N/A"
            rsi_ok   = rsi is not None and rsi <= bot["rsi_buy"]
            ma_ok    = bool(ma20 and ma60 and ma20 > ma60)
            drop_now = ((ma20 - price) / ma20 * 100) if ma20 and price else None
            drop_ok  = drop_now is not None and drop_now >= bot["drop"]
            vol_ok   = vol is not None and VOL_MIN_PCT <= vol <= VOL_MAX_PCT
            vwap_ok  = (not VWAP_FILTER) or (not _vwap_value) or (price <= _vwap_value)
            div_ok        = check_5m_divergence()
            divergence_ok = div_ok
            _vr_why       = (volume_history[-1] / (sum(volume_history) / len(volume_history))) if len(volume_history) >= 10 and sum(volume_history) > 0 else None
            volr_ok       = (_vr_why is None) or (_vr_why >= VOL_RATIO_MIN)

            # 공격모드에서 무시되는 조건 목록
            _aggressive_skip = {"ma_ok", "div_ok", "vwap_ok", "volr_ok"} if _aggressive_mode else set()
            signal_lines = []
            if "rsi_ok" not in _aggressive_skip:
                signal_lines.append(f"{chk(rsi_ok)} RSI      : {rsi_str} (기준 {bot['rsi_buy']} 이하)")
            if "ma_ok" not in _aggressive_skip:
                signal_lines.append(f"{chk(ma_ok)} 상승추세 : MA20={(f'{ma20:,.0f}') if ma20 else 'N/A'} > MA60={(f'{ma60:,.0f}') if ma60 else 'N/A'}")
            if "drop_ok" not in _aggressive_skip:
                signal_lines.append(f"{chk(drop_ok)} 눌림     : {f'{drop_now:.2f}' if drop_now is not None else 'N/A'}% (기준 {bot['drop']}% 이상)")
            if "vol_ok" not in _aggressive_skip:
                signal_lines.append(f"{chk(vol_ok)} 변동성   : {f'{vol:.2f}' if vol is not None else 'N/A'}% (기준 {VOL_MIN_PCT}~{VOL_MAX_PCT}%)")
            if "vwap_ok" not in _aggressive_skip:
                signal_lines.append(f"{chk(vwap_ok)} VWAP    : {(f'{_vwap_value:,.0f}') if _vwap_value else 'N/A'}원")
            if "div_ok" not in _aggressive_skip:
                signal_lines.append(f"{chk(div_ok)} 다이버전스: {'확인됨' if div_ok else '아직 없음'}")

            mode_str   = "공격적 모드 (RSI V-Turn + 눌림만)" if _aggressive_mode else "일반 모드 (전체 조건 필요)"
            _mkt_down_w, _mkt_d_w, _mkt_t_w, _mkt_names_w = get_market_trend()
            _mkt_icon = "❌" if _mkt_down_w else "✅"
            reasons.insert(0, _mkt_icon + " 시장동향  " + market_trend_msg(_mkt_d_w, _mkt_t_w, _mkt_names_w))
            pre_block  = "\n".join(reasons) if reasons else "✅ 시스템 운용 정상"
            cond_block = "\n".join(signal_lines)
            final_note = "⏳ 매수 신호 대기 중 (위 조건 충족 시 진입)" if not reasons else ""

            # 공격모드에서 무시된 조건 표시
            skipped_note = ""
            if _aggressive_mode:
                skipped = []
                if not locals().get("divergence_ok", False): skipped.append("다이버전스")
                if not locals().get("ma_ok", False): skipped.append("MA방향")
                if not locals().get("vwap_ok", True): skipped.append("VWAP")
                if not locals().get("volr_ok", True): skipped.append("거래량비율")
                if skipped:
                    skipped_note = f"\n─────────────────\n⚡ 공격모드 무시 조건: {', '.join(skipped)}"

            msg = (
                f"🔍 왜 안 사?\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"현재가: {price:,.0f}원  RSI: {rsi_str}\n"
                f"모드: {mode_str}\n"
                f"─────────────────\n"
                f"[ 대기 조건 ]\n{pre_block}\n"
                f"─────────────────\n"
                f"[ 매수 신호 조건 ]\n{cond_block}"
                + skipped_note
                + (f"\n─────────────────\n{final_note}" if final_note else "")
            )
            send_msg(msg, level="normal", force=True)
            _write_result("[normal] " + msg)


    # ── /test : 테스트 모드 (수치 대폭 낮춰 1회 매매 검증) ──

    elif cmd[0] in ("/verify", "/검증", "/verify_off", "/검증종료"):
        if "/verify_off" in cmd[0] or "/검증종료" in cmd[0]:
            if _verify_mode and _verify_orig:
                _verify_mode = False
                _orig = dict(_verify_orig)
                _verify_orig = {}
                for _k, _v in _orig.items():
                    if _v is not None:
                        handle_command(f"/set {_k} {_v}")
                send_msg("⏹ 검증 모드 종료. 원래 수치로 복원됐어요.", level="normal", force=True)
            else:
                send_msg("ℹ️ 검증 모드가 켜져 있지 않아요.", level="normal", force=True)
            return

        if bot["has_stock"]:
            send_msg("⚠️ 이미 보유 중이에요. 매도 후 시도하세요.", level="normal", force=True)
            return
        # 현재 수치 저장
        _verify_backup = {
            "rsi_buy":    bot.get("rsi_buy"),
            "target":     bot.get("target"),
            "max_loss":   bot.get("max_loss"),
            "drop":       bot.get("drop"),
            "trail_start":bot.get("trail_start"),
        }
        # 수치 극도로 낮추기
        bot["rsi_buy"]    = 99
        bot["target"]     = 0.05
        bot["max_loss"]   = -0.1
        bot["drop"]       = 0.001
        bot["trail_start"]= 0.03
        VOL_MIN_PCT = 0.0
        VOL_MAX_PCT = 99.0
        _verify_mode = True
        _verify_orig = _verify_backup
        send_msg(
            "🧪 검증 모드 ON\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "모든 조건 무시 → 즉시 매수 대기\n"
            "익절 0.05% / 손절 -0.1%\n"
            "청산 후 자동 원복",
            level="critical", force=True
        )

    elif cmd[0] in ("/test", "/테스트"):
        global _test_mode
        if len(cmd) >= 2 and cmd[1].lower() in ("on", "켜", "시작"):
            _enter_test_mode()
            send_msg(
                "🧪 테스트 모드 ON\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "RSI  : 70 이하 (거의 항상 진입)\n"
                "익절 : 0.05% / 손절 : -0.1%\n"
                "눌림 : 0.01%\n"
                "⚠️ 실제 자금으로 소액 매매됩니다!\n"
                "→ 해제: /test off",
                level="critical", force=True
            )
        elif len(cmd) >= 2 and cmd[1].lower() in ("off", "끄기", "종료"):
            _exit_test_mode()
            send_msg(
                "🧪 테스트 모드 OFF — 원래 수치 복원됨",
                level="normal", force=True
            )
        else:
            mode_str = "🟡 ON" if _test_mode else "⚫ OFF"
            send_msg(
                f"🧪 테스트 모드: {mode_str}\n"
                "/test on  → 수치 낮춰 즉시 매매 테스트\n"
                "/test off → 원래 수치 복원",
                level="normal", force=True
            )
    # ── /aggressive : 공격적 매수 모드 ───────────────────────
    elif cmd[0] == "/aggressive":
        _aggressive_mode = True
        send_msg(
            "⚡ 공격적 모드 ON\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "✅ 유지: RSI V-Turn + 눌림\n"
            "❌ 제외: 다이버전스 / MA방향 / VWAP / 거래량\n"
            "→ 원래대로: /normal",
            level="normal", force=True
        )

    # ── /normal : 일반 매수 모드 복귀 ────────────────────────
    elif cmd[0] == "/normal":
        _aggressive_mode = False
        send_msg(
            "🛡️ 일반 모드 복귀\n"
            "전체 조건 통과 시에만 매수합니다.",
            level="normal", force=True
        )

    # ── 설정 변경 ─────────────────────────────────────────────
    elif cmd[0] == "/grid":
        sub = cmd[1].lower() if len(cmd) > 1 else ""
        if sub == "on":
            GRID_ENABLED = True
            send_msg(
                f"🟩 그리드 ON\n간격:{GRID_STEP_PCT}%  층:{GRID_MAX_LEVELS}  층당:{grid_budget_per_level():,}원",
                level="normal", force=True
            )
        elif sub == "off":
            GRID_ENABLED = False
            send_msg("⬜ 그리드 OFF → 일반 매매 모드", level="normal", force=True)
        elif sub == "status":
            if not grid_levels:
                send_msg(
                    f"🟩 그리드 {'ON' if GRID_ENABLED else 'OFF'}\n"
                    f"간격:{GRID_STEP_PCT}%  층:{GRID_MAX_LEVELS}  포지션 없음",
                    level="normal", force=True
                )
            else:
                lines = [f"🟩 그리드 {'ON' if GRID_ENABLED else 'OFF'}",
                         f"평균매수가: {grid_avg_price:,.2f}원",
                         f"총수량: {grid_total_qty:.8f}  총투자: {grid_total_krw:,.0f}원",
                         "─────────────────"]
                for lv in grid_levels:
                    lines.append(f"  {lv['level']+1}층: {lv['price']:,.2f}원 → 목표 {lv['target']:,.2f}원")
                send_msg("\n".join(lines), level="normal", force=True)
        elif sub == "sell":
            p = get_price() or _last_price
            if grid_total_qty > 0:
                grid_do_sell_all(p, "수동 청산")
            else:
                send_msg("ℹ️ 보유 중인 그리드 포지션 없음", level="normal", force=True)
        else:
            send_msg(
                "🟩 그리드 명령어\n"
                "/grid on/off/status/sell\n"
                "/set grid_step 0.05\n"
                "/set grid_levels 6\n"
                "/set grid_budget 5000",
                level="normal", force=True
            )

    elif cmd[0] == "/settings":
        send_msg(
            f"⚙️ 현재 설정\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"RSI    : {bot['rsi_buy']}\n"
            f"익절   : {bot['target']}%\n"
            f"손절   : {bot['max_loss']}%\n"
            f"눌림   : {bot['drop']}%\n"
            f"그리드 : {'ON' if GRID_ENABLED else 'OFF'} "
            f"(간격:{GRID_STEP_PCT}% 층:{GRID_MAX_LEVELS})\n"
            f"─────────────────\n"
            f"버튼으로 변경하세요 👇",
            level="normal", force=True, keyboard=KB_SETTINGS
        )

    elif cmd[0] == "/apply_recommend":
        keys = ["rsi_buy","target","max_loss","drop","vol_min","vol_max","timeout_min","grid_step","grid_levels"]
        applied = []
        for i, k in enumerate(keys):
            if i + 1 < len(cmd):
                try:
                    handle_command(f"/set {k} {cmd[i+1]}")
                    applied.append(f"{k}={cmd[i+1]}")
                except Exception:
                    pass
        send_msg("✅ 추천값 적용!\n" + "  ".join(applied) if applied else "⚠️ 적용 실패",
                 level="critical", force=True)

    elif cmd[0] == "/set":
        SET_KR_ALIAS = {
            "익절": "target", "손절": "max_loss", "눌림": "drop",
            "rsi": "rsi_buy", "RSI": "rsi_buy",
        }
        # 다중 set: /set k1 v1 k2 v2 ...
        if len(cmd) > 3 and len(cmd) % 2 == 1:
            pairs = [(cmd[i], cmd[i+1]) for i in range(1, len(cmd), 2)]
            results = []
            for rk, rv in pairs:
                _rk = rk.lower()
                _k = SET_KR_ALIAS.get(_rk, SET_KR_ALIAS.get(rk, _rk))
                # send_msg 없이 조용히 적용
                _do_set_silent(_k, rv, results)
            if results:
                send_msg("✅ 수치 변경 완료\n" + "\n".join(results),
                         level="normal", force=True)
            save_state()
            return
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
            return
        if len(cmd) == 3:
            raw_key = cmd[1].lower()
            key = SET_KR_ALIAS.get(raw_key, SET_KR_ALIAS.get(cmd[1], raw_key))
            if key in SET_ALLOWED_KEYS:
                try:
                    val = float(cmd[2])
                    # ── 범위 검증 ────────────────────────────
                    LIMITS = {
                        "target":      (0.01,  20.0),
                        "max_loss":    (-20.0, -0.01),
                        "drop":        (0.01,  10.0),
                        "trail_start": (0.01,  20.0),
                        "trail_gap":   (0.01,  10.0),
                        "be_trigger":  (0.01,  10.0),
                        "rsi_buy":     (5.0,   95.0),
                        "trade_count": (1,     200),
                        "cooldown":    (0,     7200),
                        "vol_min":     (0.0,   20.0),
                        "vol_max":     (0.1,   50.0),
                        "timeout_min": (1,     300),
                    }
                    if key in LIMITS:
                        lo, hi = LIMITS[key]
                        if not (lo <= val <= hi):
                            send_msg(f"❌ {key} 범위 초과 ({lo} ~ {hi})", level="normal", force=True)
                            return
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
                        # 수동으로 전략 수치를 바꾸면 동적 자동조정 OFF
                        if key in ("target", "max_loss", "drop",
                                   "trail_start", "trail_gap", "be_trigger"):
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
            "▶️ /start  ⏹️ /stop  🔴 /sell (즉시 매도)  🛒 /buy (수동 매수)\n"
            "📊 /status  💰 /balance  📋 /log  📆 /weekly\n"
            "⚙️ /set 항목 값  /budget 금액  /risk 금액\n"
            "🔍 /why (매수 안 되는 이유)\n"
            "🔄 /reload (설정 재로드)\n"
            "🔁 /switch KRW-XRP (런타임 종목 전환)\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "예) /switch KRW-XRP  → 리플로 전환\n"
            "    /switch KRW-BTC  → 비트코인으로 전환\n"
            "    /set rsi_buy 35\n"
            "    /budget 20000",
            level="normal", force=True
        )

    elif cmd[0] == "/reload":
        try:
            load_config()
            send_msg("✅ 설정 파일 재로드 완료", level="normal", force=True)
        except Exception as e:
            send_msg(f"❌ 설정 파일 재로드 실패: {e}", level="critical", force=True)

    # ── /switch : 런타임 중 종목 전환 ────────────────────────
    elif cmd[0] in ("/switch", "/전환", "/종목"):
        if bot["has_stock"]:
            send_msg(
                f"⚠️ 현재 {MARKET_CODE} 보유 중!\n"
                f"→ 먼저 /sell 로 청산한 뒤 전환하세요.",
                level="critical", force=True
            )
            return

        if len(cmd) < 2:
            profile_list = "\n".join(
                f"  {'★' if k in COIN_PROFILES else '◎'} {k}"
                for k in (list(COIN_PROFILES.keys()) + ["KRW-기타 (기본값 적용)"])
            )
            send_msg(
                f"🔄 종목 전환\n"
                f"사용법: /switch KRW-XRP\n\n"
                f"등록된 프로파일:\n{profile_list}\n\n"
                f"현재 종목: {MARKET_CODE}",
                level="normal", force=True
            )
            return

        new_market = cmd[1].strip().upper()
        if not new_market.startswith("KRW-"):
            new_market = "KRW-" + new_market

        _chk = get_price(new_market)
        if _chk is None:
            send_msg(f"⛔ {new_market} — 존재하지 않는 종목이거나 가격 조회 실패", level="warning", force=True)
            return
        if _chk < 100:
            send_msg(f"⛔ {new_market} 현재가 {_chk:,.0f}원 — 100원 미만 종목은 운용 불가", level="warning", force=True)
            return
        old_market = MARKET_CODE
        MARKET_CODE = new_market

        # 가격·지표 히스토리 초기화 (종목이 바뀌면 과거 데이터 무효)
        price_history.clear()
        volume_history.clear()
        timed_prices.clear()
        _price_trough_5m.clear()
        _vwap_sum_pv = 0.0
        _vwap_sum_v  = 0.0
        _vwap_value  = None
        _real_data_count = 0
        _last_price  = 0

        if MARKET_CODE not in COIN_PROFILES:
            send_msg(f"📡 {MARKET_CODE} 데이터 분석 중...", level="normal", force=True)
            auto = fetch_coin_stats(MARKET_CODE)
            if auto:
                COIN_PROFILES[MARKET_CODE] = auto
        apply_coin_profile(MARKET_CODE, source="/switch by user")

        # yaml 파일에도 반영 (다음 재시작 시 유지)
        try:
            with open(CFG_FILE, encoding="utf-8") as f:
                cfg_raw = f.read()
            if "market:" in cfg_raw:
                import re
                cfg_raw = re.sub(r'market:\s*"[^"]*"', f'market: "{MARKET_CODE}"', cfg_raw)
            else:
                cfg_raw += f'\nmarket: "{MARKET_CODE}"\n'
            with open(CFG_FILE, "w", encoding="utf-8") as f:
                f.write(cfg_raw)
            yaml_saved = True
            # manager_cfg.yaml도 업데이트
            import os as _os
            mgr_cfg_file = _os.path.join(_os.path.dirname(CFG_FILE), "manager_cfg.yaml")
            if _os.path.exists(mgr_cfg_file):
                with open(mgr_cfg_file, encoding="utf-8") as f:
                    mgr_raw = f.read()
                mgr_raw = re.sub(r'market:\s*\S+', f'market: {MARKET_CODE}', mgr_raw)
                with open(mgr_cfg_file, "w", encoding="utf-8") as f:
                    f.write(mgr_raw)
        except Exception as e:
            yaml_saved = False
            cprint(f"[yaml 저장 오류] {e}", Fore.YELLOW)

        profile = COIN_PROFILES.get(MARKET_CODE, COIN_PROFILE_DEFAULT)
        tag = "★ 전용 프로파일" if MARKET_CODE in COIN_PROFILES else "◎ 기본 프로파일"
        send_msg(
            f"🔄 종목 전환 완료!\n"
            f"  {old_market} → {MARKET_CODE}\n"
            f"  {tag} 자동 적용\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"  익절: {profile['target']}%  손절: {profile['max_loss']}%\n"
            f"  RSI: {profile['rsi_buy']}  눌림: {profile['drop']}%\n"
            f"  변동성: {profile['vol_min']}~{profile['vol_max']}%\n"
            f"  쿨다운: {profile['cooldown']}s  타임아웃: {profile['timeout_min']}분\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"  데이터 수집 재시작 중... (약 {REAL_DATA_MIN * LOOP_INTERVAL // 60}분 후 매수 재개)\n"
            f"  yaml 저장: {'✅' if yaml_saved else '⚠️ 실패 (수동 수정 필요)'}",
            level="critical", force=True
        )
        log_change("종목전환", f"{old_market} → {MARKET_CODE}")

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

    # ── /restart : 봇 재시작 ─────────────────────────────────
    elif cmd[0] == "/restart":
        send_msg("🔄 봇을 재시작합니다...", level="critical", force=True)
        time.sleep(1)
        _restart()

    # ── /log : 최근 오류 로그 ────────────────────────────────
    elif cmd[0] == "/log":
        import subprocess as _sp
        try:
            lines = int(cmd[1]) if len(cmd) > 1 else 30
            svc = _get_service_name() or "upbit-bot"
            result = _sp.run(
                ["journalctl", "-u", svc, "-n", str(lines), "--no-pager"],
                capture_output=True, text=True, timeout=5
            )
            log_text = result.stdout[-3000:] if len(result.stdout) > 3000 else result.stdout
            send_msg(f"📋 최근 로그 ({lines}줄)\n━━━━━━━━━━━━━━━━━━━━\n{log_text}", level="normal", force=True)
        except Exception as e:
            send_msg(f"❌ 로그 조회 실패: {e}", level="normal", force=True)

    # ── /reboot : 미니PC 재부팅 ──────────────────────────────
    elif cmd[0] == "/reboot":
        if len(cmd) > 1 and cmd[1] == "confirm":
            send_msg("🔁 미니PC를 재부팅합니다...", level="critical", force=True)
            time.sleep(2)
            import subprocess as _sp
            _sp.Popen(["sudo", "reboot"])
        else:
            send_msg(
                "⚠️ 미니PC 전체 재부팅입니다!\n"
                "→ 확인: /reboot confirm",
                level="critical", force=True,
                keyboard=[[{"text": "🔁 재부팅 확인", "callback_data": "/reboot confirm"}]]
            )

    # ── /sysinfo : 시스템 정보 ───────────────────────────────
    elif cmd[0] == "/sysinfo":
        try:
            import psutil, subprocess as _sp
            cpu    = psutil.cpu_percent(interval=1)
            mem    = psutil.virtual_memory()
            disk   = psutil.disk_usage("/")
            uptime = int(time.time() - psutil.boot_time())
            uh, um = divmod(uptime // 60, 60)
            ud, uh = divmod(uh, 24)
            temp_str = ""
            try:
                temps = psutil.sensors_temperatures()
                if temps:
                    for name, entries in temps.items():
                        if entries:
                            temp_str = f"\n온도  : {entries[0].current:.1f}°C"
                            break
            except:
                pass
            send_msg(
                f"🖥️ 시스템 정보\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"CPU   : {cpu:.1f}%\n"
                f"메모리: {mem.percent:.1f}%  ({mem.used//1024//1024:,}MB / {mem.total//1024//1024:,}MB)\n"
                f"디스크: {disk.percent:.1f}%  ({disk.used//1024//1024//1024:.1f}GB / {disk.total//1024//1024//1024:.1f}GB)\n"
                f"가동  : {ud}일 {uh}시간 {um}분"
                f"{temp_str}",
                level="normal", force=True
            )
        except Exception as e:
            send_msg(f"❌ 시스템 정보 조회 실패: {e}", level="normal", force=True)

    # ── /ip : 현재 퍼블릭 IP ────────────────────────────────
    elif cmd[0] == "/ip":
        try:
            res = requests.get("https://ifconfig.me", timeout=5)
            ip  = res.text.strip()
            send_msg(
                f"🌐 현재 퍼블릭 IP\n{ip}\n\n"
                f"→ 업비트 API 허용 IP가 바뀌었다면\n"
                f"  업비트 마이페이지에서 업데이트하세요.",
                level="normal", force=True
            )
        except Exception as e:
            send_msg(f"❌ IP 조회 실패: {e}", level="normal", force=True)

    # ── /screen : 화면 캡처 ──────────────────────────────────
    elif cmd[0] in ("/screen", "/스크린", "/캡처", "/화면"):
        send_msg("📸 화면 캡처 중...", level="normal", force=True)
        send_screen("수동 요청")

    elif cmd[0] == "/menu":
        kb = _build_menu_keyboard()
        send_msg("📋 메뉴", level="normal", keyboard=kb, force=True)

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
        {"command": "buy",      "description": "수동 매수  예) /buy  또는  /buy 50000"},
        {"command": "why",      "description": "매수/매도 안 되는 이유  예) /why  또는  /why sell"},
        {"command": "aggressive","description": "공격적 매수 모드 ON (RSI+눌림만)"},
        {"command": "normal",    "description": "일반 매수 모드 복귀"},
        {"command": "set",      "description": "전략 수치 변경  예) /set rsi_buy 35"},
        {"command": "budget",   "description": "주문 예산 변경  예) /budget 20000"},
        {"command": "risk",     "description": "손실 한도 변경  예) /risk 100000"},
        {"command": "reload",   "description": "설정 파일 재로드"},
        {"command": "version",  "description": "현재 버전 확인"},
        {"command": "update",   "description": "GitHub 최신 코드로 업데이트"},
        {"command": "rollback", "description": "이전 버전으로 롤백  예) /rollback  또는  /rollback abc1234"},
        {"command": "restart",  "description": "봇 재시작"},
        {"command": "log",      "description": "최근 오류 로그  예) /log  또는  /log 50"},
        {"command": "reboot",   "description": "미니PC 재부팅"},
        {"command": "sysinfo",  "description": "CPU/메모리/디스크 사용량"},
        {"command": "ip",       "description": "현재 퍼블릭 IP 확인"},
        {"command": "screen",   "description": "현재 PC 화면 캡처 후 전송"},
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
    global VOL_MIN_PCT, VOL_MAX_PCT, MARKET_CODE
    global _verify_mode, _verify_orig
    global _last_price, _vwap_value, _vwap_sum_pv, _vwap_sum_v
    global _real_data_count, daily_pnl_krw, _dynamic_mode
    global _cooldown_alert_sent, _max_trade_alert_sent, _weekly_stop
    global last_update_id, highest_profit, _last_status_line_ts, GRID_ENABLED

    # ── 종료 시 상태 저장 (systemd stop / kill 대응) ─────────
    import signal as _signal
    def _on_exit(signum, frame):
        cprint("\n[종료] 상태 저장 중...", Fore.YELLOW)
        try:
            save_state()
        except Exception:
            pass
        import sys as _sys
        _sys.exit(0)
    _signal.signal(_signal.SIGTERM, _on_exit)
    _signal.signal(_signal.SIGINT,  _on_exit)

    load_config()
    load_state()
    _register_bot_commands()
    _start_ipc_thread()   # IPC 폴링 스레드 시작 (0.3초 간격, 메인 루프와 독립)

    if not _manager_is_running():
        setup_reply_keyboard()
        init_pinned_message()
        # ── 시작 시 텔레그램 큐 비우기 (재시작 후 이전 명령 재실행 방지) ──
    try:
        res = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
            params={"offset": -1, "timeout": 1}, timeout=5
        ).json()
        if res.get("result"):
            uid = res["result"][-1]["update_id"]
            if uid > last_update_id:
                last_update_id = uid
                requests.get(
                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
                    params={"offset": uid + 1, "timeout": 1}, timeout=5
                )
                cprint(f"[시작] 텔레그램 큐 비움 (update_id={uid})", Fore.YELLOW)
    except Exception as e:
        cprint(f"[시작] 텔레그램 큐 비우기 실패: {e}", Fore.YELLOW)

    prefill_history()

    send_msg(
        f"🚀 {BOT_NAME} [{BOT_TAG}] v{BOT_VERSION} 시작!\n"
        f"종목: {MARKET_CODE}\n"
        f"예산: {ORDER_BUDGET_KRW:,.0f}원\n"
        f"RSI: {bot['rsi_buy']} / 익절: {bot['target']}% / 손절: {bot['max_loss']}%\n"
        f"쿨다운: {COOLDOWN_SEC}초  |  데이터 준비: {_real_data_count}/{REAL_DATA_MIN}\n\n"
        f"→ ⏯ 버튼으로 매매 시작/정지  |  /help 명령어 목록",
        level="critical", keyboard=KB_MAIN
    )

    while True:
        try:
            poll_telegram()
            now_dt = datetime.now()
            now_ts = time.time()

            check_daily_reset(now_dt)
            check_weekly_reset(now_dt)
            # ── 자동 종목 순환 테스트 (5분마다, 거래 차단) ──────────
            if not hasattr(run_bot, "_switch_ts"):
                run_bot._switch_ts = time.time()
                run_bot._switch_idx = 0
                try:
                    import pyupbit as _pu2
                    _prices = {k: _pu2.get_current_price(k) for k in COIN_PROFILES if k != MARKET_CODE}
                    run_bot._switch_list = [k for k, p in _prices.items() if p is not None and p >= 100]
                except Exception:
                    run_bot._switch_list = [k for k in COIN_PROFILES if k != MARKET_CODE]
            if time.time() - run_bot._switch_ts >= 21600:
                run_bot._switch_ts = time.time()
                if run_bot._switch_list:
                    run_bot._switch_idx = (run_bot._switch_idx + 1) % len(run_bot._switch_list)
                    new_market = run_bot._switch_list[run_bot._switch_idx]
                    MARKET_CODE = new_market
                    apply_coin_profile(MARKET_CODE, source="자동순환")
                    price_history.clear()
                    volume_history.clear()
                    timed_prices.clear()
                    send_msg(f"🔁 자동 종목 전환: {new_market}", level="critical", force=True)
            check_heartbeat(now_dt)
            _write_status_for_manager()
            update_pinned_message()   # 상단 고정 수치 업데이트 (30초마다)

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

            # VWAP 갱신 — 증분 거래량 사용 (24h 누적 아님)
            if volume and len(volume_history) >= 2:
                vol_delta = float(volume_history[-1]) - float(volume_history[-2])
                if vol_delta > 0:
                    _vwap_sum_pv += price * vol_delta
                    _vwap_sum_v  += vol_delta
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
                rsi_s  = f"{rsi:.1f}" if rsi else "N/A"
                ma_pct = f"MA({(price/ma20-1)*100:+.2f}%)" if ma20 else "MA:N/A"
                status_s = "▶" if bot["running"] else "⏹"
                agg_s  = "[공격적]" if _aggressive_mode else ""
                if bot["has_stock"] and bot["buy_price"] > 0:
                    pnl_p  = (price - bot["buy_price"]) / bot["buy_price"] * 100
                    hold_m = int((now_ts - _buy_time) / 60) if _buy_time else 0
                    hold_s = f"보유중 {pnl_p:+.2f}% {hold_m}분"
                else:
                    hold_s = "미보유"
                print(f"\r[{now_dt.strftime('%H:%M:%S')}] {MARKET_CODE} {price:,.0f} RSI:{rsi_s} {ma_pct} {hold_s} {status_s}{agg_s}   ", end="", flush=True)
                _last_status_line_ts = now_ts

            # 보유 중 청산 체크
            if bot["has_stock"] and bot["buy_price"] > 0:
                bp     = bot["buy_price"]
                pnl_p  = (price - bp) / bp * 100
                hp     = highest_profit

                # 최고 수익 갱신
                if pnl_p > hp:
                    highest_profit = pnl_p
                    hp = pnl_p

                # ── 그리드 모드 ──────────────────────────────
                if GRID_ENABLED and grid_levels:
                    for lv in grid_check_sell(price):
                        grid_do_sell_level(price, lv)
                    avg_pnl = (price - grid_avg_price) / grid_avg_price * 100 if grid_avg_price > 0 else 0
                    if avg_pnl <= bot["max_loss"]:
                        grid_do_sell_all(price, "그리드 손절")
                        time.sleep(LOOP_INTERVAL)
                        continue
                    if grid_check_buy(price):
                        grid_do_buy(price, len(grid_levels))
                    time.sleep(LOOP_INTERVAL)
                    continue

                # ── 일반 매도 조건 ───────────────────────────
                # 수수료(매수+매도 각 0.05% = 총 0.1%) 감안한 실질 손익 기준
                fee_adj = FEE_RATE * 2 * 100
                if (pnl_p - fee_adj) >= bot["target"]:
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

                # 검증 완료 후 원복
                if not bot["has_stock"] and _verify_mode and _verify_orig:
                    _verify_mode = False
                    bot.update({k: v for k, v in _verify_orig.items() if k in ("rsi_buy","target","max_loss","drop","trail_start")})
                    VOL_MIN_PCT = _verify_orig.get("vol_min", VOL_MIN_PCT)
                    VOL_MAX_PCT = _verify_orig.get("vol_max", VOL_MAX_PCT)
                    _verify_orig = {}
                    send_msg("✅ 검증 완료! 원래 수치로 복원됐어요.", level="critical", force=True)
                # 동적 파라미터 조정
                update_dynamic_parameters(price)
                # -- 기차 모드 자동 전환
                global _train_mode, _train_entry_price, _train_signal_count, _train_alert_sent
                _ma5_now = calc_ma5(price_history)
                _vol_ratio_now = locals().get("vol_ratio") or bot.get("_vol_ratio")
                _is_train, _t_count, _t_detail = detect_train(
                    bot.get("_last_rsi"), _ma5_now, bot.get("_ma20"), bot.get("_ma60"), price_history, _vol_ratio_now
                )
                if _is_train and not _train_alert_sent:
                    _train_alert_sent = True
                    send_msg('🚂 기차 감지 %d/5' % _t_count, level='critical')
                elif not _is_train:
                    _train_alert_sent = False
                if _train_mode and _t_count <= 2:
                    _train_mode = False
                    send_msg('🚉 기차 소멸 -> 역추세 모드 복귀', level='normal')
                _train_signal_count = _t_count
                _train_mode = _is_train


                # [PATCH] 변동성 돌파 매수
                vb_ok, vb_target = check_vbreak_signal(price)
                if vb_ok and ma_ok and volr_ok and not bot['has_stock']:
                    if do_buy(price, f'변동성돌파 목표:{vb_target:,.0f}'):
                        highest_profit = 0.0
                        bot['be_active'] = False
                # 최종 매수 승인
                if rsi_v_turn:
                    if _aggressive_mode:
                        # 공격적 모드: RSI V-Turn + 눌림만 체크
                        # (다이버전스, MA방향, VWAP, 거래량 제외)
                        buy_ok = drop_ok
                    else:
                        # 일반 모드: 전체 조건 통과
                        buy_ok = (divergence_ok and vwap_ok and volr_ok and ma_ok and drop_ok)

                    if buy_ok:
                        mode_str = "공격적" if _aggressive_mode else "일반"
                        if do_buy(price, f"RSI-V-Turn [{mode_str}]"):
                            highest_profit = 0.0
                            bot["be_active"] = False

            bot["prev_rsi2"], bot["prev_rsi"] = bot["prev_rsi"], rsi
            time.sleep(LOOP_INTERVAL)

        except Exception as e:
            tb = traceback.format_exc()
            cprint(f"\n[봇 오류]\n{tb}", Fore.RED, bright=True)
            global _last_error_alert_ts
            _now_err = time.time()
            if _now_err - _last_error_alert_ts >= 300:
                _last_error_alert_ts = _now_err
                try:
                    requests.post(
                        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                        json={
                            "chat_id": CHAT_ID,
                            "text": f"🚨 봇 오류 발생\n→ {str(e)[:200]}\n→ 자동 재시도 중 (5분에 1회 알림)",
                            "disable_notification": False
                        },
                        timeout=5
                    )
                except Exception as e2:
                    cprint(f"[오류 알림 전송 실패] {e2}", Fore.YELLOW)
            time.sleep(5)

if __name__ == "__main__":
    run_bot()
