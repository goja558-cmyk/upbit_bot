"""
==============================================================
  KIS 자동매매 봇 - v10.9
  누적 수정 이력:
  1~30. (v10.6 참조)
  ──────────── v10.7 추가 ────────────
  31. 알림 3단계 분류   → critical / normal / silent
  32. CMD 화면 개선     → colorama 색상, 한 줄 상태 표시
  33. 날짜별 자동 백업  → backups/ 폴더, 7일 자동 삭제
  34. VWAP             → 현재가 ≤ VWAP 일 때만 매수
  35. 거래량 비율      → VOL_RATIO_MIN 이상일 때만 매수
  36. CPU/RAM 표시     → 하트비트·상태에 시스템 리소스 포함
  37. 야간 알림 차단   → 15:30~08:30 critical 외 자동 차단
  38. 미개장일 알림    → 주말 12:00·18:00 정상 작동 알림
  ──────────── v10.8 추가 ────────────
  39. 매수 조건 디버그 로그
      → 평상시: 한 줄에 각 조건 ✅/❌ 실시간 표시
      → RSI 기준+5 이내 근접 시: 전체 조건 상세 출력
  ──────────── v10.9 추가 ────────────
  40. 주간 손실 한도
      → WEEKLY_LOSS_LIMIT_KRW 초과 시 그 주 자동 정지
      → 매주 월요일 00:00 자동 초기화
  41. 매수 후 타임컷 (포지션 타임아웃)
      → POS_TIMEOUT_MIN 분 경과 시 본절 근처 강제 청산
      → 청산 기준: 매수가 × (1 - POS_TIMEOUT_LOSS_PCT/100) 이상이면 매도
  42. 연속 손실 시 RSI 기준 자동 강화
      → CONSEC_LOSS_ALERT 회 이상 연패 시 rsi_buy를 RSI_TIGHTEN_STEP 만큼 낮춤
      → /start 또는 연패 해소 시 원래 기준으로 복원
  43. /log 텔레그램 명령어
      → 오늘 trade_log.csv 내역을 텔레그램으로 텍스트 출력
  44. /reload 텔레그램 명령어
      → kis_devlp.yaml 재로딩 (봇 재시작 없이 설정 반영)
  45. CSV 컬럼 확장
      → rsi, vwap, ma20, ma60, reason 컬럼 추가
==============================================================
"""

BOT_VERSION  = "11.0"   # 업데이트 시 이 숫자를 올려주세요
BOT_NAME     = "KIS 주식봇"

import sys, os, time, json, csv, requests, yaml, shutil, traceback
import numpy as np
import psutil
from datetime import datetime, date, timedelta
from collections import deque

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

# ============================================================
# [1] 사용자 설정  ← 이 구역만 수정하면 됩니다
# ============================================================

# ── 텔레그램 ─────────────────────────────────────────────────
# ⚠️ 보안: 토큰과 ID는 코드에 직접 쓰지 않고 kis_devlp.yaml에서 읽어옵니다.
#   kis_devlp.yaml 에 아래 항목을 추가하세요:
#     telegram_token: "여기에_봇_토큰"
#     chat_id: "여기에_내_ID"
TELEGRAM_TOKEN = ""   # kis_devlp.yaml 로드 후 자동 설정됨
CHAT_ID        = ""   # kis_devlp.yaml 로드 후 자동 설정됨

# ── 종목 / 수수료 ────────────────────────────────────────────
STOCK_CODE = "114800"    # 거래할 종목 코드 (114800 = KODEX 인버스)
FEE_RATE   = 0.00015     # 매매 수수료율 (0.015% → 증권사마다 다를 수 있음)

# ── 매매 예산 ────────────────────────────────────────────────
ORDER_BUDGET_KRW   = 50_000   # 1회 주문에 쓸 최대 금액 (원)
                               # ※ /budget 명령으로 실시간 변경 가능
ORDER_BUDGET_RATIO = 0.9      # 예산의 몇 %까지 실제로 쓸지 (0.9 = 90%)
                               # 잔고가 예산보다 적으면 잔고 기준으로 계산됨

# ── 손실 한도 ────────────────────────────────────────────────
MAX_DAILY_LOSS_KRW  = -50_000   # 하루 최대 손실 (원) — 이 금액 손실 시 당일 봇 정지
DAILY_LOSS_BASE_KRW = 100_000   # 손실 한도 계산 기준 금액 (원)
MAX_DAILY_LOSS_PCT  = -2.0      # 기준 금액 대비 최대 손실 % (둘 중 먼저 걸리는 쪽 적용)
                                 # 예) 기준 100,000원 × 2% = 2,000원 vs MAX_DAILY_LOSS_KRW 중 작은 쪽
                                 # ※ /risk 명령으로 DAILY_LOSS_BASE_KRW 실시간 변경 가능
WEEKLY_LOSS_LIMIT_KRW = -200_000  # 주간 최대 손실 (원) — 초과 시 다음 주 월요일까지 자동 정지

# ── 매매 횟수 제한 ───────────────────────────────────────────
MAX_TRADE_COUNT = 10   # 하루 최대 매매 횟수 (이 횟수 채우면 당일 추가 매수 안 함)

# ── 전략 수치 (매수·매도 기준) ───────────────────────────────
# 아래 값들은 실행 중에 /set 명령으로도 바꿀 수 있습니다
# 예) /set target 1.5   → 익절 목표를 1.5%로 변경
#     /set rsi_buy 28   → RSI 매수 기준을 28로 변경
BOT_TARGET      =  1.5   # 익절 목표 (%) — 이 수익률 달성 시 자동 매도
BOT_MAX_LOSS    = -1.5   # 손절 기준 (%) — 이 손실률 도달 시 자동 매도
BOT_DROP        =  0.8   # 눌림 기준 (%) — MA20 대비 이만큼 빠져야 매수 고려
BOT_TRAIL_START =  0.6   # 트레일링 스탑 시작 수익률 (%) — 이 수익 넘으면 고점 추적 시작
BOT_TRAIL_GAP   =  0.3   # 트레일링 스탑 간격 (%) — 고점 대비 이만큼 빠지면 매도
BOT_BE_TRIGGER  =  0.4   # 본절 보호 발동 수익률 (%) — 이 수익 넘으면 매수가 밑으로 안 팜
BOT_RSI_BUY     =  30    # RSI 매수 기준 — 이 값 이하일 때 과매도로 판단하고 매수 고려
BOT_RSI_PERIOD  =  14    # RSI 계산 기간 (봉 개수) — 보통 14 고정

# ── VWAP / 거래량 필터 ───────────────────────────────────────
VWAP_FILTER   = True   # True = 오늘 평균 체결가 아래일 때만 매수 / False = 끄기
VOL_RATIO_MIN = 1.0    # 평소 거래량 대비 최소 배수 (1.0 = 평소 이상일 때만 매수)

# ── 변동성 필터 ──────────────────────────────────────────────
VOL_WINDOW_SEC = 300    # 변동성 계산 기간 (초) — 최근 5분간 가격 움직임으로 계산
VOL_MIN_PCT    = 0.15   # 최소 변동성 (%) — 너무 잠잠하면 매수 안 함
VOL_MAX_PCT    = 3.0    # 최대 변동성 (%) — 너무 급등락 중이면 매수 안 함

# ── 슬리피지 / 쿨다운 ────────────────────────────────────────
MAX_SLIPPAGE_PCT = 0.5   # 허용 슬리피지 (%) — 주문 체결가가 현재가보다 이 % 이상 벗어나면 즉시 매도
COOLDOWN_SEC     = 300   # 매도 후 다음 매수까지 대기 시간 (초) — 기본 5분

# ── 포지션 타임아웃 ──────────────────────────────────────────
POS_TIMEOUT_MIN      = 30    # 매수 후 N분 동안 목표/손절 안 걸리면 강제 청산
POS_TIMEOUT_LOSS_PCT = 0.3   # 단, 손실이 이 % 넘으면 타임아웃 청산 생략 (손절 로직에 맡김)

# ── 연패 시 RSI 자동 강화 ────────────────────────────────────
RSI_TIGHTEN_STEP = 3    # 연패 1회마다 RSI 기준을 이 값만큼 낮춤 (더 까다롭게)
RSI_BUY_DEFAULT  = BOT_RSI_BUY   # RSI 기준 기본값 (승리하거나 /start 치면 이 값으로 복원)
RSI_BUY_MIN      = 15   # RSI 기준 최솟값 (이 이하로는 낮추지 않음)

# ── 연승 시 예산 자동 확대 ───────────────────────────────────
WIN_STREAK_STEP    = 3       # N연승마다 예산 확대
WIN_BUDGET_ADD_PCT = 10      # 연승 달성 시 예산 증가율 (%)
WIN_BUDGET_MAX_KRW = 200_000 # 예산 자동 확대 최대 한도 (원)

# ── 장 운영 시간 ─────────────────────────────────────────────
MARKET_OPEN      = (9,  0)    # 장 시작 시각
MARKET_CLOSE     = (15, 20)   # 장 마감 시각 (이후 보유 중이면 강제 청산)
TIMECUT_NO_BUY   = (15, 10)   # 이 시각 이후 신규 매수 안 함
TIMECUT_FORCE_SELL = (15, 15) # 이 시각 이후 보유 중이면 무조건 매도
LUNCH_START      = (12,  0)   # 점심 시간 시작 (매수 중단)
LUNCH_END        = (13,  0)   # 점심 시간 끝 (매수 재개)

# ── 알림 설정 ────────────────────────────────────────────────
NIGHT_SILENCE_START   = (15, 30)   # 야간 알림 차단 시작 (critical 제외)
NIGHT_SILENCE_END     = (8,  30)   # 야간 알림 차단 끝
NONMARKET_ALERT_HOURS = [12, 16]   # 주말·공휴일에 정상 가동 알림 보낼 시각
SCREEN_AUTO_TIMES     = []  # 자동 화면 캡처 전송 시각 (비어있으면 비활성화)

# ── 기타 ─────────────────────────────────────────────────────
LOOP_INTERVAL   = 1    # 매매 루프 간격 (초) — 낮출수록 빠르지만 API 호출 증가
HISTORY_PREFILL = 70   # 시작 시 미리 채울 과거 데이터 개수 (MA60 계산에 최소 60 필요)
MAX_API_FAIL    = 5    # 연속 API 실패 N회 시 텔레그램 경고 발송
WARMUP_MINUTES  = 5    # 장 시작(09:00) 후 이 분(分) 동안 매수 신호 무시 (지표 안정화 대기)
BACKUP_KEEP_DAYS = 7   # 백업 파일 보관 기간 (일) — 이 기간 지난 백업은 자동 삭제

SET_ALLOWED_KEYS = {
    "target", "max_loss", "drop",
    "trail_start", "trail_gap", "be_trigger", "rsi_buy",
    # 4순위 추가: 텔레그램 실시간 제어
    "trade_count", "cooldown", "vol_min", "vol_max",
    "slippage", "timeout_min", "vwap_filter",
}

# ============================================================
# [1-2] 종목 프리셋  ← 새 종목 추가 시 여기에 등록하세요
# ============================================================
# /stock 종목코드  명령으로 텔레그램에서 바로 전환 가능
# 보유 중일 때는 전환 불가 (매도 후 변경)
#
# 프리셋에 없는 종목코드를 입력하면 "범용 설정"이 자동 적용됩니다
# ─────────────────────────────────────────────────────────────
STOCK_PRESETS = {
    # ── 2배 인버스 (변동성 크다 → 목표/손절 넓게) ─────────────
    "252670": {
        "name":         "KODEX 200선물인버스2X",
        "note":         "⚠️ 예탁금 1,000만원 필요",
        "target":        1.2,
        "max_loss":     -1.5,
        "drop":          0.8,
        "trail_start":   0.6,
        "trail_gap":     0.3,
        "be_trigger":    0.4,
        "rsi_buy":       30,
        "vol_min":       0.15,
        "vol_max":       3.0,
        "vol_ratio_min": 1.0,
        "timeout_min":   30,
        "cooldown_sec":  300,
    },
    # ── 1배 인버스 (변동성 보통 → 목표/손절 좁게) ─────────────
    "114800": {
        "name":         "KODEX 인버스",
        "note":         "예탁금 조건 없음",
        "target":        0.9,
        "max_loss":     -1.1,
        "drop":          0.5,
        "trail_start":   0.4,
        "trail_gap":     0.2,
        "be_trigger":    0.3,
        "rsi_buy":       30,
        "vol_min":       0.10,
        "vol_max":       2.0,
        "vol_ratio_min": 1.0,
        "timeout_min":   40,
        "cooldown_sec":  300,
    },
    # ── KODEX 200 정방향 (변동성 보통) ────────────────────────
    "069500": {
        "name":         "KODEX 200",
        "note":         "예탁금 조건 없음",
        "target":        0.9,
        "max_loss":     -1.1,
        "drop":          0.5,
        "trail_start":   0.4,
        "trail_gap":     0.2,
        "be_trigger":    0.3,
        "rsi_buy":       30,
        "vol_min":       0.10,
        "vol_max":       2.0,
        "vol_ratio_min": 1.0,
        "timeout_min":   40,
        "cooldown_sec":  300,
    },
    # ── 2배 레버리지 (변동성 크다) ────────────────────────────
    "122630": {
        "name":         "KODEX 레버리지",
        "note":         "⚠️ 예탁금 1,000만원 필요",
        "target":        1.2,
        "max_loss":     -1.5,
        "drop":          0.8,
        "trail_start":   0.6,
        "trail_gap":     0.3,
        "be_trigger":    0.4,
        "rsi_buy":       30,
        "vol_min":       0.15,
        "vol_max":       3.0,
        "vol_ratio_min": 1.0,
        "timeout_min":   30,
        "cooldown_sec":  300,
    },
}

# 프리셋에 없는 종목에 적용되는 범용 설정
PRESET_DEFAULT = {
    "name":         "직접 입력 종목",
    "note":         "범용 설정 적용됨",
    "target":        1.0,
    "max_loss":     -1.2,
    "drop":          0.6,
    "trail_start":   0.5,
    "trail_gap":     0.25,
    "be_trigger":    0.35,
    "rsi_buy":       30,
    "vol_min":       0.12,
    "vol_max":       2.5,
    "vol_ratio_min": 1.0,
    "timeout_min":   35,
    "cooldown_sec":  300,
}

def apply_stock_preset(code):
    """종목 코드에 맞는 프리셋을 bot 딕셔너리와 전역 변수에 반영."""
    global STOCK_CODE, VOL_MIN_PCT, VOL_MAX_PCT, VOL_RATIO_MIN
    global POS_TIMEOUT_MIN, COOLDOWN_SEC, RSI_BUY_DEFAULT

    preset = STOCK_PRESETS.get(code, PRESET_DEFAULT)

    STOCK_CODE      = code
    VOL_MIN_PCT     = preset["vol_min"]
    VOL_MAX_PCT     = preset["vol_max"]
    VOL_RATIO_MIN   = preset["vol_ratio_min"]
    POS_TIMEOUT_MIN = preset["timeout_min"]
    COOLDOWN_SEC    = preset["cooldown_sec"]
    RSI_BUY_DEFAULT = preset["rsi_buy"]

    bot["target"]      = preset["target"]
    bot["max_loss"]    = preset["max_loss"]
    bot["drop"]        = preset["drop"]
    bot["trail_start"] = preset["trail_start"]
    bot["trail_gap"]   = preset["trail_gap"]
    bot["be_trigger"]  = preset["be_trigger"]
    bot["rsi_buy"]     = preset["rsi_buy"]

    return preset

# ============================================================
# [2] 경로 설정
# ============================================================
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
LOG_FILE   = os.path.join(BASE_DIR, "trade_log.csv")
STATE_FILE = os.path.join(BASE_DIR, "bot_state_v10.json")
CFG_FILE   = os.path.join(BASE_DIR, "kis_devlp.yaml")
BACKUP_DIR      = os.path.join(BASE_DIR, "backups")
CHANGELOG_DIR   = os.path.join(BASE_DIR, "changelog")
SHARED_DIR      = os.path.join(BASE_DIR, "shared")   # 두 봇 공유 폴더
os.makedirs(BACKUP_DIR,    exist_ok=True)
os.makedirs(CHANGELOG_DIR, exist_ok=True)
os.makedirs(SHARED_DIR,    exist_ok=True)
# 공유 로그 경로 (통합 리포트용)
KIS_SHARED_LOG   = os.path.join(SHARED_DIR, "kis_trade_log.csv")
COIN_SHARED_LOG  = os.path.join(SHARED_DIR, "upbit_trade_log.csv")

def load_config():
    if not os.path.exists(CFG_FILE):
        print(f"{Fore.RED}❌ 설정 파일 없음: {CFG_FILE}"); sys.exit()
    with open(CFG_FILE, encoding="UTF-8") as f:
        return yaml.load(f, Loader=yaml.FullLoader)

_cfg = load_config()

# ── 텔레그램 토큰/ID → yaml에서 자동 로드 ─────────────────
TELEGRAM_TOKEN = _cfg.get("telegram_token", TELEGRAM_TOKEN)
CHAT_ID        = str(_cfg.get("chat_id",        CHAT_ID))
if not TELEGRAM_TOKEN or not CHAT_ID:
    print(f"{Fore.RED}❌ kis_devlp.yaml에 telegram_token / chat_id 항목이 없습니다."); sys.exit()

# ── 봇 상태 ─────────────────────────────────────────────────
bot = {
    "target":      BOT_TARGET,
    "max_loss":    BOT_MAX_LOSS,
    "drop":        BOT_DROP,
    "trail_start": BOT_TRAIL_START,
    "trail_gap":   BOT_TRAIL_GAP,
    "be_trigger":  BOT_BE_TRIGGER,
    "be_active":   False,
    "rsi_period":  BOT_RSI_PERIOD,
    "rsi_buy":     BOT_RSI_BUY,
    "prev_rsi":    None,
    "prev_rsi2":   None,
    "has_stock":   False,
    "buy_price":   0,
    "filled_qty":  0,
    "is_running":  True,
    # 디버그용 내부 저장
    "_ma20":       None,
    "_ma60":       None,
}

MA_PERIOD_SHORT = 20
MA_PERIOD_LONG  = 60
price_history   = deque(maxlen=200)
timed_prices    = deque(maxlen=500)
volume_history  = deque(maxlen=200)

# ── VWAP 누적 ────────────────────────────────────────────────
_vwap_pv_sum = 0.0
_vwap_vol_sum = 0.0
_vwap_value   = 0.0

# ── 일간 통계 ────────────────────────────────────────────────
highest_profit      = 0.0
daily_pnl_krw       = 0
trade_count         = 0
last_update_id      = 0
_last_reset_day     = None
_daily_report_sent  = False
_last_sell_time     = 0.0
_last_tg_poll       = 0.0
_api_fail_count     = 0
_api_fail_first_ts  = 0.0  # API 첫 실패 시각 (연속 실패 지속 시간 계산용)

consecutive_loss    = 0
_dynamic_mode       = True   # False면 update_dynamic_parameters() 비활성 (/set 수동 시)
consecutive_win     = 0   # 연속 수익 횟수 (연승 예산 확대용)
CONSEC_LOSS_ALERT   = 3

win_count           = 0
loss_count          = 0

_morning_alert_sent  = False
_pause_alert_sent    = ""   # 마지막으로 알림 보낸 중단 구간 이름 (중복 방지)
_price_trough_5m     = []
_last_heartbeat_hour = -1
_last_screen_time    = (-1, -1)  # 마지막 자동 캡처 (hour, minute)
_nonmarket_alert_sent = set()

# ── [40] 주간 손실 추적 ──────────────────────────────────────
weekly_pnl_krw      = 0
_last_reset_week    = None          # (year, week) 튜플
_weekly_stop        = False         # 주간 한도 초과 플래그

# ── [41] 포지션 진입 시각 ────────────────────────────────────
_buy_time           = 0.0           # 매수 체결 시각 (time.time())

# ── 중복 주문 방지 플래그 ─────────────────────────────────────
_order_pending      = False         # True면 주문 진행 중 → 신규 매수 차단

# ── 디버그: 마지막 근접 로그 출력 시각 (도배 방지) ───────────
_last_status_line_ts = 0.0  # status_line 마지막 출력 시각
_last_detail_log_ts  = 0.0  # 상세 로그 마지막 출력 시각 (도배 방지)

# ── 마지막으로 수집한 현재가 캐시 (API 재호출 없이 즉시 사용) ─
_last_price          = 0

# ── 실제 수집된 진짜 데이터 카운트 (가짜 채움 데이터 제외) ───
# prefill 시 현재가로 채운 가짜 데이터는 제외하고,
# 실제 루프에서 수집한 진짜 데이터만 카운트
_real_data_count     = 0
REAL_DATA_MIN        = 60   # MA60 계산에 최소 60개 필요 (기존 20에서 상향)

# ============================================================
# [3] 유틸: 색상 출력 / 야간 판단 / 미개장일 판단
# ============================================================
def cprint(text, color=Fore.WHITE, bright=False):
    prefix = Style.BRIGHT if bright else ""
    print(f"{prefix}{color}{text}{Style.RESET_ALL}")

def chk(ok):
    """조건 충족 여부 이모지."""
    return "✅" if ok else "❌"

def is_night_silence():
    """15:30 ~ 익일 08:30 → True (야간 무음 구간)."""
    now = datetime.now()
    h, m = now.hour, now.minute
    sh, sm = NIGHT_SILENCE_START
    eh, em = NIGHT_SILENCE_END
    after_start = (h > sh) or (h == sh and m >= sm)
    before_end  = (h < eh) or (h == eh and m <  em)
    return after_start or before_end

def is_market_open_day():
    """평일이면 True. (주말 → False, 공휴일은 주말만 체크)"""
    return datetime.now().weekday() < 5

# ============================================================
# [4] CMD 한 줄 상태 표시 + 디버그 로그 (v10.8)
# ============================================================
def status_line(price, rsi, vol, vwap, vol_ratio, pnl_pct=0.0):
    """
    매 루프 한 줄 덮어쓰기 출력.
    미보유 시: 각 매수 조건 ✅/❌ 표시
    보유 시  : 현재 손익 표시
    """
    now_str  = datetime.now().strftime("%H:%M:%S")
    cpu      = psutil.cpu_percent(interval=None)
    ram      = psutil.virtual_memory().percent
    ma20     = bot.get("_ma20")
    ma60     = bot.get("_ma60")

    if bot["has_stock"]:
        # 보유 중: 손익 표시
        pnl_col = Fore.GREEN if pnl_pct >= 0 else Fore.RED
        cond_str = (
            f"손익: {pnl_col}{pnl_pct:+.2f}%{Style.RESET_ALL}  "
            f"{Fore.GREEN}보유중{Style.RESET_ALL}"
        )
    else:
        # 대기 중: 조건별 ✅/❌
        drop_now   = ((ma20 - price) / ma20 * 100) if ma20 else 0.0
        rsi_ok     = rsi is not None and rsi <= bot["rsi_buy"]
        ma_ok      = bool(ma20 and ma60 and ma20 > ma60)
        drop_ok    = drop_now >= bot["drop"]
        vol_ok     = vol is not None and VOL_MIN_PCT <= vol <= VOL_MAX_PCT
        vwap_ok    = (not VWAP_FILTER) or (not vwap) or (price <= vwap)
        volr_ok    = (vol_ratio is None) or (vol_ratio >= VOL_RATIO_MIN)

        rsi_str    = f"{rsi:.1f}" if rsi is not None else "N/A"
        vol_str    = f"{vol:.2f}%" if vol is not None else "N/A"
        vwap_str   = f"{vwap:,.0f}" if vwap else "N/A"
        volr_str   = f"{vol_ratio:.2f}x" if vol_ratio is not None else "N/A"
        drop_str   = f"{drop_now:.2f}%"

        rsi_col    = Fore.CYAN if rsi_ok else Fore.WHITE

        cond_str = (
            f"과매도{chk(rsi_ok)}({rsi_col}{rsi_str}{Style.RESET_ALL}/기준{bot['rsi_buy']})  "
            f"상승추세{chk(ma_ok)}  "
            f"눌림{chk(drop_ok)}({drop_str}/기준{bot['drop']}%)  "
            f"변동성{chk(vol_ok)}({vol_str})  "
            f"평균가아래{chk(vwap_ok)}({vwap_str}원)  "
            f"거래량{chk(volr_ok)}({volr_str})  "
            f"{Fore.WHITE}매수대기중{Style.RESET_ALL}"
        )

    line = (
        f"[{now_str}] "
        f"가격:{Fore.YELLOW}{price:,}{Style.RESET_ALL}  "
        f"{cond_str}  "
        f"CPU:{cpu:.0f}% RAM:{ram:.0f}%"
    )
    # 1분마다 한 번만 출력 (마지막 출력 시각 기반 — 안정적)
    global _last_status_line_ts
    now_ts = time.time()
    if now_ts - _last_status_line_ts >= 60:
        _last_status_line_ts = now_ts
        print(f"\r{line:<180}", end="", flush=True)


def detail_log(price, rsi, vol, drop, ma20, ma60, vwap, vol_ratio, rsi_v_turn, prev1, prev2):
    """
    [v10.8-수정39] RSI가 기준+5 이내로 근접했을 때 상세 조건 출력.
    30초에 한 번만 출력 (도배 방지).
    """
    global _last_detail_log_ts
    now_ts = time.time()
    if now_ts - _last_detail_log_ts < 30:
        return
    _last_detail_log_ts = now_ts

    drop_ok  = drop >= bot["drop"]
    ma_ok    = bool(ma20 and ma60 and ma20 > ma60)
    vol_ok   = vol is not None and VOL_MIN_PCT <= vol <= VOL_MAX_PCT
    vwap_ok  = (not VWAP_FILTER) or (not vwap) or (price <= vwap)
    volr_ok  = (vol_ratio is None) or (vol_ratio >= VOL_RATIO_MIN)
    div_ok   = check_5m_divergence()
    rsi_ok   = rsi <= bot["rsi_buy"]

    vwap_str = f"{vwap:.0f}" if vwap else "N/A"
    volr_str = f"{vol_ratio:.2f}x" if vol_ratio is not None else "N/A"

    # ── RSI 반등 설명 ─────────────────────────────────────────
    if prev1 is not None and prev2 is not None:
        if rsi_v_turn:
            rsi_turn_str = f"✅ {prev2:.1f} → {prev1:.1f} → {rsi:.1f} 올라오고 있어요!"
        elif rsi > prev1:
            rsi_turn_str = f"⚠️ {prev1:.1f} → {rsi:.1f} 올라오는 중이지만 아직 과매도 확인 필요"
        elif rsi < prev1:
            rsi_turn_str = f"❌ {prev1:.1f} → {rsi:.1f} 아직 내려가는 중"
        else:
            rsi_turn_str = f"❌ {prev1:.1f} → {rsi:.1f} 보합 (방향 미확인)"
    else:
        rsi_turn_str = "❌ 데이터 부족 (조금 더 기다려요)"

    # ── 상승 추세 설명 ────────────────────────────────────────
    if ma20 and ma60:
        diff = ma20 - ma60
        if ma_ok:
            ma_str = f"✅ 단기 {ma20:.0f}원 > 장기 {ma60:.0f}원 (+{diff:.1f}원) 상승 추세예요"
        else:
            ma_str = f"❌ 단기 {ma20:.0f}원 < 장기 {ma60:.0f}원 ({diff:.1f}원) 아직 하락 추세예요"
    else:
        ma_str = "❌ 데이터 부족"

    # ── 이중 바닥 설명 ────────────────────────────────────────
    if len(_price_trough_5m) == 0:
        div_str = "❌ 아직 저점 없음 → 패턴 형성 전이에요"
    elif len(_price_trough_5m) == 1:
        _, p1, r1 = _price_trough_5m[0]
        div_str = f"⚠️ 1차 저점 {p1:,}원/RSI {r1:.1f} → 2차 저점 기다리는 중"
    else:
        _, p1, r1 = _price_trough_5m[0]
        _, p2, r2 = _price_trough_5m[1]
        if div_ok:
            div_str = f"✅ 1차 {p1:,}원/RSI {r1:.1f} → 2차 {p2:,}원/RSI {r2:.1f} 패턴 완성!"
        else:
            div_str = f"❌ 1차 {p1:,}원/RSI {r1:.1f} → 2차 {p2:,}원/RSI {r2:.1f} 패턴 불일치"

    # ── 눌림 설명 ─────────────────────────────────────────────
    need = bot["drop"] - drop
    if drop_ok:
        drop_str = f"✅ 평균가 대비 {drop:.2f}% 빠짐 → 충분히 눌렸어요"
    else:
        drop_str = f"❌ 평균가 대비 {drop:.2f}% 빠짐 → {need:.2f}% 더 빠져야 해요"

    print(
        f"\n{'─'*50}\n"
        f"🔍 매수 신호 거의 다 됐어요! [{datetime.now().strftime('%H:%M:%S')}]\n"
        f"  과매도(많이 빠졌나?) : {rsi:.2f} {chk(rsi_ok)} (기준 {bot['rsi_buy']} 이하 / 현재 {rsi:.2f})\n"
        f"  RSI 반등 확인       : {rsi_turn_str}\n"
        f"  이중 바닥 확인      : {div_str}\n"
        f"  상승 추세           : {ma_str}\n"
        f"  변동성              : {vol:.2f}% {chk(vol_ok)} (기준 {VOL_MIN_PCT}~{VOL_MAX_PCT}%)\n"
        f"  눌림                : {drop_str}\n"
        f"  평균가 아래인가?    : {chk(vwap_ok)} (오늘 평균가 {vwap_str}원 / 현재 {price:,}원)\n"
        f"  거래량              : {chk(volr_ok)} ({volr_str} / 평소 대비)\n"
        f"{'─'*50}"
    )

# ============================================================
# [5] 알림 레벨 분류 send_msg
# ============================================================
def send_msg(text, level="normal", keyboard=None, force=False):
    """
    level: critical(긴급/알림음) / normal(일반) / silent(무음)
    야간(15:30~08:30)에는 critical 만 전송.
    force=True: 야간이어도 강제 전송 (명령 응답 등)
    keyboard: inline_keyboard 배열 (버튼 첨부 시)
    """
    if is_night_silence() and level != "critical" and not force:
        cprint(f"\n[야간 알림 차단] {text[:50]}", Fore.MAGENTA)
        return

    color_map = {"critical": Fore.RED, "normal": Fore.CYAN, "silent": Fore.WHITE}
    cprint(f"\n📡 [{level.upper()}] {text}", color_map.get(level, Fore.WHITE),
           bright=(level == "critical"))

    try:
        url     = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id":              CHAT_ID,
            "text":                 text,
            "disable_notification": (level == "silent"),
        }
        if keyboard:
            payload["reply_markup"] = {"inline_keyboard": keyboard}
        res = requests.post(url, json=payload, timeout=5)
        if res.status_code == 200:
            cprint("✅ 텔레그램 전송 완료", Fore.GREEN)
        else:
            cprint(f"❌ 텔레그램 전송 실패: {res.text}", Fore.RED)
    except Exception as e:
        cprint(f"❌ 네트워크 오류: {e}", Fore.RED)

# 자주 쓰는 버튼 묶음
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
        {"text": "📆 주간",   "callback_data": "/weekly"},
        {"text": "🔄 설정",   "callback_data": "/reload"},
        {"text": "❓ 도움말", "callback_data": "/help"},
    ],
]

KB_HOLDING = [
    [
        {"text": "📊 상태",     "callback_data": "/status"},
        {"text": "📋 내역",     "callback_data": "/log"},
    ],
    [
        {"text": "⏸️ 봇 정지",  "callback_data": "/stop"},
        {"text": "📈 리포트",   "callback_data": "/report"},
    ],
]

def poll_callback():
    """인라인 버튼 콜백 처리 (getUpdates 에서 callback_query 도 처리)."""
    pass  # poll_telegram() 에서 통합 처리

def send_menu(text="📋 메뉴를 선택하세요", level="normal"):
    """메인 버튼 메뉴 전송."""
    send_msg(text, level=level, keyboard=KB_MAIN)

# ============================================================
# [6] 인프라 (토큰 버킷, API, KIS 인증)
# ============================================================
_BUCKET_CAPACITY  = 4
_BUCKET_RATE      = 3.5
_bucket_tokens    = float(_BUCKET_CAPACITY)
_bucket_last_time = time.time()

def _acquire_token():
    global _bucket_tokens, _bucket_last_time
    now = time.time()
    _bucket_tokens = min(
        _BUCKET_CAPACITY,
        _bucket_tokens + (now - _bucket_last_time) * _BUCKET_RATE
    )
    _bucket_last_time = now
    if _bucket_tokens < 1.0:
        time.sleep((1.0 - _bucket_tokens) / _BUCKET_RATE)
        _bucket_tokens = 0.0
    else:
        _bucket_tokens -= 1.0

def api_call(method, url, **kwargs):
    global _api_fail_count, _api_fail_first_ts
    _acquire_token()
    for attempt in range(3):
        try:
            r = getattr(requests, method)(url, timeout=10, **kwargs)
            if r.status_code == 200:
                _api_fail_count    = 0
                _api_fail_first_ts = 0.0
                return r.json()
            if r.status_code == 429:
                retry_after = int(r.headers.get("Retry-After", 1))
                cprint(f"[증권사 서버 속도 제한] {retry_after}초 기다린 후 재시도할게요", Fore.YELLOW)
                time.sleep(retry_after)
        except Exception as e:
            cprint(f"[증권사 서버 연결 오류 {attempt+1}회] {e}", Fore.RED)
            time.sleep(2)
    _api_fail_count += 1
    if _api_fail_count == 1:
        _api_fail_first_ts = time.time()  # 첫 실패 시각 기록
    elapsed = time.time() - _api_fail_first_ts
    if elapsed >= 30:  # 30초 이상 연속 실패 시 즉시 알림
        send_msg(
            f"🚨 KIS 서버 연결 실패 ({int(elapsed)}초째 연속 오류)\n→ 인터넷 연결을 확인하세요.",
            level="critical"
        )
        _api_fail_first_ts = time.time()  # 알림 후 타이머 리셋 (중복 알림 방지)
    return None

_token, _token_time  = None, 0
_token_fail_count    = 0
_TOKEN_FAIL_ALERT    = 3

def get_token():
    global _token, _token_time, _token_fail_count
    if _token and (time.time() - _token_time) < 3500:
        return _token
    res = api_call("post", f"{_cfg['prod']}/oauth2/tokenP", data=json.dumps({
        "grant_type": "client_credentials",
        "appkey":     _cfg['my_app'],
        "appsecret":  _cfg['my_sec']
    }))
    if res and "access_token" in res:
        is_renewal        = _token is not None
        _token            = res["access_token"]
        _token_time       = time.time()
        _token_fail_count = 0
        if is_renewal:
            send_msg("🔑 KIS 토큰 갱신 완료. 정상 동작 중입니다.", level="silent")
        return _token
    _token_fail_count += 1
    cprint(f"[로그인 실패] KIS 인증 오류 ({_token_fail_count}회 연속)", Fore.RED)
    if _token_fail_count >= _TOKEN_FAIL_ALERT:
        send_msg(
            f"🚨 KIS 로그인 실패 ({_token_fail_count}회 연속)\n→ 봇을 재시작해 주세요.",
            level="critical"
        )
    return None

def kis_headers(tr_id):
    t = get_token()
    if not t: return None
    return {
        "authorization": f"Bearer {t}",
        "appkey":        _cfg['my_app'],
        "appsecret":     _cfg['my_sec'],
        "tr_id":         tr_id,
        "content-type":  "application/json"
    }

# ============================================================
# [7] 상태 저장 / 불러오기
# ============================================================


def send_screen(caption_suffix=""):
    """화면 캡처 → JPEG 압축(최소 용량) → 텔레그램 전송"""
    try:
        from PIL import ImageGrab, Image
        import io
        img = ImageGrab.grab()
        # 50% 축소 (항상 — 용량 최소화)
        w, h = img.size
        img = img.resize((w // 2, h // 2), Image.LANCZOS)
        # JPEG quality=50 → 보통 80~150 KB
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=50, optimize=True)
        buf.seek(0)
        label = datetime.now().strftime("%H:%M:%S")
        caption = f"🖥️ [{label}]{' ' + caption_suffix if caption_suffix else ''}"
        import requests as _rq
        res = _rq.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
            data={"chat_id": CHAT_ID, "caption": caption},
            files={"photo": ("screen.jpg", buf, "image/jpeg")},
            timeout=20
        )
        return res.status_code == 200
    except ImportError:
        send_msg(
            "❌ Pillow 라이브러리가 없어요.\n"
            "  pip install pillow",
            level="normal", force=True
        )
        return False
    except Exception as e:
        send_msg(f"❌ 화면 캡처 실패: {e}", level="normal", force=True)
        return False

def log_change(category, detail):
    """변경 사항을 changelog/ 폴더에 날짜별 txt 파일로 자동 기록"""
    today = str(date.today())
    filepath = os.path.join(CHANGELOG_DIR, f"changelog_{today}.txt")
    now_str  = datetime.now().strftime("%H:%M:%S")
    line     = f"[{now_str}] [{category}] {detail}\n"
    with open(filepath, "a", encoding="utf-8") as f:
        if not os.path.exists(filepath) or os.path.getsize(filepath) == 0:
            f.write(f"# KIS 자동매매 봇 변경 기록 — {today}\n")
            f.write("=" * 50 + "\n")
        f.write(line)

def save_state():
    with open(STATE_FILE, "w", encoding="utf-8") as f:
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
            "saved_week":          list((datetime.now().year, datetime.now().isocalendar()[1])),
            "target":      bot["target"],
            "max_loss":    bot["max_loss"],
            "drop":        bot["drop"],
            "trail_start": bot["trail_start"],
            "trail_gap":   bot["trail_gap"],
            "be_trigger":  bot["be_trigger"],
            "rsi_buy":     bot["rsi_buy"],
            "stock_code":  STOCK_CODE,
            "date":        str(date.today())
        }, f, ensure_ascii=False, indent=2)

def load_state():
    global daily_pnl_krw, weekly_pnl_krw, trade_count, highest_profit, _last_sell_time
    global DAILY_LOSS_BASE_KRW, ORDER_BUDGET_KRW
    global win_count, loss_count, consecutive_loss, consecutive_win
    global _buy_time, STOCK_CODE, _weekly_stop
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
    # 주간 정지 플래그 복원 (재시작 후에도 주간 정지 유지)
    _weekly_stop        = s.get("_weekly_stop",        False)
    # 저장된 종목 코드가 있으면 프리셋 복원
    saved_code = s.get("stock_code")
    if saved_code and saved_code != STOCK_CODE:
        apply_stock_preset(saved_code)
        cprint(f"[상태 복원] 종목 코드 복원: {saved_code}", Fore.CYAN)
    if s.get("date") != str(date.today()):
        return
    bot.update({
        "has_stock":  s.get("has_stock",  False),
        "buy_price":  s.get("buy_price",  0),
        "filled_qty": s.get("filled_qty", 0),
        "be_active":  s.get("be_active",  False),
        "prev_rsi":   s.get("prev_rsi",   None),
        "prev_rsi2":  s.get("prev_rsi2",  None),
    })
    _buy_time        = s.get("buy_time",        0.0)
    daily_pnl_krw    = s.get("daily_pnl_krw",   0)
    # weekly_pnl_krw는 check_weekly_reset()이 판단하기 전에 덮어쓰지 않도록
    # 같은 주(week)일 때만 복원
    saved_week = s.get("saved_week")
    from datetime import datetime as _dt
    now = _dt.now()
    current_week = (now.year, now.isocalendar()[1])
    if saved_week and tuple(saved_week) == current_week:
        weekly_pnl_krw = s.get("weekly_pnl_krw", 0)
    trade_count      = s.get("trade_count",      0)
    highest_profit   = s.get("highest_profit",   0.0)
    win_count        = s.get("win_count",         0)
    loss_count       = s.get("loss_count",        0)
    consecutive_loss = s.get("consecutive_loss",  0)
    consecutive_win  = s.get("consecutive_win",   0)
    _last_sell_time  = s.get("_last_sell_time",   0.0)

def log_trade(side, price, qty, pnl_krw=0, rsi=None, vwap=None, ma20=None, ma60=None, reason="",
              vol_pct=None, vol_ratio=None, drop_pct=None, divergence_ok=None,
              pos_hold_time=None, buy_price_ref=None):
    """[45] CSV 컬럼 확장: rsi, vwap, ma20, ma60, reason + 분석용 컬럼 추가."""
    row_data = [
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        side, price, qty,
        round(pnl_krw, 0),
        round(daily_pnl_krw, 0),
        round(rsi,  2) if rsi  is not None else "",
        round(vwap, 0) if vwap is not None else "",
        round(ma20, 0) if ma20 is not None else "",
        round(ma60, 0) if ma60 is not None else "",
        reason,
        round(vol_pct,   4) if vol_pct   is not None else "",
        round(vol_ratio, 2) if vol_ratio  is not None else "",
        round(drop_pct,  4) if drop_pct   is not None else "",
        int(divergence_ok)  if divergence_ok is not None else "",
        round(pos_hold_time, 1) if pos_hold_time is not None else "",
        round(buy_price_ref, 0) if buy_price_ref is not None else "",
    ]
    header = ["datetime", "side", "price", "qty", "pnl_krw", "daily_pnl_krw",
              "rsi", "vwap", "ma20", "ma60", "reason",
              "vol_pct", "vol_ratio", "drop_pct", "divergence_ok",
              "pos_hold_time", "buy_price_ref"]
    # 메인 로그
    write_header = not os.path.exists(LOG_FILE)
    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(header)
        w.writerow(row_data)
    # shared 폴더 동기화 (통합 리포트용)
    try:
        write_header2 = not os.path.exists(KIS_SHARED_LOG)
        with open(KIS_SHARED_LOG, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if write_header2:
                w.writerow(header)
            w.writerow(row_data)
    except Exception as e:
        cprint(f"[shared 로그 오류] {e}", Fore.YELLOW)

# ── indicator_log: 매 루프 지표 기록 ──────────────────────────
INDICATOR_LOG_FILE = os.path.join(BASE_DIR, "indicator_log.csv")
_indicator_log_counter = 0
INDICATOR_LOG_INTERVAL = 10  # N루프마다 1회 기록 (1초 루프 기준 10초마다)

def log_indicator(price, rsi, ma20, ma60, vwap, vol_pct, vol_ratio, drop_pct):
    """매 루프 지표를 indicator_log.csv에 기록 (분석용)."""
    global _indicator_log_counter
    _indicator_log_counter += 1
    if _indicator_log_counter % INDICATOR_LOG_INTERVAL != 0:
        return
    write_header = not os.path.exists(INDICATOR_LOG_FILE)
    try:
        with open(INDICATOR_LOG_FILE, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if write_header:
                w.writerow(["datetime", "price", "rsi", "ma20", "ma60", "vwap",
                            "vol_pct", "vol_ratio", "drop_pct"])
            w.writerow([
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                price,
                round(rsi,  2) if rsi  is not None else "",
                round(ma20, 0) if ma20 is not None else "",
                round(ma60, 0) if ma60 is not None else "",
                round(vwap, 0) if vwap is not None else "",
                round(vol_pct,   4) if vol_pct   is not None else "",
                round(vol_ratio, 2) if vol_ratio  is not None else "",
                round(drop_pct,  4) if drop_pct   is not None else "",
            ])
    except Exception as e:
        cprint(f"[indicator_log 오류] {e}", Fore.YELLOW)

# ============================================================
# [8] 날짜별 자동 백업
# ============================================================
def do_daily_backup(backup_date_str):
    for src, name in [
        (LOG_FILE,   f"trade_log_{backup_date_str}.csv"),
        (STATE_FILE, f"bot_state_{backup_date_str}.json"),
    ]:
        if os.path.exists(src):
            dst = os.path.join(BACKUP_DIR, name)
            try:
                shutil.copy2(src, dst)
                cprint(f"[백업 완료] {dst}", Fore.CYAN)
            except Exception as e:
                cprint(f"[백업 실패] {e}", Fore.RED)

    cutoff = date.today() - timedelta(days=BACKUP_KEEP_DAYS)
    for fname in os.listdir(BACKUP_DIR):
        try:
            date_part = fname.replace("trade_log_","").replace("bot_state_","").replace(".csv","").replace(".json","")
            if datetime.strptime(date_part, "%Y%m%d").date() < cutoff:
                os.remove(os.path.join(BACKUP_DIR, fname))
                cprint(f"[오래된 백업 삭제] {fname}", Fore.MAGENTA)
        except:
            pass

# ============================================================
# [9] 지표 계산
# ============================================================
def get_tick_size(price, code):
    if int(code) >= 200000: return 5
    if price < 2000:        return 1
    if price < 5000:        return 5
    if price < 20000:       return 10
    if price < 50000:       return 50
    if price < 200000:      return 100
    if price < 500000:      return 500
    return 1000

def get_price_and_volume(code):
    h = kis_headers("FHKST01010100")
    if not h: return 0, 0
    res = api_call(
        "get",
        f"{_cfg['prod']}/uapi/domestic-stock/v1/quotations/inquire-price",
        headers=h,
        params={"fid_cond_mrkt_div_code": "J", "fid_input_iscd": code}
    )
    try:
        price  = int(res["output"]["stck_prpr"])
        volume = int(res["output"].get("acml_vol", 0))
        return price, volume
    except:
        return 0, 0

def get_price(code):
    p, _ = get_price_and_volume(code)
    return p

def get_balance_krw():
    h = kis_headers("TTTC8908R")
    if not h: return 0
    res = api_call(
        "get",
        f"{_cfg['prod']}/uapi/domestic-stock/v1/trading/inquire-psbl-order",
        headers=h,
        params={
            "CANO":         _cfg['my_acct_stock'],
            "ACNT_PRDT_CD": _cfg['my_prod'],
            "PDNO":         STOCK_CODE,
            "ORD_UNPR":     "0",
            "ORD_DVSN":     "01",
            "CMA_EVLU_AMT_ICLD_YN": "N",
            "OVRS_ICLD_YN": "N"
        }
    )
    try:    return int(res["output"]["ord_psbl_cash"])
    except: return 0

def get_atr_recommendation(code):
    h = kis_headers("FHKST01010400")
    if not h: return None
    res = api_call(
        "get",
        f"{_cfg['prod']}/uapi/domestic-stock/v1/quotations/inquire-daily-price",
        headers=h,
        params={
            "fid_cond_mrkt_div_code": "J",
            "fid_input_iscd":         code,
            "fid_period_div_code":    "D",
            "fid_org_adj_prc":        "1"
        }
    )
    try:
        prices  = res["output2"]
        changes = []
        for i in range(min(len(prices) - 1, 20)):
            high      = int(prices[i]["stck_hgpr"])
            low       = int(prices[i]["stck_lwpr"])
            pre_close = int(prices[i + 1]["stck_clpr"])
            tr = max(high - low, abs(high - pre_close), abs(low - pre_close))
            changes.append(tr / pre_close * 100)
        atr = np.mean(changes)
        return {
            "target":   round(atr * 0.8, 1),
            "drop":     round(atr * 1.0, 1),
            "max_loss": round(-(atr * 3.5), 1),
        }
    except:
        return None

def calc_order_qty(price):
    if price <= 0: return 0
    balance = get_balance_krw()
    if balance < 0:  # API 실패 시 get_balance_krw가 0 반환 → 구별 불가하므로 재시도
        balance = get_balance_krw()
    if balance == 0:
        # 진짜 잔고 부족인지 API 실패인지 알 수 없음 → 경고 후 0 반환
        cprint("[잔고 조회] 잔고 0 또는 API 실패 — 매수 건너뜀", Fore.YELLOW)
        return 0
    usable  = min(ORDER_BUDGET_KRW, balance) * ORDER_BUDGET_RATIO
    return max(int(usable / price), 0)

def calc_rsi(prices, period=14):
    if len(prices) < period + 1: return None
    p      = np.array(list(prices)[-(period + 100):])  # smoothing 수렴을 위해 충분한 데이터 사용
    deltas = np.diff(p)
    if len(deltas) < period: return None
    gains  = np.where(deltas > 0,  deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i])  / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0 and avg_gain == 0: return 50.0
    if avg_loss == 0:                   return 100.0
    return round(100 - (100 / (1 + avg_gain / avg_loss)), 2)

def calc_vol_pct(window_sec=VOL_WINDOW_SEC):
    cutoff = time.time() - window_sec
    recent = [p for ts, p in timed_prices if ts >= cutoff]
    if len(recent) >= 2 and min(recent) > 0:
        return round((max(recent) - min(recent)) / min(recent) * 100, 4)
    return None

def net_diff_krw(buy_price, sell_price, qty):
    if buy_price == 0 or qty == 0: return 0
    gross = (sell_price - buy_price) * qty
    fee   = (buy_price + sell_price) * qty * FEE_RATE
    return gross - fee

def is_daily_loss_exceeded():
    # MAX_DAILY_LOSS_KRW는 음수(-50000), MAX_DAILY_LOSS_PCT도 음수(-2.0)
    # 둘 중 덜 손해(절댓값 작은) 쪽을 한도로 사용
    pct_limit = DAILY_LOSS_BASE_KRW * (MAX_DAILY_LOSS_PCT / 100)  # 음수
    threshold = max(MAX_DAILY_LOSS_KRW, pct_limit)                # 더 완화된 쪽
    return daily_pnl_krw <= threshold

# ── VWAP ─────────────────────────────────────────────────────
def update_vwap(price, volume):
    global _vwap_pv_sum, _vwap_vol_sum, _vwap_value
    if volume <= 0 or price <= 0:
        return _vwap_value
    _vwap_pv_sum  += price * volume
    _vwap_vol_sum += volume
    if _vwap_vol_sum > 0:
        _vwap_value = _vwap_pv_sum / _vwap_vol_sum
    return _vwap_value

def reset_vwap():
    global _vwap_pv_sum, _vwap_vol_sum, _vwap_value
    _vwap_pv_sum = _vwap_vol_sum = _vwap_value = 0.0

# ── 거래량 비율 ──────────────────────────────────────────────
def calc_volume_ratio(current_volume):
    if len(volume_history) < 10: return None
    avg_vol = np.mean(list(volume_history))
    if avg_vol == 0: return None
    return round(current_volume / avg_vol, 2)

# ============================================================
# [10] 5분 기반 RSI 다이버전스
# ============================================================
def update_5m_trough(now_ts, price, rsi):
    _price_trough_5m.append((now_ts, price, rsi))
    if len(_price_trough_5m) > 2:
        _price_trough_5m.pop(0)

def check_5m_divergence():
    if len(_price_trough_5m) < 2:
        return False  # 데이터 부족 → 확인 불가 → False (거짓 신호 방지)
    _, price1, rsi1 = _price_trough_5m[0]
    _, price2, rsi2 = _price_trough_5m[1]
    return (price2 < price1) and (rsi2 > rsi1)

# ============================================================
# [11] 주문 시스템
# ============================================================
def cancel_order(order_no, qty):
    h = kis_headers("TTTC0803U")
    if not h: return False
    body = {
        "CANO":                _cfg['my_acct_stock'],
        "ACNT_PRDT_CD":        _cfg['my_prod'],
        "KRX_FWDG_ORD_ORGNO": "",
        "ORGN_ODNO":           order_no,
        "ORD_DVSN":            "00",
        "RVSE_CNCL_DVSN_CD":   "02",
        "ORD_QTY":             str(qty),
        "ORD_UNPR":            "0",
        "QTY_ALL_ORD_YN":      "Y"
    }
    res = api_call(
        "post",
        f"{_cfg['prod']}/uapi/domestic-stock/v1/trading/order-rvsecncl",
        headers=h, data=json.dumps(body)
    )
    if res and res.get("rt_cd") == "0":
        send_msg("ℹ️ 미체결 주문 자동 취소 완료.", level="silent")
        return True
    send_msg("🚨 미체결 주문 취소 실패!\n→ KIS 앱에서 직접 확인해 주세요.", level="critical")
    return False

def confirm_order(order_no, requested_qty, retry=6):
    if not order_no: return 0, 0
    h     = kis_headers("TTTC8001R")
    today = datetime.now().strftime("%Y%m%d")
    time.sleep(1.0)
    for attempt in range(retry):
        res = api_call(
            "get",
            f"{_cfg['prod']}/uapi/domestic-stock/v1/trading/inquire-daily-ccld",
            headers=h,
            params={
                "CANO":            _cfg['my_acct_stock'],
                "ACNT_PRDT_CD":    _cfg['my_prod'],
                "INQR_STRT_DT":    today,
                "INQR_END_DT":     today,
                "SLL_BUY_DVSN_CD": "00",
                "INQR_DVSN":       "00",
                "ODNO":            order_no,
                "INQR_DVSN_3":     "00",
                "CTX_AREA_FK100":  "",
                "CTX_AREA_NK100":  ""
            }
        )
        if res and res.get("output1"):
            item   = res["output1"][0]
            filled = int(item.get("tot_ccld_qty", 0))
            avg_p  = float(item.get("avg_prvs", 0))
            if filled >= requested_qty:
                return filled, avg_p
        time.sleep(1.5 if attempt < 2 else 2.5)
    send_msg("⚠️ 주문 체결 확인 안 됨. 자동 취소 시도.", level="critical")
    cancel_order(order_no, requested_qty)
    return 0, 0

def send_order(side, code, qty, ref_price=0):
    h = kis_headers("TTTC0802U" if side == "BUY" else "TTTC0801U")
    if not h: return 0, 0
    ord_dvsn    = "00" if ref_price > 0 else "01"
    limit_price = ref_price if ord_dvsn == "00" else 0
    body = {
        "CANO":         _cfg['my_acct_stock'],
        "ACNT_PRDT_CD": _cfg['my_prod'],
        "PDNO":         code,
        "ORD_DVSN":     ord_dvsn,
        "ORD_QTY":      str(qty),
        "ORD_UNPR":     str(limit_price)
    }
    res = api_call(
        "post",
        f"{_cfg['prod']}/uapi/domestic-stock/v1/trading/order-cash",
        headers=h, data=json.dumps(body)
    )
    if not res or res.get("rt_cd") != "0": return 0, 0
    return confirm_order(res.get("output", {}).get("ODNO", ""), qty)

def do_sell(price, reason, retry=2):
    global daily_pnl_krw, weekly_pnl_krw, trade_count, highest_profit, _last_sell_time
    global consecutive_loss, consecutive_win, win_count, loss_count, _buy_time
    global ORDER_BUDGET_KRW, _daily_report_sent
    rsi  = bot.get("_last_rsi")
    vwap = _vwap_value if _vwap_value else None
    ma20 = bot.get("_ma20")
    ma60 = bot.get("_ma60")
    for attempt in range(retry):
        filled, avg_p = send_order("SELL", STOCK_CODE, bot["filled_qty"], ref_price=price)
        if filled > 0:
            actual_sell    = avg_p if avg_p > 0 else price
            pnl_krw        = net_diff_krw(bot["buy_price"], actual_sell, filled)
            daily_pnl_krw += pnl_krw
            weekly_pnl_krw += pnl_krw  # [40] 주간 누적
            if pnl_krw >= 0:
                win_count += 1
                consecutive_loss = 0
                consecutive_win  += 1
                # [42] 연패 해소 → RSI 원래 기준 복원
                if bot["rsi_buy"] < RSI_BUY_DEFAULT:
                    bot["rsi_buy"] = RSI_BUY_DEFAULT
                    send_msg(
                        f"✅ 연패 해소! RSI 기준 복원 → {RSI_BUY_DEFAULT}",
                        level="normal"
                    )
                # [신규] 연승 시 예산 자동 확대
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
                        save_state()  # 예산 확대 즉시 저장
            else:
                loss_count += 1
                consecutive_loss += 1
                consecutive_win  = 0  # 연승 리셋
            log_change("매도", f"{STOCK_CODE} {price:,}원 × {bot['filled_qty']}주  손익:{pnl_krw:+,.0f}원  [{reason}]")
            trade_count += 1
            _daily_report_sent = False  # 오늘 거래 발생 → 마감 시 요약 발송 허용
            bot.update({"has_stock": False, "filled_qty": 0, "be_active": False})
            highest_profit  = 0.0
            _last_sell_time = time.time()
            _buy_time       = 0.0   # [41] 타임아웃 초기화
            save_state()
            log_trade("SELL", actual_sell, filled, pnl_krw,
                      rsi=rsi, vwap=vwap, ma20=ma20, ma60=ma60, reason=reason)  # [45]

            reason_kr = {
                "목표 익절":          "🎯 목표 수익 달성",
                "최대 손절":          "🔻 손절 기준 도달",
                "트레일링 스탑":      "📉 고점 대비 하락",
                "본절 보호":          "🛡️ 손해 방지",
                "장마감 강제청산":    "🕒 장 마감",
                "일일 최대손실 도달": "🛑 일일 손실 한도",
                "슬리피지 초과":      "⚠️ 슬리피지 초과",
                "일일 손실 셧다운":   "🚨 일일 손실 셧다운",
                "포지션 타임아웃":    "⏱️ 시간 초과 청산",
            }.get(reason, reason)

            send_msg(
                f"✅ 팔았어요! [{reason_kr}]\n"
                f"판 가격  : {actual_sell:,.0f}원\n"
                f"이번 손익: {pnl_krw:+,.0f}원\n"
                f"오늘 누적: {daily_pnl_krw:+,.0f}원\n"
                f"주간 누적: {weekly_pnl_krw:+,.0f}원\n"  # [40]
                f"오늘 거래: {trade_count}회 (승 {win_count} / 패 {loss_count})",
                level="critical"
            )
            # [42] 연패 시 RSI 기준 강화
            if consecutive_loss >= CONSEC_LOSS_ALERT:
                new_rsi = max(bot["rsi_buy"] - RSI_TIGHTEN_STEP, RSI_BUY_MIN)
                if new_rsi < bot["rsi_buy"]:
                    bot["rsi_buy"] = new_rsi
                    send_msg(
                        f"⚠️ 연속 {consecutive_loss}번 손해!\n"
                        f"→ RSI 기준 자동 강화: {bot['rsi_buy'] + RSI_TIGHTEN_STEP} → {bot['rsi_buy']}\n"
                        f"→ /stop 으로 봇을 멈출 수도 있어요.",
                        level="critical"
                    )
                else:
                    send_msg(
                        f"⚠️ 연속 {consecutive_loss}번 손해!\n"
                        f"→ RSI 기준이 이미 최소값({RSI_BUY_MIN})이에요.\n"
                        f"→ /stop 으로 봇을 멈출 수 있어요.",
                        level="critical"
                    )
            return True
        time.sleep(2)
    send_msg(
        f"🚨 매도 실패! [{reason}]\n→ KIS 앱에서 {bot['filled_qty']}주 직접 매도해 주세요.\n현재가: {price:,.0f}원",
        level="critical"
    )
    return False


def do_buy(price, reason, retry=2):
    """매수 주문 실행 + 알림 (목표가·손절가 실제 가격 표시)"""
    global _order_pending, _buy_time
    if _order_pending:
        cprint("[중복 주문 방지] 이미 주문 진행 중입니다.", Fore.YELLOW)
        return False
    _order_pending = True
    try:
      qty = calc_order_qty(price)
      if qty < 1:
        send_msg(
            f"ℹ️ 매수 신호! 잔고 부족으로 건너뜀\n"
            f"현재가: {price:,.0f}원 / 예산: {ORDER_BUDGET_KRW:,.0f}원",
            level="silent"
        )
        return False

      tick         = get_tick_size(price, STOCK_CODE)
      target_price = price + tick

      for attempt in range(retry):
        filled, avg_p = send_order("BUY", STOCK_CODE, qty, ref_price=target_price)
        if filled > 0:
            actual_buy = avg_p if avg_p > 0 else target_price
            slippage   = abs(actual_buy - price) / price * 100
            _buy_time  = time.time()  # 체결 시점 즉시 기록

            bot.update({"has_stock": True, "buy_price": actual_buy,
                        "filled_qty": filled, "be_active": False})

            if slippage > MAX_SLIPPAGE_PCT:
                send_msg(
                    f"⚠️ 슬리피지 초과 ({slippage:.2f}%)\n→ 즉시 매도합니다.",
                    level="critical"
                )
                sell_ok = do_sell(actual_buy, "슬리피지 초과")
                if not sell_ok:
                    send_msg(
                        f"🚨 슬리피지 초과 후 즉시 매도 실패!\n"
                        f"→ KIS 앱에서 {bot['filled_qty']}주 직접 매도해 주세요.\n"
                        f"매수가: {actual_buy:,.0f}원",
                        level="critical"
                    )
                return False

            # 목표가·손절가 실제 가격 계산
            target_krw   = actual_buy * (1 + bot["target"]   / 100)
            stoploss_krw = actual_buy * (1 + bot["max_loss"]  / 100)

            log_change("매수", f"{STOCK_CODE} {actual_buy:,}원 × {filled}주  [{reason}]")
            log_trade("BUY", actual_buy, filled,
                      rsi=bot.get("_last_rsi"), vwap=_vwap_value,
                      ma20=bot.get("_ma20"), ma60=bot.get("_ma60"), reason=reason)
            send_msg(
                f"🛒 매수 완료! [{reason}]\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"가격   : {actual_buy:,.0f}원\n"
                f"수량   : {filled}주\n"
                f"투자금 : {actual_buy * filled:,.0f}원\n"
                f"─────────────────\n"
                f"🎯 목표가 : {target_krw:,.0f}원  (+{bot['target']}%)\n"
                f"🔻 손절가 : {stoploss_krw:,.0f}원  ({bot['max_loss']}%)\n"
                f"⏱️ {POS_TIMEOUT_MIN}분 후 미결 시 자동 청산",
                level="critical"
            )
            save_state()
            return True
        time.sleep(2)

      send_msg(
        f"🚨 매수 실패! [{reason}]\n→ KIS 앱에서 확인해 주세요.\n현재가: {price:,.0f}원",
        level="critical"
      )
      return False
    finally:
        _order_pending = False

# ============================================================
# [12] 텔레그램 명령 처리
# ============================================================

def poll_sleep(seconds):
    """sleep 중에도 텔레그램 명령을 3초 간격으로 처리"""
    end = time.time() + seconds
    while time.time() < end:
        poll_telegram()
        remaining = end - time.time()
        if remaining <= 0: break
        time.sleep(min(3, remaining))

_tg_fail_count = 0
_tg_fail_alert_threshold = 10

def poll_telegram():
    global last_update_id, _last_tg_poll, _tg_fail_count
    if time.time() - _last_tg_poll < 3: return
    _last_tg_poll = time.time()
    try:
        res = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
            params={"offset": last_update_id + 1, "timeout": 1},
            timeout=5
        ).json()
        _tg_fail_count = 0  # 성공 시 리셋
        for upd in res.get("result", []):
            last_update_id = upd["update_id"]

            # ── 일반 텍스트 메시지 ──────────────────────────
            msg = upd.get("message", {})
            if str(msg.get("chat", {}).get("id", "")) == str(CHAT_ID):
                handle_command(msg.get("text", "").strip())

            # ── 인라인 버튼 콜백 ────────────────────────────
            cb = upd.get("callback_query", {})
            if cb and str(cb.get("from", {}).get("id", "")) == str(CHAT_ID):
                try:
                    requests.post(
                        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery",
                        json={"callback_query_id": cb["id"]},
                        timeout=3
                    )
                except:
                    pass
                handle_command(cb.get("data", "").strip())
    except Exception as e:
        _tg_fail_count += 1
        cprint(f"[텔레그램 폴링 오류 {_tg_fail_count}회] {e}", Fore.YELLOW)
        if _tg_fail_count >= _tg_fail_alert_threshold:
            cprint(f"🚨 텔레그램 연속 {_tg_fail_count}회 실패 — 네트워크 확인 필요", Fore.RED, bright=True)
            _tg_fail_count = 0  # 알림 후 리셋

def handle_command(text):
    global DAILY_LOSS_BASE_KRW, ORDER_BUDGET_KRW
    if not text.startswith("/") and not text.startswith("!"): return

    # ── 명령어 정규화 ────────────────────────────────────────
    # 대소문자 무시, 한글·단축키·별칭 → 표준 명령어로 변환
    CMD_ALIAS = {
        # 상태
        "/s":         "/status",
        "/st":        "/status",
        "/상태":      "/status",
        "/현재":      "/status",
        "/현재상태":  "/status",
        # 리포트
        "/r":         "/report",
        "/결과":      "/report",
        "/오늘":      "/report",
        "/오늘결과":  "/report",
        "/리포트":    "/report",
        # 주간
        "/w":         "/weekly",
        "/주간":      "/weekly",
        "/주간손익":  "/weekly",
        "/이번주":    "/weekly",
        # 로그
        "/l":         "/log",
        "/내역":      "/log",
        "/거래내역":  "/log",
        "/로그":      "/log",
        # 잔고
        "/b":         "/balance",
        "/잔고":      "/balance",
        "/계좌":      "/balance",
        "/돈":        "/balance",
        # 홀드
        "/h":         "/hold",
        "/보유":      "/hold",
        "/수동":      "/hold",
        "/수동등록":  "/hold",
        # 종목
        "/종목":      "/stock",
        "/종목변경":  "/stock",
        "/바꿔":      "/stock",
        # 시작
        "/시작":      "/start",
        "/켜":        "/start",
        "/켜줘":      "/start",
        "/on":        "/start",
        # 정지
        "/stop":      "/stop",
        "/정지":      "/stop",
        "/멈춰":      "/stop",
        "/꺼":        "/stop",
        "/꺼줘":      "/stop",
        "/off":       "/stop",
        # 시뮬
        "/sim":       "/sim",
        "/시뮬":      "/sim",
        "/시뮬레이션":"/sim",
        # 리로드
        "/reload":    "/reload",
        "/리로드":    "/reload",
        "/재로딩":    "/reload",
        "/설정":      "/reload",
        # 리스크
        "/risk":      "/risk",
        "/손실한도":  "/risk",
        "/한도":      "/risk",
        # 예산
        "/budget":    "/budget",
        "/예산":      "/budget",
        # set
        "/set":       "/set",
        "/변경":      "/set",
        "/수치":      "/set",
        # 도움말
        "/help":      "/help",
        "/도움":      "/help",
        "/도움말":    "/help",
        "/명령어":    "/help",
        "/뭐있어":    "/help",
        "/뭐":        "/help",
        # 메뉴
        "/menu":      "/menu",
        "/메뉴":      "/menu",
        # 스크린샷
        "/screen":    "/screen",
        "/스크린":    "/screen",
        "/캡처":      "/screen",
        "/화면":      "/screen",
        # 일시정지
        "/pause":     "/pause",
        "/잠깐":      "/pause",
        "/일시정지":  "/pause",
        "/쉬어":      "/pause",
        # weekly
        "/weekly":    "/weekly",
    }

    parts    = text.strip().split()
    raw_cmd  = parts[0].lower()
    std_cmd  = CMD_ALIAS.get(raw_cmd, raw_cmd)   # 별칭 → 표준
    cmd      = [std_cmd] + parts[1:]              # 표준 명령 + 나머지 인자
    text     = " ".join(cmd)                      # text도 정규화된 버전으로 교체

    if cmd[0] == "/status":
        # API 호출 없이 캐시된 값으로 즉시 응답
        price     = _last_price if _last_price else bot["buy_price"]
        pnl_now   = net_diff_krw(bot["buy_price"], price, bot["filled_qty"]) if bot["has_stock"] else 0
        limit_krw = min(abs(MAX_DAILY_LOSS_KRW),
                        abs(DAILY_LOSS_BASE_KRW * (MAX_DAILY_LOSS_PCT / 100)))
        cpu      = psutil.cpu_percent(interval=None)
        ram      = psutil.virtual_memory().percent
        ma20     = bot.get("_ma20")
        ma60     = bot.get("_ma60")
        ma20_str = f"{ma20:.0f}" if ma20 else "N/A"
        ma60_str = f"{ma60:.0f}" if ma60 else "N/A"
        rsi_str  = f"{bot.get('_last_rsi', 0):.1f}" if bot.get("_last_rsi") else "N/A"
        send_msg(
            f"📊 현재 상태\n"
            f"봇 가동중 : {'네 ▶️' if bot['is_running'] else '멈춤 ⏸️'}\n"
            f"주식 보유 : {'네 🟢' if bot['has_stock'] else '아니요 ⚪'}\n"
            f"매수가    : {bot['buy_price']:,.0f}원\n"
            f"현재가    : {price:,.0f}원\n"
            f"RSI       : {rsi_str}\n"
            f"VWAP      : {_vwap_value:,.0f}원\n"
            f"MA20/60   : {ma20_str} / {ma60_str}\n"
            f"현재 손익 : {pnl_now:+,.0f}원\n"
            f"오늘 누적 : {daily_pnl_krw:+,.0f}원\n"
            f"손실 한도 : -{limit_krw:,.0f}원\n"
            f"1회 예산  : {ORDER_BUDGET_KRW:,.0f}원\n"
            f"오늘 거래 : {trade_count}회 (승 {win_count} / 패 {loss_count})\n"
            f"안전장치  : {'켜짐 🛡️' if bot['be_active'] else '꺼짐'}\n"
            f"─────────────────\n"
            f"CPU: {cpu:.0f}%  RAM: {ram:.0f}%",
            level="normal",
            keyboard=KB_HOLDING if bot["has_stock"] else KB_MAIN
        , force=True)


    elif cmd[0] == "/screen":
        send_msg("📸 화면 캡처 중...", level="normal", force=True)
        send_screen("수동 요청")

    elif cmd[0] == "/menu":
        send_menu()

    elif cmd[0] == "/balance":
        send_msg("💰 잔고 조회 중...", level="normal", force=True)
        try:
            balance = get_balance_krw()
            send_msg(
                f"💰 현재 계좌 잔고: {balance:,.0f}원",
                level="normal",
                keyboard=KB_MAIN
            , force=True)
        except Exception as e:
            send_msg(f"❌ 잔고 조회 실패: {e}", level="normal", force=True)


    elif cmd[0] == "/pause":
        if len(cmd) < 2:
            send_msg(
                "⏸️ 일시 정지 방법\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "/pause 분수\n\n"
                "예) /pause 30  → 30분 후 자동 재개\n"
                "예) /pause 0   → 수동 정지 (/start 로 재개)",
                level="normal"
            )
        else:
            try:
                minutes = int(cmd[1])
                log_change("봇제어", f"일시정지 {minutes}분 (/pause)")
                bot["is_running"] = False
                if minutes > 0:
                    import threading
                    def _auto_resume():
                        time.sleep(minutes * 60)
                        if not bot["is_running"]:
                            bot["is_running"] = True
                            send_msg(
                                f"▶️ {minutes}분 경과! 자동으로 매매를 재개해요.",
                                level="normal"
                            , force=True)
                    threading.Thread(target=_auto_resume, daemon=True).start()
                    send_msg(
                        f"⏸️ {minutes}분간 일시 정지해요.\n"
                        f"→ {minutes}분 후 자동으로 재개돼요.\n"
                        f"→ 지금 바로 재개하려면 /start",
                        level="normal",
                        keyboard=[[{"text": "▶️ 지금 재개", "callback_data": "/start"}]]
                    , force=True)
                else:
                    send_msg(
                        "⏸️ 수동 정지했어요.\n→ /start 로 재개하세요.",
                        level="normal",
                        keyboard=[[{"text": "▶️ 재개", "callback_data": "/start"}]]
                    , force=True)
            except ValueError:
                send_msg("❌ 숫자로 입력해 주세요.\n예) /pause 30", level="normal")

    elif cmd[0] == "/stop":
        log_change("봇제어", "정지 (/stop)")
        bot["is_running"] = False
        send_msg(
            "⏸️ 봇을 멈췄어요.",
            level="normal",
            keyboard=[[{"text": "▶️ 다시 시작", "callback_data": "/start"},
                       {"text": "📊 상태 확인", "callback_data": "/status"}]]
        , force=True)

    elif cmd[0] == "/start":
        log_change("봇제어", "시작 (/start)")
        bot["is_running"] = True
        if bot["rsi_buy"] < RSI_BUY_DEFAULT:
            bot["rsi_buy"] = RSI_BUY_DEFAULT
            send_msg(
                f"▶️ 봇이 다시 매매를 시작해요!\nRSI 기준도 기본값({RSI_BUY_DEFAULT})으로 복원했어요.",
                level="normal",
                keyboard=KB_MAIN
            , force=True)
        else:
            send_msg(
                "▶️ 봇이 다시 매매를 시작해요!",
                level="normal",
                keyboard=KB_MAIN
            , force=True)

    elif cmd[0] == "/set":
        # 한글 항목명 → 영어 키 변환
        SET_KR_ALIAS = {
            "익절":      "target",
            "목표":      "target",
            "익절목표":  "target",
            "손절":      "max_loss",
            "손절기준":  "max_loss",
            "눌림":      "drop",
            "눌림기준":  "drop",
            "트레일시작":"trail_start",
            "트레일":    "trail_start",
            "트레일간격":"trail_gap",
            "간격":      "trail_gap",
            "본절":      "be_trigger",
            "본절기준":  "be_trigger",
            "rsi":       "rsi_buy",
            "RSI":       "rsi_buy",
            "rsi기준":   "rsi_buy",
        }
        if len(cmd) != 3:
            send_msg(
                "⚙️ 전략 수치 변경 방법\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "/set 항목 값\n\n"
                "📌 항목 목록\n"
                "  익절 (target)       현재: " + str(bot['target']) + "%\n"
                "  손절 (max_loss)     현재: " + str(bot['max_loss']) + "%\n"
                "  눌림 (drop)         현재: " + str(bot['drop']) + "%\n"
                "  트레일시작          현재: " + str(bot['trail_start']) + "%\n"
                "  트레일간격          현재: " + str(bot['trail_gap']) + "%\n"
                "  본절 (be_trigger)   현재: " + str(bot['be_trigger']) + "%\n"
                "  rsi                 현재: " + str(bot['rsi_buy']) + "\n\n"
                "📌 예시\n"
                "  /set 익절 1.5\n"
                "  /set 손절 -1.2\n"
                "  /set rsi 28",
                level="normal"
            , force=True)
        else:
            raw_key = cmd[1].lower()
            key = SET_KR_ALIAS.get(raw_key, SET_KR_ALIAS.get(cmd[1], raw_key))
            if key in SET_ALLOWED_KEYS:
                try:
                    val = float(cmd[2])
                    # 새로 추가된 전역변수 키 처리
                    global MAX_TRADE_COUNT, COOLDOWN_SEC, VOL_MIN_PCT, VOL_MAX_PCT
                    global MAX_SLIPPAGE_PCT, POS_TIMEOUT_MIN, VWAP_FILTER
                    if key == "trade_count":
                        MAX_TRADE_COUNT = int(val)
                        send_msg(f"✅ 하루 최대 거래횟수 변경: {MAX_TRADE_COUNT}회", level="normal", force=True)
                    elif key == "cooldown":
                        COOLDOWN_SEC = int(val)
                        send_msg(f"✅ 쿨다운 변경: {COOLDOWN_SEC}초", level="normal", force=True)
                    elif key == "vol_min":
                        VOL_MIN_PCT = val
                        send_msg(f"✅ 최소 변동성 변경: {VOL_MIN_PCT}%", level="normal", force=True)
                    elif key == "vol_max":
                        VOL_MAX_PCT = val
                        send_msg(f"✅ 최대 변동성 변경: {VOL_MAX_PCT}%", level="normal", force=True)
                    elif key == "slippage":
                        MAX_SLIPPAGE_PCT = val
                        send_msg(f"✅ 슬리피지 한도 변경: {MAX_SLIPPAGE_PCT}%", level="normal", force=True)
                    elif key == "timeout_min":
                        POS_TIMEOUT_MIN = int(val)
                        send_msg(f"✅ 포지션 타임아웃 변경: {POS_TIMEOUT_MIN}분", level="normal", force=True)
                    elif key == "vwap_filter":
                        VWAP_FILTER = bool(int(val))
                        send_msg(f"✅ VWAP 필터: {'켜짐' if VWAP_FILTER else '꺼짐'}", level="normal", force=True)
                    else:
                        bot[key] = val
                        if key in ("target", "max_loss"):
                            global _dynamic_mode
                            _dynamic_mode = False
                        send_msg(f"✅ 변경 완료!\n{key} = {cmd[2]}", level="normal", force=True)
                    log_change("설정변경", f"{key} = {cmd[2]}")
                    save_state()
                except:
                    send_msg("❌ 값은 숫자로 입력해 주세요.\n예) /set 익절 1.5", level="normal")
            else:
                send_msg(
                    f"❌ '{cmd[1]}'은 변경할 수 없는 항목이에요.\n\n"
                    f"변경 가능한 항목:\n"
                    f"  익절, 손절, 눌림, 트레일시작, 트레일간격, 본절, rsi\n"
                    f"  trade_count, cooldown, vol_min, vol_max\n"
                    f"  slippage, timeout_min, vwap_filter\n\n"
                    f"예) /set 익절 1.5  /set trade_count 30",
                    level="normal"
                )

    elif cmd[0] == "/risk":
        if len(cmd) != 2:
            send_msg(
                "⚙️ 일일 손실 한도 변경\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"현재 기준금액: {DAILY_LOSS_BASE_KRW:,.0f}원\n"
                f"현재 최대손실: {DAILY_LOSS_BASE_KRW * MAX_DAILY_LOSS_PCT / 100:,.0f}원\n\n"
                "사용법: /risk 금액\n"
                "예시 : /risk 100000  → 기준 10만원으로 변경",
                level="normal"
            , force=True)
        else:
            try:
                val = int(cmd[1].replace(",", "").replace("원", ""))
                if val < 10_000:
                    send_msg("❌ 10,000원 이상으로 입력해 주세요.\n예) /risk 100000", level="normal")
                else:
                    log_change("손실한도변경", f"DAILY_LOSS_BASE_KRW = {val:,}원")
                    DAILY_LOSS_BASE_KRW = val
                    limit_krw = DAILY_LOSS_BASE_KRW * (MAX_DAILY_LOSS_PCT / 100)
                    send_msg(
                        f"✅ 손실 한도 변경 완료!\n"
                        f"기준금액: {val:,.0f}원\n"
                        f"최대 손실: {limit_krw:,.0f}원",
                        level="normal"
                    , force=True)
                    save_state()
            except:
                send_msg("❌ 숫자로 입력해 주세요.\n예) /risk 100000", level="normal")

    elif cmd[0] == "/budget":
        try:
            val = int(cmd[1].replace(",", "").replace("원", ""))
            if val < 10_000:
                send_msg("❌ 10,000원 이상으로 입력해 주세요.\n예) /budget 50000", level="normal")
            else:
                log_change("예산변경", f"ORDER_BUDGET_KRW = {val:,}원")
                ORDER_BUDGET_KRW = val
                send_msg(
                    f"✅ 예산 변경 완료!\n"
                    f"1회 주문 예산: {val:,.0f}원",
                    level="normal"
                , force=True)
                save_state()
        except:
            send_msg(
                f"⚙️ 1회 주문 예산 변경\n"
                f"현재 예산: {ORDER_BUDGET_KRW:,.0f}원\n\n"
                f"사용법: /budget 금액\n"
                f"예시 : /budget 50000  → 5만원으로 변경",
                level="normal"
            , force=True)

    elif cmd[0] == "/report":
        total    = win_count + loss_count
        win_rate = round(win_count / total * 100) if total > 0 else 0
        price    = _last_price if _last_price else 0
        pnl_now  = net_diff_krw(bot["buy_price"], price, bot["filled_qty"]) if bot["has_stock"] else 0
        cpu      = psutil.cpu_percent(interval=None)
        ram      = psutil.virtual_memory().percent
        send_msg(
            f"📊 오늘 매매 리포트\n"
            f"{'─'*20}\n"
            f"총 거래  : {total}회\n"
            f"익절(승) : {win_count}회\n"
            f"손절(패) : {loss_count}회\n"
            f"승률     : {win_rate}%\n"
            f"{'─'*20}\n"
            f"확정 손익: {daily_pnl_krw:+,.0f}원\n"
            f"미확정   : {pnl_now:+,.0f}원 {'(보유중)' if bot['has_stock'] else '(없음)'}\n"
            f"합계     : {daily_pnl_krw + pnl_now:+,.0f}원\n"
            f"{'─'*20}\n"
            f"CPU: {cpu:.0f}%  RAM: {ram:.0f}%",
            level="normal",
            keyboard=KB_MAIN
        , force=True)

    elif cmd[0] in ("/help", "/도움말"):
        send_msg(
            "📋 전체 명령어 목록\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "📊 조회\n"
            "  /status   현재 상태 (RSI·VWAP·손익)\n"
            "  /report   오늘 매매 결과\n"
            "  /weekly   이번 주 손익\n"
            "  /log      오늘 거래 내역\n"
            "  /balance  계좌 잔고\n"
            "─────────────────\n"
            "🎮 봇 제어\n"
            "  /start         매매 시작\n"
            "  /stop          완전 정지\n"
            "  /pause N       N분 일시정지 후 자동 재개\n"
            "─────────────────\n"
            "⚙️ 설정 변경\n"
            "  /set 항목 값   전략 수치 변경\n"
            "  /risk 금액     일일 손실 한도 변경\n"
            "  /budget 금액   1회 주문 예산 변경\n"
            "  /reload        설정파일 다시 읽기\n"
            "─────────────────\n"
            "📈 매매\n"
            "  /hold 가격 수량  수동 매수 등록\n"
            "  /stock           종목 변경\n"
            "  /sim             시뮬레이션\n"
            "─────────────────\n"
            "🛠️ 기타\n"
            "  /screen  현재 PC 화면 캡처 전송\n"
            "  /menu    버튼 메뉴 열기\n"
            "  /help    이 목록\n"
            "─────────────────\n"
            "💡 단축키·한글 별칭 지원\n"
            "  /s /상태   /b /잔고   /r /오늘\n"
            "  /l /내역   /w /주간   /멈춰 /켜줘\n"
            "  /잠깐 N   /캡처   /종목\n"
            "─────────────────\n"
            "📚 용어 검색: !rsi  !vwap  !손절  등",
            level="normal",
            keyboard=KB_MAIN,
            force=True
        )

    # ── [43] /log : 오늘 거래 내역 텔레그램 출력 ────────────
    elif cmd[0] == "/log":
        try:
            if not os.path.exists(LOG_FILE):
                send_msg("📋 아직 오늘 거래 내역이 없어요.", level="normal", force=True)
                return
            today_str = str(date.today())
            lines = []
            with open(LOG_FILE, encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get("datetime","").startswith(today_str):
                        side_kr = "매수" if row["side"] == "BUY" else "매도"
                        pnl_str = ""
                        if row["side"] == "SELL":
                            pnl = float(row.get("pnl_krw") or 0)
                            pnl_str = f"  손익: {pnl:+,.0f}원"
                        reason_str = f"  [{row.get('reason','')}]" if row.get("reason") else ""
                        lines.append(
                            f"{row['datetime'][11:16]} {side_kr} "
                            f"{int(float(row['price'])):,}원 × {row['qty']}주"
                            f"{pnl_str}{reason_str}"
                        )
            if not lines:
                send_msg("📋 오늘 거래 내역이 없어요.", level="normal", force=True)
            else:
                chunk = f"📋 오늘 거래 내역 ({len(lines)}건)\n{'─'*24}\n"
                for line in lines:
                    if len(chunk) + len(line) > 3800:
                        send_msg(chunk, level="normal", force=True)
                        chunk = ""
                    chunk += line + "\n"
                if chunk:
                    chunk += f"{'─'*24}\n오늘 누적: {daily_pnl_krw:+,.0f}원"
                    send_msg(chunk, level="normal", force=True)
        except Exception as e:
            send_msg(f"❌ 로그 읽기 실패: {e}", level="normal", force=True)

    # ── [44] /reload : YAML 설정 핫리로드 ───────────────────
    elif cmd[0] == "/reload":
        try:
            global _cfg
            _cfg = load_config()
            send_msg(
                f"🔄 설정 파일 다시 읽었어요!\n"
                f"kis_devlp.yaml 변경 사항이 반영됐어요.\n"
                f"(봇 전략 수치는 /set 명령으로 변경하세요)",
                level="normal"
            , force=True)
        except Exception as e:
            send_msg(f"❌ 설정 파일 읽기 실패: {e}", level="critical", force=True)

    # ── /hold : 수동 매수 포지션 등록 ──────────────────────
    elif cmd[0] == "/hold":
        if bot["has_stock"]:
            send_msg(
                f"⚠️ 이미 포지션이 등록돼 있어요.\n"
                f"매수가: {bot['buy_price']:,.0f}원 / {bot['filled_qty']}주\n"
                f"먼저 /sell 로 청산하거나 봇이 자동 매도하게 두세요.",
                level="normal"
            , force=True)
            return
        if len(cmd) < 3:
            send_msg(
                "📌 수동 매수 포지션 등록\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "사용법: /hold 매수가 수량\n\n"
                "📌 입력 순서\n"
                "  1번째: 내가 산 가격 (원)\n"
                "  2번째: 내가 산 수량 (주)\n\n"
                "📌 예시\n"
                "  1,840원에 5주 샀다면\n"
                "  → /hold 1840 5",
                level="normal"
            , force=True)
            return
        try:
            buy_p = float(cmd[1].replace(",", "").replace("원", ""))
            qty   = int(cmd[2].replace(",", "").replace("주", ""))
            if buy_p <= 0 or qty <= 0:
                raise ValueError
            # 실수 방지: 가격과 수량이 뒤바뀐 것 같으면 경고
            if buy_p < qty and qty > 100:
                send_msg(
                    f"⚠️ 혹시 가격과 수량이 바뀐 건 아닌가요?\n"
                    f"입력하신 값: 가격 {buy_p:,.0f}원 / 수량 {qty}주\n\n"
                    f"맞다면 다시 입력해 주세요.\n"
                    f"순서: /hold 가격 수량\n"
                    f"예시: /hold 1840 5",
                    level="normal"
                , force=True)
                return
        except:
            send_msg(
                "❌ 입력값이 잘못됐어요.\n\n"
                "📌 올바른 형식\n"
                "  /hold 가격 수량\n"
                "  예) /hold 1840 5",
                level="normal"
            )
            return

        global _buy_time
        bot["has_stock"]  = True
        bot["buy_price"]  = buy_p
        bot["filled_qty"] = qty
        bot["be_active"]  = False
        _buy_time         = time.time()
        save_state()

        pnl_now = net_diff_krw(buy_p, _last_price, qty) if _last_price else 0
        send_msg(
            f"✅ 포지션 등록 완료!\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"매수가  : {buy_p:,.0f}원\n"
            f"수량    : {qty}주\n"
            f"투자금  : {buy_p * qty:,.0f}원\n"
            f"현재 손익: {pnl_now:+,.0f}원\n"
            f"─────────────────\n"
            f"익절 목표: +{bot['target']}%  ({buy_p * (1 + bot['target']/100):,.0f}원)\n"
            f"손절 기준: {bot['max_loss']}%  ({buy_p * (1 + bot['max_loss']/100):,.0f}원)\n"
            f"→ 지금부터 봇이 자동으로 관리해요.",
            level="normal"
        , force=True)

    # ── /stock : 종목 변경 ───────────────────────────────────
    elif cmd[0] == "/stock":
        if len(cmd) < 2:
            # 인자 없으면 현재 종목 + 가능 목록 안내
            lines = ["📋 거래 가능한 종목 목록\n━━━━━━━━━━━━━━━━━━━━"]
            for code, p in STOCK_PRESETS.items():
                marker = "▶️ " if code == STOCK_CODE else "   "
                lines.append(f"{marker}{code}  {p['name']}\n      {p['note']}")
            lines.append(f"\n현재 종목: {STOCK_CODE} ({STOCK_PRESETS.get(STOCK_CODE, PRESET_DEFAULT)['name']})")
            lines.append("변경: /stock 114800")
            send_msg("\n".join(lines), level="normal", force=True)
            return

        new_code = cmd[1].strip()

        # 보유 중이면 변경 불가
        if bot["has_stock"]:
            send_msg(
                f"⚠️ 지금 주식을 보유 중이에요!\n"
                f"매도 후에 종목을 변경해 주세요.",
                level="normal"
            , force=True)
            return

        # 같은 종목이면 무시
        if new_code == STOCK_CODE:
            send_msg(
                f"ℹ️ 이미 {STOCK_CODE} 종목이에요.",
                level="normal"
            , force=True)
            return

        # 프리셋 적용
        log_change("종목변경", f"STOCK_CODE = {new_code}")
        preset = apply_stock_preset(new_code)
        is_known = new_code in STOCK_PRESETS

        send_msg(
            f"✅ 종목 변경 완료!\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"종목    : {new_code}  {preset['name']}\n"
            f"비고    : {preset['note']}\n"
            f"{'─'*20}\n"
            f"익절    : +{preset['target']}%\n"
            f"손절    : {preset['max_loss']}%\n"
            f"눌림    : {preset['drop']}%\n"
            f"트레일  : {preset['trail_start']}% 시작 / {preset['trail_gap']}% 간격\n"
            f"본절    : {preset['be_trigger']}%\n"
            f"RSI     : {preset['rsi_buy']} 이하\n"
            f"타임아웃: {preset['timeout_min']}분\n"
            f"{'─'*20}\n"
            f"{'⚠️ 프리셋 없는 종목 → 범용 설정 적용됨' if not is_known else '✅ 최적 프리셋 적용됨'}",
            level="normal"
        , force=True)

    # ── [40] /weekly : 주간 손익 확인 ───────────────────────
    elif cmd[0] == "/weekly":
        remaining = WEEKLY_LOSS_LIMIT_KRW - weekly_pnl_krw
        send_msg(
            f"📆 이번 주 손익\n"
            f"주간 누적 손익: {weekly_pnl_krw:+,.0f}원\n"
            f"주간 손실 한도: {WEEKLY_LOSS_LIMIT_KRW:,.0f}원\n"
            f"한도까지 남은 여유: {remaining:,.0f}원\n"
            f"주간 정지 여부: {'⛔ 정지 중' if _weekly_stop else '✅ 정상 가동'}",
            level="normal"
        , force=True)

    # ── /sim : 설정값 변경 시뮬레이션 ───────────────────────
    elif cmd[0] == "/sim":
        run_simulation(cmd)

    # ── /report : 통합 리포트 ────────────────────────────────
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

    # ── /version : 현재 실행 중인 코드 버전 확인 ────────────
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

    # ── /update : GitHub에서 최신 코드로 교체 후 재시작 ─────
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

    # ── 용어 사전 (!용어) ────────────────────────────────────
    elif text.startswith("!"):
        handle_glossary(text[1:].strip().lower())

# ============================================================
# [GitHub 원격 업데이트]
# ============================================================
# [통합 리포트]
# ============================================================
def _read_log_summary(log_path, period="daily"):
    """CSV 로그 파일에서 기간별 손익 집계"""
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
    """두 봇의 거래 로그를 합산한 통합 리포트 전송"""
    period_kr = {"daily": "오늘", "weekly": "이번 주", "monthly": "이번 달", "total": "전체"}.get(period, period)

    # 주식봇 로그
    kis_data  = _read_log_summary(KIS_SHARED_LOG,  period) or _read_log_summary(LOG_FILE, period)
    # 코인봇 로그
    coin_data = _read_log_summary(COIN_SHARED_LOG, period)

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

    # 합산
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
VERSION_FILE = os.path.join(BASE_DIR, ".bot_version.json")
BOT_SCRIPT   = os.path.abspath(__file__)

def _load_local_version():
    """로컬에 저장된 현재 버전 정보 로드"""
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
    """GitHub API로 최신 커밋 정보 조회"""
    try:
        res = requests.get(
            f"https://api.github.com/repos/{repo}/commits/main",
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github.v3+json"
            },
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

def _github_download(repo, token, filename):
    """GitHub에서 파일 내용 다운로드"""
    try:
        res = requests.get(
            f"https://api.github.com/repos/{repo}/contents/{filename}",
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github.v3.raw"
            },
            timeout=30
        )
        if res.status_code == 200:
            return res.text
    except Exception as e:
        cprint(f"[GitHub 다운로드 오류] {e}", Fore.YELLOW)
    return None

def _extract_version(code):
    """코드 문자열에서 BOT_VERSION 값 추출"""
    for line in code.splitlines():
        if line.startswith("BOT_VERSION"):
            try:
                return line.split("=")[1].strip().strip('"').strip("'")
            except:
                pass
    return None

def _version_newer(new_ver, cur_ver):
    """new_ver 이 cur_ver 보다 높으면 True"""
    try:
        return tuple(map(int, new_ver.split("."))) > tuple(map(int, cur_ver.split(".")))
    except:
        return False

def _restart():
    """Windows/Linux 공통 재시작"""
    import subprocess
    subprocess.Popen([sys.executable] + sys.argv)
    time.sleep(1)
    os._exit(0)

def _apply_code(new_code, new_info, current_hash, force=False):
    """공통 코드 교체 + 재시작 로직"""
    bot_filename = os.path.basename(BOT_SCRIPT)
    backup_path  = BOT_SCRIPT + ".bak"
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
    """GitHub에서 최신 코드를 받아 버전 비교 후 교체"""
    github_token = _cfg.get("github_token", "")
    github_repo  = _cfg.get("github_repo", "")
    bot_filename = os.path.basename(BOT_SCRIPT)

    if not github_token or not github_repo:
        send_msg(
            "❌ 업데이트 설정 없음\n"
            "kis_devlp.yaml 에 아래 항목을 추가하세요:\n"
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

    # 새 코드 다운로드
    new_code = _github_download(github_repo, github_token, bot_filename)
    if not new_code:
        send_msg("❌ 코드 다운로드 실패", level="critical", force=True)
        return

    # 버전 번호 비교
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
        f"메시지: {latest['message']}\n"
        f"→ 교체 중...",
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
        try:
            res = requests.get(
                f"https://api.github.com/repos/{github_repo}/contents/{bot_filename}",
                headers={
                    "Authorization": f"token {github_token}",
                    "Accept": "application/vnd.github.v3.raw"
                },
                params={"ref": target_hash},
                timeout=30
            )
            if res.status_code != 200:
                send_msg(f"❌ 커밋 {target_hash} 다운로드 실패\n→ 해시를 다시 확인하세요.", level="critical", force=True)
                return
            new_code = res.text
        except Exception as e:
            send_msg(f"❌ 롤백 다운로드 오류: {e}", level="critical", force=True)
            return

        rollback_ver = _extract_version(new_code) or "?"
        info = {"hash": target_hash[:7], "full": target_hash, "message": f"롤백: {target_hash[:7]}", "time": "", "version": rollback_ver}
        current = _load_local_version()
        current_hash = current["hash"] if current else "없음"
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
        # 현재 파일을 임시 저장 후 교체
        tmp = BOT_SCRIPT + ".tmp"
        shutil.copy2(BOT_SCRIPT, tmp)
        shutil.copy2(backup_path, BOT_SCRIPT)
        shutil.copy2(tmp, backup_path)  # 현재를 .bak으로 (재롤백 가능하게)
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

# ── /sim 시뮬레이션 함수 ─────────────────────────────────────def run_simulation(cmd):
    """
    /sim                    → 현재 설정으로 기존 거래 결과 요약
    /sim target 1.5         → 익절 목표를 1.5%로 바꿨으면 어땠을까
    /sim max_loss -2.0      → 손절 기준을 -2.0%로 바꿨으면 어땠을까
    /sim target 1.5 max_loss -2.0  → 두 개 동시 변경
    """
    # trade_log.csv 에서 SELL 행만 읽기
    if not os.path.exists(LOG_FILE):
        send_msg("📊 아직 거래 기록이 없어요.\n봇이 실제로 매매를 해야 시뮬레이션을 쓸 수 있어요.", level="normal")
        return

    sells = []
    try:
        with open(LOG_FILE, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("side") != "SELL": continue
                try:
                    sells.append({
                        "buy_price":  float(row.get("price", 0)),
                        "pnl_krw":    float(row.get("pnl_krw", 0)),
                        "reason":     row.get("reason", ""),
                        "qty":        int(row.get("qty", 1)),
                        "date":       row.get("datetime", "")[:10],
                    })
                except:
                    continue
    except Exception as e:
        send_msg(f"❌ 거래 기록 읽기 실패: {e}", level="normal")
        return

    if len(sells) < 3:
        send_msg(
            f"📊 거래 기록이 {len(sells)}건밖에 없어요.\n"
            "최소 3건 이상 쌓여야 의미 있는 시뮬레이션이 가능해요.",
            level="normal"
        )
        return

    # ── 파라미터 파싱 ────────────────────────────────────────
    # /sim target 1.5  또는  /sim target 1.5 max_loss -2.0
    sim_params = {}
    i = 1
    while i < len(cmd) - 1:
        key = cmd[i].lstrip("/")
        try:
            val = float(cmd[i + 1])
            if key in ("target", "max_loss"):
                sim_params[key] = val
            i += 2
        except:
            i += 1

    # 시뮬레이션할 target / max_loss (없으면 현재값 사용)
    sim_target   = sim_params.get("target",   bot["target"])
    sim_max_loss = sim_params.get("max_loss", bot["max_loss"])

    # ── 현재 설정으로 실제 결과 ──────────────────────────────
    real_total  = sum(s["pnl_krw"] for s in sells)
    real_wins   = sum(1 for s in sells if s["pnl_krw"] > 0)
    real_losses = sum(1 for s in sells if s["pnl_krw"] < 0)

    # ── 시뮬레이션: 각 거래를 새 기준으로 재계산 ────────────
    # buy_price × qty 로 투자금 역산, 새 기준 적용
    sim_total = 0
    sim_wins  = 0
    sim_losses = 0
    sim_details = []   # 달라진 거래만 기록

    for s in sells:
        invested = s["buy_price"] * s["qty"]
        if invested <= 0:
            # 투자금 계산 안 되면 실제 손익 그대로
            sim_total += s["pnl_krw"]
            if s["pnl_krw"] > 0: sim_wins += 1
            else: sim_losses += 1
            continue

        # 수수료 포함 실제 수익률 역산
        real_pct = s["pnl_krw"] / invested * 100

        # 새 기준 적용: 어느 조건이 먼저 걸리는지
        # (실제 손익률이 새 목표/손절 사이에 있으면 그대로, 아니면 클램프)
        if real_pct >= sim_target:
            # 새 목표에서 익절됐을 경우
            new_pnl = round(invested * sim_target / 100)
        elif real_pct <= sim_max_loss:
            # 새 손절에서 잘렸을 경우
            new_pnl = round(invested * sim_max_loss / 100)
        else:
            # 새 기준 범위 안 → 실제 손익 그대로
            new_pnl = round(s["pnl_krw"])

        sim_total += new_pnl
        if new_pnl > 0: sim_wins += 1
        else: sim_losses += 1

        diff = new_pnl - s["pnl_krw"]
        if abs(diff) >= 100:   # 100원 이상 차이 나는 거래만 기록
            sim_details.append((s["date"], round(s["pnl_krw"]), new_pnl, diff))

    # ── 결과 메시지 ──────────────────────────────────────────
    diff_total  = sim_total - real_total
    diff_wins   = sim_wins  - real_wins
    n           = len(sells)
    real_wr     = real_wins / n * 100
    sim_wr      = sim_wins  / n * 100

    changed = bool(sim_params)
    if changed:
        change_desc = []
        if "target"   in sim_params: change_desc.append(f"익절 {bot['target']}% → {sim_target}%")
        if "max_loss" in sim_params: change_desc.append(f"손절 {bot['max_loss']}% → {sim_max_loss}%")
        header = "📊 시뮬레이션 결과\n" + " / ".join(change_desc)
    else:
        header = "📊 현재 설정 기준 거래 요약"

    trend = "📈" if diff_total > 0 else "📉" if diff_total < 0 else "➡️"

    msg = (
        f"{header}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"분석 거래 수 : {n}건\n"
        f"\n"
        f"【 실제 결과 】\n"
        f"  누적 손익 : {real_total:+,.0f}원\n"
        f"  승률      : {real_wr:.1f}%  ({real_wins}승 {real_losses}패)\n"
    )

    if changed:
        msg += (
            f"\n【 바꿨다면? 】{trend}\n"
            f"  누적 손익 : {sim_total:+,.0f}원\n"
            f"  승률      : {sim_wr:.1f}%  ({sim_wins}승 {sim_losses}패)\n"
            f"\n"
            f"【 차이 】\n"
            f"  손익 변화 : {diff_total:+,.0f}원\n"
            f"  승패 변화 : {diff_wins:+}회\n"
        )
        if sim_details:
            msg += f"\n📌 영향받은 거래 (상위 {min(3,len(sim_details))}건)\n"
            for date_, real_p, sim_p, d in sorted(sim_details, key=lambda x: abs(x[3]), reverse=True)[:3]:
                arrow = "↑" if d > 0 else "↓"
                msg += f"  {date_}  {real_p:+,}원 → {sim_p:+,}원  ({arrow}{abs(d):,}원)\n"

        msg += (
            f"\n⚠️ 주의: 이미 체결된 거래만 기준으로 계산해요.\n"
            f"설정을 바꾸면 진입 횟수 자체가 달라질 수 있어서\n"
            f"참고용으로만 보세요.\n"
            f"\n사용법:\n"
            f"  /sim target 1.5\n"
            f"  /sim max_loss -2.0\n"
            f"  /sim target 1.5 max_loss -2.0"
        )
    else:
        msg += (
            f"\n사용법: 값을 바꿔서 비교해보세요\n"
            f"  /sim target 1.5\n"
            f"  /sim max_loss -2.0\n"
            f"  /sim target 1.5 max_loss -2.0"
        )

    send_msg(msg, level="normal")

# ── 용어 사전 데이터 + 처리 함수 ────────────────────────────
GLOSSARY = {
    # RSI
    "rsi": (
        "📖 RSI (상대강도지수)\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "최근 N일 동안 얼마나 많이 올랐는지·내렸는지를\n"
        "0~100 숫자로 나타낸 지표예요.\n\n"
        "📌 기준\n"
        "  30 이하 → 많이 떨어진 상태 (과매도)\n"
        "           → 반등 가능성 있음 → 매수 고려\n"
        "  70 이상 → 많이 오른 상태 (과매수)\n"
        "           → 조정 가능성 있음\n\n"
        "📌 이 봇에서는?\n"
        f"  RSI가 {bot['rsi_buy']} 이하로 내려갔다가\n"
        "  다시 올라올 때 매수 신호로 봐요.\n\n"
        "📌 예시\n"
        "  어제 삼성전자가 갑자기 급락했어요.\n"
        "  RSI가 25로 떨어졌다가 다시 28, 31로 올라오면\n"
        "  '바닥을 찍고 반등한다' 고 판단하는 거예요."
    ),
    "눌림": (
        "📖 눌림 (눌림목)\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "주가가 올라가다가 잠깐 숨을 고르며\n"
        "살짝 내려오는 구간을 말해요.\n\n"
        "📌 왜 중요하냐면?\n"
        "  주가가 쭉 오를 때 올라탔다간 고점에서 살 수 있어요.\n"
        "  눌림 구간에서 사야 더 싸게 살 수 있어요.\n\n"
        "📌 이 봇에서는?\n"
        f"  20일 평균가(MA20) 대비 {bot['drop']}% 이상 내려왔을 때\n"
        "  '적당히 눌렸다' 고 보고 매수를 고려해요.\n\n"
        "📌 예시\n"
        "  MA20이 10,000원인데 현재가가 9,920원이면\n"
        "  0.8% 눌린 상태예요. 기준(0.8%) 충족!"
    ),
    "vwap": (
        "📖 VWAP (거래량 가중 평균가)\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "오늘 장이 열린 이후 체결된 모든 거래의\n"
        "거래량을 가중치로 계산한 평균 가격이에요.\n"
        "쉽게 말하면 '오늘 대부분의 사람들이\n"
        "얼마에 샀는지' 를 나타내요.\n\n"
        "📌 이 봇에서는?\n"
        "  현재가가 VWAP보다 낮을 때만 매수해요.\n"
        "  → 평균보다 싸게 사는 거니까 유리해요.\n\n"
        "📌 예시\n"
        "  VWAP = 13,500원\n"
        "  현재가 13,400원 → 평균보다 싸다 ✅ 매수 고려\n"
        "  현재가 13,600원 → 평균보다 비싸다 ❌ 매수 안 함"
    ),
    "ma20": (
        "📖 MA20 (20일 이동평균선)\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "최근 20개 봉의 가격을 평균낸 값이에요.\n"
        "주가의 단기 흐름을 보여줘요.\n\n"
        "📌 이 봇에서는?\n"
        "  MA20 > MA60 이면 '단기가 장기보다 높다'\n"
        "  → 상승 추세로 보고 매수를 고려해요.\n\n"
        "📌 예시\n"
        "  MA20 = 13,500원, MA60 = 13,200원\n"
        "  → 단기 평균이 더 높다 = 최근 오르는 중 ✅"
    ),
    "ma60": (
        "📖 MA60 (60일 이동평균선)\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "최근 60개 봉의 가격을 평균낸 값이에요.\n"
        "MA20보다 긴 기간이라 주가의 큰 흐름을 봐요.\n\n"
        "📌 이 봇에서는?\n"
        "  MA20이 MA60보다 높아야 매수 조건 충족이에요.\n"
        "  MA60이 MA20보다 높으면 하락 추세로 보고 쉬어요.\n\n"
        "📌 예시\n"
        "  MA20 = 13,200원, MA60 = 13,500원\n"
        "  → 단기 평균이 더 낮다 = 최근 내려가는 중 ❌"
    ),
    "ma": (
        "📖 MA (이동평균선)\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "특정 기간 동안의 평균 가격을 선으로 이은 거예요.\n"
        "주가의 전반적인 방향을 파악할 때 써요.\n\n"
        "📌 종류\n"
        "  MA20  → 최근 20봉 평균 (단기 흐름)\n"
        "  MA60  → 최근 60봉 평균 (중장기 흐름)\n\n"
        "📌 자세한 설명\n"
        "  !ma20  또는  !ma60  을 입력해보세요."
    ),
    "이동평균": (
        "📖 이동평균선 = MA\n"
        "!ma 를 입력하면 자세한 설명을 볼 수 있어요."
    ),
    "트레일링": (
        "📖 트레일링 스탑\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "수익이 나고 있을 때 고점을 자동으로 추적하다가\n"
        "고점에서 일정 % 빠지면 자동으로 파는 기능이에요.\n\n"
        "📌 일반 손절과의 차이\n"
        "  일반 손절  → 매수가 기준으로 고정된 % 손실 시 매도\n"
        "  트레일링   → 수익이 나면 기준점이 위로 따라 올라감\n\n"
        f"📌 이 봇에서는?\n"
        f"  수익이 {bot['trail_start']}% 넘으면 추적 시작\n"
        f"  고점 대비 {bot['trail_gap']}% 빠지면 자동 매도\n\n"
        "📌 예시\n"
        "  매수가 10,000원 → 수익 0.8% (고점)\n"
        "  → 고점에서 0.3% 빠지면 → 자동 매도!"
    ),
    "본절": (
        "📖 본절 보호 (본전 보호)\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "수익이 일정 % 이상 났을 때 안전장치를 켜서\n"
        "이후 가격이 매수가 아래로 내려오면 바로 파는 기능이에요.\n"
        "한 마디로 '최소한 본전은 건진다' 는 거예요.\n\n"
        f"📌 이 봇에서는?\n"
        f"  수익이 {bot['be_trigger']}% 넘으면 안전장치 ON\n"
        "  이후 가격이 매수가 이하로 내려오면 즉시 매도\n\n"
        "📌 예시\n"
        "  10,000원에 삼 → 10,040원(+0.4%) 도달 → 안전장치 ON\n"
        "  이후 가격이 9,990원으로 내려오면 → 바로 매도!"
    ),
    "손절": (
        "📖 손절 (손실 확정)\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "손해를 보고 있어도 더 큰 손해를 막기 위해\n"
        "과감하게 파는 것을 말해요.\n\n"
        f"📌 이 봇에서는?\n"
        f"  매수가 대비 {bot['max_loss']}% 손실 시 자동 손절\n\n"
        "📌 예시\n"
        "  10,000원에 삼 → 9,850원(-1.5%) 되면 자동 매도\n"
        "  → 더 빠지기 전에 끊어내는 거예요."
    ),
    "익절": (
        "📖 익절 (이익 확정)\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "목표 수익률에 도달했을 때 수익을 확정하고 파는 것이에요.\n\n"
        f"📌 이 봇에서는?\n"
        f"  매수가 대비 +{bot['target']}% 달성 시 자동 익절\n\n"
        "📌 예시\n"
        "  10,000원에 삼 → 10,120원(+1.2%) 되면 자동 매도\n"
        "  → 욕심 부리지 않고 목표에서 챙기는 거예요."
    ),
    "슬리피지": (
        "📖 슬리피지 (Slippage)\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "내가 원한 가격과 실제로 체결된 가격의 차이예요.\n"
        "주문을 넣는 순간과 실제 체결 사이에 가격이 움직여서 생겨요.\n\n"
        f"📌 이 봇에서는?\n"
        f"  체결가가 현재가보다 {MAX_SLIPPAGE_PCT}% 이상 벗어나면\n"
        "  즉시 매도해요. (불리한 가격에 산 거니까)\n\n"
        "📌 예시\n"
        "  13,450원에 매수 주문 → 실제 체결은 13,520원\n"
        "  차이가 0.52% → 허용 범위(0.5%) 초과 → 즉시 매도!"
    ),
    "다이버전스": (
        "📖 다이버전스 (Divergence, 이중 바닥)\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "가격은 더 낮아졌는데 RSI는 오히려 높아지는 현상이에요.\n"
        "힘이 빠진 하락이라 곧 반등할 가능성이 높다는 신호예요.\n\n"
        "📌 쉽게 말하면?\n"
        "  1차 저점: 가격 10,000원 / RSI 25\n"
        "  2차 저점: 가격  9,900원 / RSI 28\n"
        "  → 가격은 더 빠졌는데 RSI는 올라왔다\n"
        "  → '파는 힘이 줄었다' = 반등 신호!\n\n"
        "📌 이 봇에서는?\n"
        "  이 패턴이 확인될 때만 매수 신호로 인정해요."
    ),
    "쿨다운": (
        "📖 쿨다운 (Cooldown)\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "한 번 팔고 나서 바로 또 사지 않도록\n"
        "일정 시간 매수를 쉬는 것이에요.\n\n"
        f"📌 이 봇에서는?\n"
        f"  매도 후 {COOLDOWN_SEC//60}분간 매수 안 해요.\n\n"
        "📌 왜 필요하냐면?\n"
        "  팔자마자 바로 다시 사면 수수료도 이중으로 내고\n"
        "  감정적인 매매가 될 수 있어서요."
    ),
    "atr": (
        "📖 ATR (평균 실제 범위)\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "주가가 하루 동안 얼마나 움직이는지 평균을 낸 값이에요.\n"
        "변동성을 측정하는 지표예요.\n\n"
        "📌 이 봇에서는?\n"
        "  매일 아침 8:55에 최근 20일 ATR을 계산해서\n"
        "  오늘 적절한 익절·손절 기준을 추천해줘요.\n\n"
        "📌 예시\n"
        "  ATR이 1.5%면 → 익절 1.2%, 손절 -5.25% 추천\n"
        "  ATR이 3.0%면 → 더 크게 움직이니까 목표도 커져요."
    ),
    "타임아웃": (
        "📖 포지션 타임아웃\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "주식을 샀는데 오르지도 내리지도 않고\n"
        "횡보만 하면 일정 시간 후 자동으로 파는 기능이에요.\n\n"
        f"📌 이 봇에서는?\n"
        f"  매수 후 {POS_TIMEOUT_MIN}분이 지나도 목표·손절에 안 걸리면\n"
        "  자동으로 청산해요.\n\n"
        "📌 왜 필요하냐면?\n"
        "  방향 없이 묶여 있는 돈을 풀어서\n"
        "  더 좋은 기회에 다시 쓸 수 있게 해줘요."
    ),
    "거래량": (
        "📖 거래량 비율\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "지금 거래량이 평소 대비 얼마나 많은지 나타내요.\n\n"
        f"📌 이 봇에서는?\n"
        f"  평소 대비 {VOL_RATIO_MIN}배 이상일 때만 매수해요.\n"
        "  거래량이 적으면 가짜 신호일 수 있어서요.\n\n"
        "📌 예시\n"
        "  평소 거래량: 10만주\n"
        "  지금 거래량: 15만주 → 1.5배 → 조건 충족 ✅\n"
        "  지금 거래량:  8만주 → 0.8배 → 조건 미충족 ❌"
    ),
    "변동성": (
        "📖 변동성\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"최근 {VOL_WINDOW_SEC//60}분 동안 가격이 얼마나 움직였는지예요.\n\n"
        "📌 이 봇에서는?\n"
        f"  너무 잠잠해도 ({VOL_MIN_PCT}% 미만), 너무 급등락해도 ({VOL_MAX_PCT}% 초과)\n"
        "  매수 안 해요.\n\n"
        "📌 예시\n"
        "  5분간 13,400~13,450원 움직임 → 0.37% → 정상 ✅\n"
        "  5분간 13,000~13,500원 움직임 → 3.8% → 너무 급등락 ❌"
    ),
}

# 별칭 매핑 (여러 단어로 검색해도 찾을 수 있게)
GLOSSARY_ALIAS = {
    "이동평균선": "ma",
    "평균선": "ma",
    "골든크로스": "ma",
    "과매도": "rsi",
    "과매수": "rsi",
    "상대강도": "rsi",
    "눌림목": "눌림",
    "눌림목매수": "눌림",
    "평균가": "vwap",
    "체결평균가": "vwap",
    "트레일링스탑": "트레일링",
    "추적손절": "트레일링",
    "본전보호": "본절",
    "본전": "본절",
    "손절매": "손절",
    "익절매": "익절",
    "수익실현": "익절",
    "미끄러짐": "슬리피지",
    "이중바닥": "다이버전스",
    "divergence": "다이버전스",
    "cooldown": "쿨다운",
    "대기시간": "쿨다운",
    "timeout": "타임아웃",
    "타임": "타임아웃",
    "vol": "거래량",
    "volume": "거래량",
}

def handle_glossary(keyword):
    """!용어 검색 처리."""
    # 별칭 → 실제 키로 변환
    key = GLOSSARY_ALIAS.get(keyword, keyword)

    if key in GLOSSARY:
        send_msg(GLOSSARY[key], level="normal")
        return

    # 부분 일치 검색
    matches = [k for k in GLOSSARY if keyword in k]
    if matches:
        send_msg(GLOSSARY[matches[0]], level="normal")
        return

    # 못 찾은 경우 → 전체 목록 안내
    all_terms = "  ".join(sorted(GLOSSARY.keys()))
    send_msg(
        f"❓ '{keyword}' 는 등록된 용어가 아니에요.\n\n"
        f"📚 검색 가능한 용어 목록\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{all_terms}\n\n"
        f"사용법: !rsi  !vwap  !눌림  !손절  등",
        level="normal"
    )

# ============================================================
# [13] 일일 초기화 / 하트비트 / 미개장일 알림
# ============================================================
def check_daily_reset():
    global daily_pnl_krw, trade_count, highest_profit
    global _last_reset_day, _daily_report_sent, _morning_alert_sent, _pause_alert_sent
    global consecutive_loss, win_count, loss_count
    global _price_trough_5m, _last_heartbeat_hour, _nonmarket_alert_sent, _last_screen_time

    today = date.today()
    if _last_reset_day == today: return

    if _last_reset_day is not None and not _daily_report_sent:
        send_msg(
            f"📅 어제 매매 결과\n"
            f"날짜     : {_last_reset_day}\n"
            f"거래횟수 : {trade_count}회 (승 {win_count} / 패 {loss_count})\n"
            f"어제 손익: {daily_pnl_krw:+,.0f}원",
            level="normal"
        )
        do_daily_backup(str(_last_reset_day).replace("-", ""))

    daily_pnl_krw         = 0
    trade_count           = 0
    highest_profit        = 0.0
    consecutive_loss      = 0
    win_count             = 0
    loss_count            = 0
    _morning_alert_sent   = False
    _pause_alert_sent     = ""
    _price_trough_5m      = []
    _last_reset_day       = today
    _daily_report_sent    = True   # 날짜 리셋 시 요약 미발송 (당일 거래 없음)
    _last_heartbeat_hour  = -1
    _nonmarket_alert_sent = set()
    _last_screen_time    = (-1, -1)
    reset_vwap()
    cprint(f"\n[날짜 바뀜] {today} — 오늘 통계 초기화 완료", Fore.CYAN, bright=True)

# ── [40] 주간 손실 한도 체크 / 초기화 ───────────────────────
def check_weekly_reset():
    """월요일이 되면 주간 손익 초기화 + 주간 정지 해제."""
    global weekly_pnl_krw, _last_reset_week, _weekly_stop
    now   = datetime.now()
    cw    = (now.year, now.isocalendar()[1])
    if _last_reset_week == cw:
        return
    if _last_reset_week is not None:
        send_msg(
            f"📆 새로운 한 주 시작!\n"
            f"지난 주 누적 손익: {weekly_pnl_krw:+,.0f}원\n"
            f"주간 손실 한도 초기화: {WEEKLY_LOSS_LIMIT_KRW:,.0f}원",
            level="normal"
        )
    weekly_pnl_krw   = 0
    _weekly_stop     = False
    _last_reset_week = cw
    cprint(f"\n[주간 초기화] {cw} — 주간 통계 초기화 완료", Fore.CYAN, bright=True)

def is_weekly_loss_exceeded():
    return weekly_pnl_krw <= WEEKLY_LOSS_LIMIT_KRW

# ── [41] 포지션 타임아웃 체크 ────────────────────────────────
def check_pos_timeout(price):
    """매수 후 POS_TIMEOUT_MIN 분 경과 시 강제 청산."""
    if not bot["has_stock"] or _buy_time == 0.0:
        return False
    elapsed = (time.time() - _buy_time) / 60
    if elapsed < POS_TIMEOUT_MIN:
        return False
    # 현재 손실이 POS_TIMEOUT_LOSS_PCT 초과이면 타임아웃 청산 생략 (손절 로직에 맡김)
    if bot["buy_price"] > 0:
        loss_pct = (price - bot["buy_price"]) / bot["buy_price"] * 100
        if loss_pct < -POS_TIMEOUT_LOSS_PCT:
            return False
    return True

def check_heartbeat(now_dt):
    global _last_heartbeat_hour
    if now_dt.minute != 0: return
    if now_dt.hour == _last_heartbeat_hour: return
    _last_heartbeat_hour = now_dt.hour

    pnl_now = 0
    if bot["has_stock"] and bot["buy_price"] > 0:
        # 장중일 때만 실시간 가격 조회
        m_open  = now_dt.replace(hour=MARKET_OPEN[0],  minute=MARKET_OPEN[1],  second=0)
        m_close = now_dt.replace(hour=MARKET_CLOSE[0], minute=MARKET_CLOSE[1], second=0)
        if m_open <= now_dt <= m_close:
            cur_price = get_price(STOCK_CODE)
            pnl_now   = net_diff_krw(bot["buy_price"], cur_price, bot["filled_qty"])
        else:
            pnl_now = net_diff_krw(bot["buy_price"], _last_price, bot["filled_qty"]) if _last_price else 0

    cpu = psutil.cpu_percent(interval=0.5)
    ram = psutil.virtual_memory().percent

    send_msg(
        f"💚 봇 정상 가동 중 [{now_dt.strftime('%H:%M')}]\n"
        f"오늘 확정 손익: {daily_pnl_krw:+,.0f}원\n"
        f"미확정 손익  : {pnl_now:+,.0f}원 {'(보유중)' if bot['has_stock'] else '(미보유)'}\n"
        f"거래 횟수   : {trade_count}회 (승 {win_count} / 패 {loss_count})\n"
        f"CPU: {cpu:.0f}%  RAM: {ram:.0f}%",
        level="silent"
    )


def check_auto_screen(now_dt):
    """장중 정해진 시각에 자동 화면 캡처 전송"""
    global _last_screen_time
    if not is_market_open_day(): return
    for (h, m) in SCREEN_AUTO_TIMES:
        if now_dt.hour == h and now_dt.minute == m:
            if _last_screen_time == (h, m): return
            _last_screen_time = (h, m)
            send_screen(f"자동 ({h:02d}:{m:02d})")
            return

def check_nonmarket_alert(now_dt):
    global _nonmarket_alert_sent
    if is_market_open_day(): return
    if now_dt.minute != 0: return
    if now_dt.hour not in NONMARKET_ALERT_HOURS: return
    if now_dt.hour in _nonmarket_alert_sent: return
    _nonmarket_alert_sent.add(now_dt.hour)
    cpu = psutil.cpu_percent(interval=0.5)
    ram = psutil.virtual_memory().percent
    send_msg(
        f"💤 오늘은 장이 열리지 않아요 [{now_dt.strftime('%m/%d %H:%M')}]\n"
        f"봇은 정상 가동 중이에요. 내일 장이 열리면 자동으로 매매를 시작해요.\n"
        f"CPU: {cpu:.0f}%  RAM: {ram:.0f}%",
        level="normal", force=True
    )

def prefill_history(code, count=HISTORY_PREFILL):
    """
    [기능 1] 시작 시 데이터 수집 (수집 중 텔레그램 응답 가능)
    터미널에 실시간 진행률 바 표시: [████░░░░░░░░░░░░░░░░] 15/70 (21%)
    """
    cprint(f"📊 초기 데이터 {count}개를 수집합니다. 잠시만 기다려주세요...", Fore.CYAN)
    collected = 0

    def _progress(step: str):
        bar_len = 20
        filled  = int(bar_len * collected / count) if count else bar_len
        bar     = "█" * filled + "░" * (bar_len - filled)
        pct     = collected / count * 100 if count else 100
        print(f"\r  [{bar}] {collected}/{count} ({pct:.0f}%)  {step}   ", end="", flush=True)

    # ── 1순위: 일봉 API (FHKST03010100) ─────────────────────
    h = kis_headers("FHKST03010100")
    if h:
        today = datetime.now().strftime("%Y%m%d")
        from_date = (datetime.now() - timedelta(days=120)).strftime("%Y%m%d")
        res = api_call(
            "get",
            f"{_cfg['prod']}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
            headers=h,
            params={
                "fid_cond_mrkt_div_code": "J",
                "fid_input_iscd": code,
                "fid_input_date_1": from_date,
                "fid_input_date_2": today,
                "fid_period_div_code": "D",
                "fid_org_adj_prc": "0",
            }
        )
        if res and res.get("output2"):
            bars = res["output2"]
            for bar in list(reversed(bars))[-count:]:
                try:
                    p = int(bar.get("stck_clpr", 0))
                    v = int(bar.get("acml_vol", 0))
                    if p > 0:
                        price_history.append(p)
                        volume_history.append(v)
                        collected += 1
                        _progress("일봉 API")
                except: continue

    # ── 2순위: 부족한 만큼 현재가로 채우기 ─────────────────────
    remaining = count - collected
    if remaining > 0:
        for i in range(remaining):
            price, vol = get_price_and_volume(code)
            if price > 0:
                price_history.append(price)
                volume_history.append(vol)
                collected += 1
                _progress("실시간 보완")
            time.sleep(0.5)

    print()  # 진행률 줄 개행
    cprint(f"✅ 데이터 수집 완료! (총 {len(price_history)}개)", Fore.GREEN)
    poll_telegram()  # prefill 완료 후 밀린 텔레그램 명령 한 번에 처리

def get_atr(prices, period=14):
    """[기능 2] 최근 변동성(ATR) 계산"""
    if len(prices) < period + 1: return None
    p = list(prices)
    deltas = [abs(p[i] - p[i-1]) for i in range(len(p)-period, len(p))]
    return np.mean(deltas)

def update_dynamic_parameters(price):
    """[기능 3] 변동성에 따라 익절/손절/눌림 기준 실시간 조절 (수동 /set 시 비활성)"""
    if not _dynamic_mode: return
    if len(price_history) < 14: return
    atr = get_atr(price_history, 14)
    if atr:
        atr_pct = (atr / price) * 100
        new_target = max(0.6, min(2.5, atr_pct * 1.5))
        new_loss   = max(1.0, min(3.0, atr_pct * 2.0))
        new_drop   = max(0.3, min(2.0, atr_pct * 1.0))  # ATR 기반 눌림 기준
        bot["target"]   = round(new_target, 2)
        bot["max_loss"] = -round(new_loss, 2)
        bot["drop"]     = round(new_drop, 2)

 
# ============================================================
# [14] 메인 전략 루프
# ============================================================
def register_bot_commands():
    """텔레그램 메뉴바에 명령어 목록 등록 (봇 시작 시 1회 자동 실행)."""
    commands = [
        {"command": "menu",    "description": "버튼 메뉴 열기"},
        {"command": "status",  "description": "현재 상태 (가격·RSI·손익 즉시 확인)"},
        {"command": "report",  "description": "오늘 매매 결과 (승률·손익 요약)"},
        {"command": "weekly",  "description": "이번 주 누적 손익 확인"},
        {"command": "log",     "description": "오늘 거래 내역 전체 출력"},
        {"command": "balance", "description": "계좌 잔고 조회"},
        {"command": "hold",    "description": "수동 매수 등록  예) /hold 13450 3"},
        {"command": "stock",   "description": "종목 변경  예) /stock 114800"},
        {"command": "sim",     "description": "설정값 변경 시뮬레이션  예) /sim target 1.5"},
        {"command": "start",   "description": "봇 매매 시작 (RSI 기준 복원)"},
        {"command": "stop",    "description": "봇 매매 일시 정지"},
        {"command": "reload",  "description": "설정 파일 다시 읽기 (재시작 불필요)"},
        {"command": "set",     "description": "전략 수치 변경  예) /set target 1.5"},
        {"command": "risk",    "description": "일일 손실 한도 변경  예) /risk 100000"},
        {"command": "budget",  "description": "1회 주문 예산 변경  예) /budget 50000"},
        {"command": "help",    "description": "명령어 전체 목록 보기"},
        {"command": "pause",   "description": "일시 정지  예) /pause 30  (N분 후 자동 재개)"},
        {"command": "screen",  "description": "현재 PC 화면 캡처 후 전송"},
        {"command": "version", "description": "현재 실행 중인 코드 버전 확인"},
        {"command": "update",  "description": "GitHub에서 최신 코드로 업데이트"},
        {"command": "rollback","description": "이전 버전으로 롤백  예) /rollback  또는  /rollback abc1234"},
    ]
    try:
        res = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setMyCommands",
            json={"commands": commands},
            timeout=5
        )
        if res.status_code == 200 and res.json().get("result"):
            cprint("[텔레그램] 메뉴바 명령어 등록 완료 ✅", Fore.GREEN)
        else:
            cprint(f"[텔레그램] 메뉴바 등록 실패: {res.text}", Fore.YELLOW)
    except Exception as e:
        cprint(f"[텔레그램] 메뉴바 등록 오류: {e}", Fore.YELLOW)

def run_bot():
    global highest_profit, _daily_report_sent, _morning_alert_sent, _weekly_stop

    load_state()
    _daily_report_sent = True  # load_state 이후에 세팅해야 덮어쓰기 방지
    register_bot_commands()   # 텔레그램 메뉴바 자동 등록

    limit_krw = min(abs(MAX_DAILY_LOSS_KRW),
                    abs(DAILY_LOSS_BASE_KRW * (MAX_DAILY_LOSS_PCT / 100)))
    send_msg(
        f"🚀 봇 시작! [{STOCK_CODE}] v10.9\n"
        f"1회 주문 예산: {ORDER_BUDGET_KRW:,.0f}원\n"
        f"하루 최대 손실: -{limit_krw:,.0f}원\n"
        f"주간 최대 손실: {WEEKLY_LOSS_LIMIT_KRW:,.0f}원\n"
        f"포지션 타임아웃: {POS_TIMEOUT_MIN}분\n"
        f"→ 명령어 목록은 /help 를 보내주세요.",
        level="normal"
    )

    if bot["has_stock"] and bot["filled_qty"] > 0:
        price_now = get_price(STOCK_CODE)
        pnl_now   = net_diff_krw(bot["buy_price"], price_now, bot["filled_qty"])
        send_msg(
            f"🚨 잔여 포지션 발견!\n"
            f"→ {bot['filled_qty']}주 보유중 (매수가: {bot['buy_price']:,.0f}원)\n"
            f"현재가: {price_now:,.0f}원 / 손익: {pnl_now:+,.0f}원\n"
            f"→ 봇이 자동으로 관리해요.",
            level="critical"
        )

    prefill_history(STOCK_CODE, HISTORY_PREFILL)

    while True:
        try: # 👈 while보다 스페이스 4칸(또는 탭 1번) 들어감
            # 1. 텔레그램 명령 확인 (👈 여기가 포인트! try보다 딱 '4칸'만 더 들어갑니다)
            poll_telegram()

            if not bot["is_running"]:
                poll_telegram()
                time.sleep(1)
                continue

            # 2. 시간 및 손실 한도 체크
            now_dt = datetime.now()
            m_open = now_dt.replace(hour=MARKET_OPEN[0], minute=MARKET_OPEN[1], second=0)
            m_close = now_dt.replace(hour=MARKET_CLOSE[0], minute=MARKET_CLOSE[1], second=0)
        
            check_daily_reset()
            check_weekly_reset()
            check_heartbeat(now_dt)
            check_nonmarket_alert(now_dt)
            check_auto_screen(now_dt)

            # [40] 주간/일일 손실 셧다운 체크
            if is_weekly_loss_exceeded() and not _weekly_stop:
                _weekly_stop = True
                send_msg(
                    f"🚨 주간 손실 한도 초과! ({weekly_pnl_krw:+,.0f}원)\n"
                    f"→ 다음 주 월요일까지 자동 정지합니다.",
                    level="critical"
                )
                save_state()
            if _weekly_stop or is_daily_loss_exceeded() or is_weekly_loss_exceeded():
                poll_sleep(10)
                continue

            # ── 장 마감/시작 전 처리 ──
            if now_dt >= m_close:
                if bot["has_stock"]: do_sell(get_price(STOCK_CODE), "장마감 강제청산")
                if not _daily_report_sent and _last_reset_day == date.today():
                    win_rate = (win_count / trade_count * 100) if trade_count > 0 else 0
                    # 오늘 최고/최대 손실 거래 CSV에서 추출
                    best_str  = ""
                    worst_str = ""
                    try:
                        if os.path.exists(LOG_FILE):
                            import csv as _csv
                            today_str = str(date.today())
                            sells = []
                            with open(LOG_FILE, encoding="utf-8") as _f:
                                for row in _csv.DictReader(_f):
                                    if row.get("side") == "SELL" and row.get("datetime","").startswith(today_str):
                                        try: sells.append(float(row["pnl_krw"]))
                                        except: pass
                            if sells:
                                best_str  = f"\n최고 거래 : +{max(sells):,.0f}원"
                                worst_str = f"\n최대 손실 : {min(sells):+,.0f}원"
                    except: pass
                    send_msg(
                        f"📅 오늘 매매 종료 요약\n"
                        f"──────────────────\n"
                        f"거래횟수 : {trade_count}회\n"
                        f"승 / 패  : {win_count}승 {loss_count}패 (승률 {win_rate:.0f}%)\n"
                        f"오늘 손익: {daily_pnl_krw:+,.0f}원"
                        f"{best_str}{worst_str}\n"
                        f"주간 누적: {weekly_pnl_krw:+,.0f}원",
                        level="normal"
                    )
                    _daily_report_sent = True
                poll_sleep(60); continue

            if now_dt < m_open:
                # (아침 알림 로직은 기존대로 유지)
                poll_sleep(10); continue

            # 3. 실시간 데이터 수집 (딱 한 번만!)
            price, volume = get_price_and_volume(STOCK_CODE)
            if price == 0:
                poll_telegram(); time.sleep(1); continue

            # 💡 [핵심] 변동성(ATR) 실시간 업데이트
            if not bot["has_stock"]:
                update_dynamic_parameters(price)

            # 4. 히스토리 업데이트 및 카운트
            _last_price = price
            now_ts = time.time()
            price_history.append(price)
            timed_prices.append((now_ts, price))
            volume_history.append(volume)

            vwap = update_vwap(price, volume)
            vol_ratio = calc_volume_ratio(volume)
            _real_data_count += 1

            if _real_data_count == REAL_DATA_MIN:
                send_msg(f"✅ 진짜 데이터 {REAL_DATA_MIN}개 수집 완료!", level="normal")

            # 5. 지표 계산
            ma20 = np.mean(list(price_history)[-MA_PERIOD_SHORT:]) if len(price_history) >= MA_PERIOD_SHORT else None
            ma60 = np.mean(list(price_history)[-MA_PERIOD_LONG:]) if len(price_history) >= MA_PERIOD_LONG else None
            rsi  = calc_rsi(price_history, bot["rsi_period"])
            vol  = calc_vol_pct()

            # 디버그 및 상태 저장용 (텔레그램 /status 명령 시 사용됨)
            bot.update({"_ma20": ma20, "_ma60": ma60, "_last_rsi": rsi})

            # 6. 실시간 상태 출력 (터미널용 한 줄 로그)
            pnl_pct = 0.0
            if bot["has_stock"] and bot["buy_price"] > 0:
                base_krw = bot["buy_price"] * bot["filled_qty"]
                pnl_now  = net_diff_krw(bot["buy_price"], price, bot["filled_qty"])
                pnl_pct  = (pnl_now / base_krw * 100) if base_krw else 0
        
            status_line(price, rsi, vol, vwap, vol_ratio, pnl_pct)

            t_no_buy     = now_dt.replace(hour=TIMECUT_NO_BUY[0],     minute=TIMECUT_NO_BUY[1],     second=0)
            t_force_sell = now_dt.replace(hour=TIMECUT_FORCE_SELL[0], minute=TIMECUT_FORCE_SELL[1], second=0)

            # 시간이 다 됐으면 일단 팔고, 이번 루프는 여기서 끝냅니다 (continue)
            if now_dt >= t_force_sell:
                if bot["has_stock"]:
                    do_sell(price, "장마감 강제청산(15:15)")
                time.sleep(LOOP_INTERVAL)
                continue 

            # 8. ── 실제 매수/매도 로직 (타임컷 아래에 배치) ──
            if bot["has_stock"]:
                # ── 포지션 보유 중: 매도 조건 ────────────────────
                base_krw       = bot["buy_price"] * bot["filled_qty"]
                pnl_now        = net_diff_krw(bot["buy_price"], price, bot["filled_qty"])
                profit_pct     = (pnl_now / base_krw * 100) if base_krw else 0
                highest_profit = max(highest_profit, profit_pct)

                if not bot["be_active"] and profit_pct >= bot["be_trigger"]:
                    bot["be_active"] = True
                    send_msg(
                        f"🛡️ 안전장치 켜짐 (수익 {profit_pct:.2f}%)\n→ 매수가 아래로 내려오면 자동 매도해요.",
                        level="normal"
                    )

                if bot["be_active"] and price <= bot["buy_price"]:
                    do_sell(price, "본절 보호"); continue
                if highest_profit >= bot["trail_start"] and profit_pct <= highest_profit - bot["trail_gap"]:
                    do_sell(price, "트레일링 스탑"); continue
                if profit_pct >= bot["target"]:
                    do_sell(price, "목표 익절"); continue
                if profit_pct <= bot["max_loss"]:
                    do_sell(price, "최대 손절"); continue
                # [41] 포지션 타임아웃
                if check_pos_timeout(price):
                    elapsed_min = int((time.time() - _buy_time) / 60)
                    send_msg(
                        f"⏱️ 포지션 타임아웃 ({elapsed_min}분 경과)\n"
                        f"→ 방향 없는 포지션 청산해요.\n"
                        f"현재 손익: {profit_pct:+.2f}%",
                        level="normal"
                    )
                    do_sell(price, "포지션 타임아웃"); continue
                pass
            else:
                # ── 8. 포지션 없음: 매수 신호 탐색 ──────────────────
            
                # [안전장치] 진짜 데이터 부족 시 대기
                if _real_data_count < REAL_DATA_MIN:
                    remaining = REAL_DATA_MIN - _real_data_count
                    if int(time.time()) % 10 == 0:
                        print(f"\r⏳ 데이터 수집 중... ({_real_data_count}/{REAL_DATA_MIN}){' '*10}", end="", flush=True)
                    bot["prev_rsi2"], bot["prev_rsi"] = bot["prev_rsi"], rsi
                    time.sleep(LOOP_INTERVAL); continue

                # [Warm-up] 장 시작 직후 WARMUP_MINUTES 동안 매수 금지 (지표 안정화)
                t_market_open = now_dt.replace(hour=MARKET_OPEN[0], minute=MARKET_OPEN[1], second=0)
                t_warmup_end  = t_market_open + timedelta(minutes=WARMUP_MINUTES)
                if t_market_open <= now_dt < t_warmup_end:
                    remaining_warmup = int((t_warmup_end - now_dt).total_seconds())
                    if int(time.time()) % 30 == 0:
                        print(f"\r⏳ 웜업 중... {remaining_warmup}초 후 매수 활성화{chr(32)*10}", end="", flush=True)
                    if _pause_alert_sent != "warmup":
                        send_msg(
                            f"⏳ 장 초반 안정화 중 (09:00 ~ 09:{WARMUP_MINUTES:02d})\n"
                            f"→ 지표가 자리잡을 때까지 {WARMUP_MINUTES}분간 매수를 쉬어요.\n"
                            f"→ 09:{WARMUP_MINUTES:02d} 이후 자동으로 매수를 시작해요.",
                            level="silent"
                        )
                        _pause_alert_sent = "warmup"
                    bot["prev_rsi2"], bot["prev_rsi"] = bot["prev_rsi"], rsi
                    time.sleep(LOOP_INTERVAL); continue

                # [시간 체크] 매수 금지 시간 및 점심 시간
                t_lunch_s = now_dt.replace(hour=LUNCH_START[0], minute=LUNCH_START[1], second=0)
                t_lunch_e = now_dt.replace(hour=LUNCH_END[0],   minute=LUNCH_END[1],   second=0)
            
                if t_lunch_s <= now_dt < t_lunch_e:
                    if _pause_alert_sent != "lunch":
                        send_msg(
                            f"🍱 점심시간 (12:00 ~ 13:00)\n"
                            f"→ 이 시간엔 거래량이 적어 매수를 쉬어요.\n"
                            f"→ 오후 1시 이후 자동으로 재개해요.",
                            level="silent"
                        )
                        _pause_alert_sent = "lunch"
                    bot["prev_rsi2"], bot["prev_rsi"] = bot["prev_rsi"], rsi
                    time.sleep(LOOP_INTERVAL); continue

                if now_dt >= t_no_buy:
                    if _pause_alert_sent != "nobuy":
                        send_msg(
                            f"🕒 장 마감 준비 (15:10 이후)\n"
                            f"→ 마감이 가까워 신규 매수를 멈춰요.\n"
                            f"→ 보유 중인 주식은 15:15에 자동으로 팔아요.",
                            level="silent"
                        )
                        _pause_alert_sent = "nobuy"
                    bot["prev_rsi2"], bot["prev_rsi"] = bot["prev_rsi"], rsi
                    time.sleep(LOOP_INTERVAL); continue

                # 중단 구간 벗어남 → 플래그 리셋 (재개 알림)
                if _pause_alert_sent in ("warmup", "lunch", "nobuy"):
                    resume_msg = {
                        "warmup": "✅ 장 초반 안정화 완료! 지금부터 매수 신호를 찾아요.",
                        "lunch":  "✅ 점심시간 종료! 오후 매매를 재개해요.",
                        "nobuy":  "✅ 장 마감 준비 해제 (이 메시지는 정상적으로 표시되지 않아야 함)",
                    }.get(_pause_alert_sent, "")
                    if resume_msg:
                        send_msg(resume_msg, level="silent")
                    _pause_alert_sent = ""

                # [제한 체크] 쿨타임 및 최대 거래 횟수
                if (time.time() - _last_sell_time < COOLDOWN_SEC) or (trade_count >= MAX_TRADE_COUNT):
                    bot["prev_rsi2"], bot["prev_rsi"] = bot["prev_rsi"], rsi
                    time.sleep(LOOP_INTERVAL); continue

                # [지표 분석]
                if ma20 and ma60 and rsi is not None and vol is not None:
                    drop  = ((ma20 - price) / ma20) * 100
                    prev1 = bot["prev_rsi"]
                    prev2 = bot["prev_rsi2"]

                    # RSI V-Turn 판단
                    rsi_v_turn = (
                        prev2 is not None and prev1 is not None and
                        prev2 <= bot["rsi_buy"] and prev1 > prev2 and rsi > prev1
                    )

                    # 5분 다이버전스 업데이트 및 체크
                    # RSI가 기준 이하이고 이전보다 낮을 때 저점 기록 (실제 최저점 포착)
                    if prev1 is not None and rsi <= bot["rsi_buy"] and rsi < prev1:
                        update_5m_trough(now_ts, price, rsi)
                    elif prev1 is not None and prev1 <= bot["rsi_buy"] and rsi > prev1:
                        update_5m_trough(now_ts, price, prev1)
                    divergence_ok = check_5m_divergence()

                    # 상세 로그 출력 (RSI 근접 시)
                    if rsi <= bot["rsi_buy"] + 5:
                        detail_log(price, rsi, vol, drop, ma20, ma60, vwap, vol_ratio, rsi_v_turn, prev1, prev2)

                    # [필터링] 변동성 범위 체크 (너무 조용하거나 너무 급등락)
                    if vol is not None and (vol < VOL_MIN_PCT or vol > VOL_MAX_PCT):
                        bot["prev_rsi2"], bot["prev_rsi"] = bot["prev_rsi"], rsi
                        time.sleep(LOOP_INTERVAL); continue

                    vwap_ok = (not VWAP_FILTER) or (not vwap) or (price <= vwap)
                    volr_ok = (vol_ratio is None) or (vol_ratio >= VOL_RATIO_MIN)
                    ma_ok   = bool(ma20 and ma60 and ma20 > ma60)
                    drop_ok = drop >= bot["drop"]

                    # ✅ [최종 진입 승인] ma20>ma60 상승추세 + 눌림 + RSI반등 + 다이버전스 + VWAP + 거래량
                    if rsi_v_turn and divergence_ok and vwap_ok and volr_ok and ma_ok and drop_ok:
                        if do_buy(price, "RSI-V-Turn + Div"):
                            highest_profit = 0
                            bot["be_active"] = False
                            _buy_time = time.time()

                # 💡 다음 루프를 위해 RSI 기록 업데이트
                bot["prev_rsi2"], bot["prev_rsi"] = bot["prev_rsi"], rsi
            time.sleep(LOOP_INTERVAL)

        except Exception as e:
                tb = traceback.format_exc()
                msg = (
                    f"🚨 봇 오류 발생\n→ {e}\n"
                    f"→ 5초 후 자동 재시도. 반복되면 봇을 재시작해 주세요."
                )
                cprint(f"\n[봇 오류 발생]\n{tb}", Fore.RED, bright=True)
                try:
                    requests.post(
                        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                        json={"chat_id": CHAT_ID, "text": msg, "disable_notification": False},
                        timeout=5
                    )
                except: pass
                time.sleep(5)

if __name__ == "__main__":
    run_bot()
