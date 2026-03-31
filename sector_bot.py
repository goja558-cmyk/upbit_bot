"""
==============================================================
  섹터 로테이션 자동매매 봇 v1.0
  전략서 기반 구현 — 기존 kis_bot.py KIS API 인프라 활용

  ▶ 전략 요약:
    - ETF 10종 중 모멘텀 상위 2~3개만 보유
    - 20일+60일 수익률 가중 모멘텀 스코어
    - 킬 스위치 / MDD -10% 전량 KOFR 대피
    - 트레일링 스탑 (+6% 후 고점 -2%)
    - 손절 -5%
    - 10만원 소액 테스트 — 종목당 최소 단위 분배

  ▶ 실행:
    python sector_bot.py

  ▶ 설정 파일: sector_cfg.yaml (아래 양식 참고)
    app_key:        "여기에_KIS_APP_KEY"
    app_secret:     "여기에_KIS_APP_SECRET"
    account_no:     "12345678"     # 계좌번호 앞 8자리
    account_suffix: "01"           # 계좌번호 뒤 2자리
    telegram_token: "봇토큰"
    chat_id:        "내채팅ID"
    mock:           true           # true=모의투자 false=실투자
    total_budget:   100000         # 전체 예산(원)
==============================================================
"""

BOT_VERSION = "1.0"
BOT_NAME    = "섹터로테이션봇"
BOT_TAG     = "📊 섹터"

import os, sys, time, json, yaml, requests, threading, math, traceback
from datetime import datetime, date, timedelta
from colorama import Fore, Style, init as colorama_init

colorama_init(autoreset=True)

# ============================================================
# [1] 경로 설정
# ============================================================
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
CFG_FILE   = os.path.join(BASE_DIR, "sector_cfg.yaml")
LOG_FILE   = os.path.join(BASE_DIR, "sector_trade_log.csv")
STATE_FILE = os.path.join(BASE_DIR, "sector_state.json")
SHARED_DIR = os.path.join(BASE_DIR, "shared")
os.makedirs(SHARED_DIR, exist_ok=True)


def cprint(msg, color=Fore.WHITE, bright=False):
    prefix = Style.BRIGHT if bright else ""
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"{prefix}{color}[{ts}] {msg}{Style.RESET_ALL}")


# ============================================================
# [2] ETF 유니버스 (전략서 4.1)
# ============================================================
ETF_UNIVERSE = {
    "396500": {"name": "TIGER 반도체TOP10",    "max_weight": 0.25, "tag": "성장"},
    "152100": {"name": "PLUS K방산",           "max_weight": 0.15, "tag": "모멘텀"},
    "494670": {"name": "TIGER 조선TOP10",      "max_weight": 0.15, "tag": "모멘텀"},
    "143860": {"name": "TIGER 헬스케어",       "max_weight": 0.15, "tag": "방어"},
    "305720": {"name": "KODEX 2차전지산업",    "max_weight": 0.05, "tag": "고위험"},
    "445290": {"name": "KODEX 로봇액티브",     "max_weight": 0.05, "tag": "고위험"},
    "381170": {"name": "HANARO 원자력iSelect", "max_weight": 0.15, "tag": "성장"},
    "091170": {"name": "TIGER 은행",           "max_weight": 0.15, "tag": "방어"},
    "227560": {"name": "TIGER 200 생활소비재", "max_weight": 0.15, "tag": "방어"},
}
KOFR_CODE    = "449170"
KOFR_NAME    = "TIGER KOFR금리액티브"
INVERSE_CODE = "114800"
INVERSE_NAME = "KODEX 인버스"

# ============================================================
# [3] 전략 파라미터
# ============================================================
MOMENTUM_DAYS_SHORT  = 20     # 단기 모멘텀 기간
MOMENTUM_DAYS_LONG   = 60     # 장기 모멘텀 기간
MOMENTUM_W_SHORT     = 0.5
MOMENTUM_W_LONG      = 0.5
TOP_N_ETF            = 2      # 상위 몇 개 편입 (10만원이라 2개)
MIN_SCORE_THRESHOLD  = 0.0    # 이하면 대피

TRAIL_START_PCT  = 6.0        # 트레일링 스탑 시작 수익률 (%)
TRAIL_GAP_PCT    = 2.0        # 트레일링 스탑 간격 (%)
STOP_LOSS_PCT    = -5.0       # 손절 (%)
MAX_DD_PCT       = -10.0      # 계좌 MDD 한도 (%)
KILL_DAY_LOSS    = -3.0       # 킬 스위치 일일 손실 (%)

# ── 대피 단계 파라미터 ─────────────────────────────────────
D1_KOSPI_PCT   = -1.5   # NORMAL→DEFENSE_1 코스피 조건
D1_PF_PCT      = -2.0   # NORMAL→DEFENSE_1 포트폴리오 수익률 조건
D2_KOSPI_PCT   = -2.0   # DEFENSE_1→2 코스피 조건
D2_CONSEC      = 2      # DEFENSE_1→2 연속하락일 조건
D3_KOSPI_PCT   = -3.0   # DEFENSE_2→3 코스피 조건
D3_CONSEC      = 3      # DEFENSE_2→3 연속하락일 조건

D_DOWN_3TO2    = -1.5   # DEFENSE_3→2 완화 조건 (코스피)
D_DOWN_2TO1    = -0.5   # DEFENSE_2→1 완화 조건 (코스피)
D_DOWN_1WAIT   =  0.0   # DEFENSE_1→복귀대기 조건 (코스피)

INVERSE_TRAIL_GAP   = 2.0    # 인버스 트레일링 스탑 간격 (%)
INVERSE_STOP_LOSS   = -4.0   # 인버스 손절 (%)

RESUME_KOSPI_PCT    =  1.0   # 자동 복귀 코스피 조건
RESUME_VOL_RATIO    =  1.2   # 자동 복귀 거래량 비율
OVERHEAT_PCT     = 15.0       # 과열 필터: 5일 수익률 초과 시 매수 금지

COOLDOWN_DAYS    = 14         # 섹터 쿨다운 일수

TRADE_START_H    = 9
TRADE_START_M    = 10
TRADE_BUY_END_H  = 15
TRADE_BUY_END_M  = 20
TRADE_SELL_END_H = 15
TRADE_SELL_END_M = 25
NO_BUY_FRIDAY    = True

MIN_VOLUME_KRW   = 5_000_000_000  # 일 거래대금 50억
MAX_SPREAD_PCT   = 0.5
KOFR_MIN_RATIO   = 0.15           # KOFR 상시 최소 비중

# ============================================================
# [4] 전역 상태
# ============================================================
_cfg             = {}
TELEGRAM_TOKEN   = ""
CHAT_ID          = ""
TOTAL_BUDGET     = 100_000
IS_MOCK          = True

portfolio        = {}     # {code: {qty, avg_price, high_price, entry_date}}
cooldown_list    = {}     # {code: date until}
kill_switch_active = False
mdd_active       = False
peak_value       = 0.0
initial_value    = 0.0
daily_pnl_krw    = 0
daily_loss_base  = 100_000
last_reset_day   = None
trade_count      = 0

# ── 대피 시스템 상태 ──────────────────────────────────────
# defense_stage: NORMAL / DEFENSE_1 / DEFENSE_2 / DEFENSE_3 / DEFENSE_WAIT
defense_stage        = "NORMAL"
kospi_baseline       = 0.0      # 당일 장 시작 시 코스피 기준가
_kospi_baseline_set  = False    # 당일 기준가 캡처 여부
consecutive_down_days = 0       # 코스피 연속 하락일수
inverse_peak_return  = 0.0      # 인버스 보유 중 고점 수익률
defense_down_date    = None     # 단계 하향 조건 충족 시작일 (1일 유지 확인)
_last_update_id  = 0
_tg_lock         = threading.Lock()

# KIS 인증
_access_token    = ""
_token_expire    = 0.0

# Rate limit
_bucket_tokens   = 4.0
_bucket_last     = time.time()
_BUCKET_CAP      = 4
_BUCKET_RATE     = 3.0

# ============================================================
# [4-B] IPC: 매니저 ↔ 섹터봇 통신
#   - upbit_bot.py 와 동일한 패턴 사용
#   - cmd 파일명: shared/cmd_stock.json  (manager가 stock 워커에 쓰는 경로)
#   - result 파일명: shared/result_stock.json
# ============================================================
_IPC_CMD_FILE    = None
_IPC_RESULT_FILE = None
_IPC_REQ_ID      = ""
_is_ipc_context  = False
_IPC_THREAD_STARTED = False
_MANAGER_PID_FILE   = os.path.join(SHARED_DIR, "manager.pid")


def _init_ipc():
    global _IPC_CMD_FILE, _IPC_RESULT_FILE
    _IPC_CMD_FILE    = os.path.join(SHARED_DIR, "cmd_stock.json")
    _IPC_RESULT_FILE = os.path.join(SHARED_DIR, "result_stock.json")


def _manager_is_running():
    if not os.path.exists(_MANAGER_PID_FILE):
        return False
    try:
        with open(_MANAGER_PID_FILE) as f:
            pid = int(f.read().strip())
        return os.path.exists(f"/proc/{pid}")
    except Exception:
        return False


def _get_result_file():
    if not _IPC_RESULT_FILE:
        return None
    if _IPC_REQ_ID:
        base = _IPC_RESULT_FILE.replace(".json", "")
        return f"{base}_{_IPC_REQ_ID}.json"
    return _IPC_RESULT_FILE


def _atomic_write_result(data: dict):
    rf = _get_result_file()
    if not rf:
        return
    tmp = rf + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, rf)
    except Exception as e:
        cprint(f"[IPC 쓰기 오류] {e}", Fore.YELLOW)


def _write_ipc_result(result_text, keyboard=None):
    """IPC 컨텍스트일 때만 결과 파일에 씀."""
    if not _is_ipc_context:
        return
    _atomic_write_result({"result": result_text, "keyboard": keyboard, "ts": time.time()})


def _start_ipc_thread():
    """매니저 명령을 0.3초마다 폴링하는 스레드."""
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
                            globals()["_IPC_REQ_ID"]     = req_id
                            globals()["_is_ipc_context"] = True
                            try:
                                _handle_ipc_cmd(cmd_text)
                            finally:
                                globals()["_is_ipc_context"] = False
                                globals()["_IPC_REQ_ID"]     = ""
                    except Exception as e:
                        cprint(f"[IPC 처리 오류] {e}", Fore.YELLOW)
                        try:
                            os.remove(tmp)
                        except Exception:
                            pass
            except Exception as e:
                cprint(f"[IPC 루프 오류] {e}", Fore.YELLOW)
            time.sleep(0.3)

    t = threading.Thread(target=_ipc_loop, daemon=True, name="ipc-sector")
    t.start()
    cprint("✅ IPC 스레드 시작 (cmd_stock.json 감시 중)", Fore.CYAN)


def _handle_ipc_cmd(text):
    """매니저에서 받은 명령 처리 → _write_ipc_result로 응답."""
    global kill_switch_active, mdd_active  # elif 안 중복 선언 금지 — 최상단에서 한 번만
    cmd = text.strip().split()
    if not cmd:
        return
    c = cmd[0].lower()

    if c in ("/status", "/s", "/상태"):
        _ipc_send_status()
    elif c in ("/portfolio", "/p", "/포트"):
        _ipc_send_portfolio()
    elif c in ("/scores", "/score", "/스코어"):
        _ipc_send_scores()
    elif c in ("/why", "/왜", "/왜안사"):
        holdings = list(portfolio.keys())
        stage = defense_stage
        scores = get_all_scores() or {}
        ranked = sorted(scores.items(), key=lambda x: x[1]["score"], reverse=True)
        lines = [
            f"🔍 왜 안사? [{stage}]",
            f"━━━━━━━━━━━━━━━━━━━━",
        ]
        if stage != "NORMAL":
            lines.append(f"❌ 대피 단계: {stage} → 매수 불가")
        elif kill_switch_active:
            lines.append("❌ 킬스위치 ON → 매수 불가")
        elif mdd_active:
            lines.append("❌ MDD 한도 초과 → 매수 불가")
        else:
            lines.append("✅ 매수 가능 상태")
        lines.append("─────────────────")
        if ranked:
            lines.append("📊 모멘텀 스코어 상위 3개:")
            for i, (code, d) in enumerate(ranked[:3], 1):
                held = "📦" if code in holdings else ""
                cd = "⏸쿨다운" if code in cooldown_list else ""
                lines.append(f"{i}. {ETF_UNIVERSE.get(code,{}).get('name',code)}{held}{cd}: {d['score']:+.1f}%")
        _write_ipc_result("[normal] " + "\n".join(lines))
    elif c in ("/bollinger", "/bb"):
        holdings = list(portfolio.keys())
        msg = "\n\n".join(get_bollinger_status(c) for c in holdings) if holdings else "보유없음"
        _write_ipc_result("[normal] " + msg)
    elif c in ("/investor", "/수급"):
        holdings = list(portfolio.keys())
        msg = "\n\n".join(get_investor_status(c) for c in holdings) if holdings else "보유없음"
        _write_ipc_result("[normal] " + msg)
    elif c in ("/rebalance", "/r", "/리밸"):
        _write_ipc_result("[normal] 🔄 리밸런싱 시작 (백그라운드)...")
        threading.Thread(target=_do_rebalance, daemon=True).start()
    elif c in ("/kofr", "/대피"):
        _write_ipc_result("[critical] 🚨 DEFENSE_1 대피 실행 중...")
        threading.Thread(target=lambda: _enter_defense_1("매니저 명령"), daemon=True).start()
    elif c in ("/resume", "/복귀"):
        _write_ipc_result("[normal] 🟢 복귀 실행 중...")
        threading.Thread(target=_do_resume, daemon=True).start()
    elif c in ("/defense", "/대피단계"):
        _write_ipc_result(f"[normal] 🛡 현재 대피 단계: {defense_stage}")
    elif c in ("/kill", "/킬"):
        kill_switch_active = True
        _save_state()
        _write_ipc_result("[critical] 🔴 킬 스위치 ON")
    elif c in ("/unkill", "/킬해제"):
        kill_switch_active = False
        _save_state()
        _write_ipc_result("[normal] 🟢 킬 스위치 OFF — 매매 재개")
    elif c in ("/start",):
        kill_switch_active = False
        mdd_active = False
        _save_state()
        _write_ipc_result("[normal] 🟢 섹터봇 재개됨")
    elif c in ("/stop",):
        kill_switch_active = True
        _save_state()
        _write_ipc_result("[critical] ⏹ 섹터봇 정지 (킬스위치 ON)")
    elif c in ("/help", "/도움말"):
        _write_ipc_result(
            "[normal] 📋 섹터봇 명령어\n"
            "/status    — 현재 상태\n"
            "/portfolio — 보유 ETF\n"
            "/scores    — 모멘텀 스코어\n"
            "/rebalance — 수동 리밸런싱\n"
            "/kofr      — DEFENSE_1 대피\n"
            "/resume    — 복귀 (ETF 재매수)\n"
            "/defense   — 현재 대피 단계\n"
            "/kill      — 킬 스위치 ON\n"
            "/unkill    — 킬 스위치 OFF\n"
            "/start     — 재개\n"
            "/stop      — 정지"
        )
    else:
        _write_ipc_result(f"[normal] ⚠️ 알 수 없는 명령: {text}")


def _ipc_send_status():
    val  = _calc_portfolio_value()
    dd   = (val - peak_value) / peak_value * 100 if peak_value > 0 else 0.0
    lines = [
        f"📊 섹터봇 현황",
        f"━━━━━━━━━━━━━━━━━━",
        f"포트폴리오: {val:,.0f}원",
        f"오늘 손익:  {daily_pnl_krw:+,}원",
        f"MDD:        {dd:.1f}%",
        f"킬스위치:   {'🔴 ON' if kill_switch_active else '🟢 OFF'}",
        f"MDD모드:    {'🔴 ON' if mdd_active else '🟢 OFF'}",
        f"대피단계:   {defense_stage}",
        f"보유:       {len(portfolio)}종목",
    ]
    for code, pos in portfolio.items():
        name = ETF_UNIVERSE.get(code, {}).get("name", KOFR_NAME if code == KOFR_CODE else code)
        lines.append(f"  {name}: {pos['qty']}주 @ {pos['avg_price']:,.0f}원")
    _write_ipc_result("[normal] " + "\n".join(lines))


def _ipc_send_portfolio():
    if not portfolio:
        _write_ipc_result("[normal] 보유 ETF 없음 (현금 대기)")
        return
    lines = ["📦 보유 ETF"]
    for code, pos in portfolio.items():
        name = ETF_UNIVERSE.get(code, {}).get("name", KOFR_NAME if code == KOFR_CODE else code)
        lines.append(f"  {name}: {pos['qty']}주 @ {pos['avg_price']:,.0f}원 (편입:{pos['entry_date']})")
    _write_ipc_result("[normal] " + "\n".join(lines))


def _ipc_send_scores():
    """IPC 컨텍스트에서 스코어 계산 — 동기 실행."""
    scores = get_all_scores()
    if not scores:
        _write_ipc_result("[normal] ❌ 스코어 계산 실패 (데이터 없음)")
        return
    ranked = sorted(scores.items(), key=lambda x: x[1]["score"], reverse=True)
    lines  = ["📊 모멘텀 스코어 순위"]
    for i, (code, data) in enumerate(ranked, 1):
        name = ETF_UNIVERSE.get(code, {}).get("name", code)
        cd   = " 🚫쿨다운" if is_cooldown(code) else ""
        held = " 📦보유" if code in portfolio else ""
        lines.append(f"{i}. {name}{cd}{held}: {data['score']:+.1f}%")
    _write_ipc_result("[normal] " + "\n".join(lines))

# ============================================================
# [5] 설정 로드
# ============================================================
def load_config():
    global _cfg, TELEGRAM_TOKEN, CHAT_ID, TOTAL_BUDGET, IS_MOCK
    global initial_value, peak_value, daily_loss_base
    if not os.path.exists(CFG_FILE):
        _make_default_config()
        cprint(f"❌ {CFG_FILE} 파일이 없어서 기본 양식을 생성했습니다.", Fore.RED, bright=True)
        cprint("   sector_cfg.yaml 에 KIS API 키와 텔레그램 정보를 입력 후 재실행하세요.", Fore.YELLOW)
        sys.exit(1)
    with open(CFG_FILE, encoding="utf-8") as f:
        _cfg = yaml.safe_load(f) or {}
    TELEGRAM_TOKEN  = _cfg.get("telegram_token", "")
    CHAT_ID         = str(_cfg.get("chat_id", ""))
    TOTAL_BUDGET    = int(_cfg.get("total_budget", 100_000))
    IS_MOCK         = bool(_cfg.get("mock", True))
    initial_value   = float(_cfg.get("initial_value", TOTAL_BUDGET))
    peak_value      = float(_cfg.get("peak_value",    initial_value))
    daily_loss_base = TOTAL_BUDGET
    _init_ipc()   # IPC 파일 경로 초기화
    cprint(f"✅ 설정 로드 — 예산:{TOTAL_BUDGET:,}원 / {'모의투자' if IS_MOCK else '실투자'}", Fore.GREEN)


def _make_default_config():
    default = {
        "app_key":        "여기에_KIS_APP_KEY",
        "app_secret":     "여기에_KIS_APP_SECRET",
        "account_no":     "12345678",
        "account_suffix": "01",
        "telegram_token": "여기에_텔레그램_봇_토큰",
        "chat_id":        "여기에_내_채팅_ID",
        "mock":           True,
        "total_budget":   100000,
    }
    with open(CFG_FILE, "w", encoding="utf-8") as f:
        yaml.dump(default, f, allow_unicode=True, default_flow_style=False)


# ============================================================
# [6] 인프라: Rate Limit / API / KIS 인증
# ============================================================
def _acquire_token():
    global _bucket_tokens, _bucket_last
    now = time.time()
    _bucket_tokens = min(_BUCKET_CAP, _bucket_tokens + (now - _bucket_last) * _BUCKET_RATE)
    _bucket_last   = now
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
            cprint(f"[API {r.status_code}] {url[-60:]}", Fore.YELLOW)
        except Exception as e:
            cprint(f"[API 오류 {attempt+1}] {e}", Fore.RED)
            time.sleep(2)
    return {}


def _prod_url():
    """주문용 URL — 모의/실투자 구분"""
    return (
        "https://openapivts.koreainvestment.com:29443"
        if IS_MOCK
        else "https://openapi.koreainvestment.com:9443"
    )

def _market_url():
    """시세/차트 조회용 URL — 항상 실서버 (모의서버는 데이터 30일 제한)"""
    return "https://openapi.koreainvestment.com:9443"


def get_kis_token():
    global _access_token, _token_expire
    if _access_token and time.time() < _token_expire:
        return _access_token
    res = api_call("post", f"{_prod_url()}/oauth2/tokenP", json={
        "grant_type": "client_credentials",
        "appkey":     _cfg.get("app_key", ""),
        "appsecret":  _cfg.get("app_secret", ""),
    })
    _access_token = res.get("access_token", "")
    _token_expire = time.time() + 3600 * 11
    if _access_token:
        cprint("✅ KIS 토큰 발급 성공", Fore.GREEN)
    else:
        cprint("❌ KIS 토큰 발급 실패 — API 키를 확인하세요", Fore.RED)
    return _access_token


def kis_headers(tr_id):
    tok = get_kis_token()
    if not tok:
        return None
    return {
        "Content-Type":  "application/json; charset=utf-8",
        "authorization": f"Bearer {tok}",
        "appkey":        _cfg.get("app_key", ""),
        "appsecret":     _cfg.get("app_secret", ""),
        "tr_id":         tr_id,
        "custtype":      "P",
    }


def _acnt():
    return _cfg.get("account_no", ""), _cfg.get("account_suffix", "01")


# ============================================================
# [7] 텔레그램
# ============================================================
def send_msg(text, force=False):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        cprint(f"[TG 미설정] {text[:80]}", Fore.YELLOW)
        return
    h = datetime.now().hour
    if 2 <= h < 7 and not force:
        return
    tagged = f"[{BOT_TAG}]\n{text}"
    with _tg_lock:
        for attempt in range(2):
            try:
                res = requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                    json={"chat_id": CHAT_ID, "text": tagged[:4000]},
                    timeout=5,
                )
                if res.status_code == 200:
                    return
            except Exception as e:
                cprint(f"[TG 오류] {e}", Fore.YELLOW)
            time.sleep(1)


def poll_telegram():
    global _last_update_id
    if not TELEGRAM_TOKEN or not CHAT_ID:
        return
    try:
        res = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
            params={"offset": _last_update_id + 1, "timeout": 2},
            timeout=5,
        )
        if res.status_code != 200:
            return
        for upd in res.json().get("result", []):
            _last_update_id = upd["update_id"]
            msg  = upd.get("message", {})
            text = msg.get("text", "").strip()
            if str(msg.get("chat", {}).get("id", "")) != CHAT_ID:
                continue
            if text:
                _handle_cmd(text.split())
    except Exception:
        pass


def _handle_cmd(cmd):
    global kill_switch_active   # elif 블록 안 global은 Python 오류 — 최상단 선언 필수
    if not cmd:
        return
    c = cmd[0].lower()

    if c in ("/status", "/s", "/상태"):
        _send_status()
    elif c in ("/portfolio", "/p", "/포트"):
        _send_portfolio()
    elif c in ("/scores", "/score", "/스코어"):
        _send_scores()
    elif c in ("/why", "/왜", "/왜안사"):
        holdings = list(portfolio.keys())
        stage = defense_stage
        scores = get_all_scores() or {}
        ranked = sorted(scores.items(), key=lambda x: x[1]["score"], reverse=True)
        lines = [f"🔍 왜 안사? [{stage}]", "━━━━━━━━━━━━━━━━━━━━"]
        if stage != "NORMAL":
            lines.append(f"❌ 대피 단계: {stage}")
        elif kill_switch_active:
            lines.append("❌ 킬스위치 ON")
        elif mdd_active:
            lines.append("❌ MDD 한도 초과")
        else:
            lines.append("✅ 매수 가능 상태")
        lines.append("─────────────────")
        for i, (code, d) in enumerate(ranked[:3], 1):
            held = "📦" if code in holdings else ""
            lines.append(f"{i}. {ETF_UNIVERSE.get(code,{}).get('name',code)}{held}: {d['score']:+.1f}%")
        send_msg("\n".join(lines), force=True)
    elif c in ("/bollinger", "/bb"):
        for code in (list(portfolio.keys()) or ["보유없음"]):
            send_msg(get_bollinger_status(code) if code != "보유없음" else "보유없음", force=True)
    elif c in ("/investor", "/수급"):
        for code in (list(portfolio.keys()) or ["보유없음"]):
            send_msg(get_investor_status(code) if code != "보유없음" else "보유없음", force=True)
    elif c in ("/rebalance", "/r", "/리밸"):
        send_msg("🔄 수동 리밸런싱 실행 중...", force=True)
        threading.Thread(target=_do_rebalance, daemon=True).start()
    elif c in ("/kofr", "/대피"):
        send_msg("🚨 DEFENSE_1 대피 실행 중...", force=True)
        threading.Thread(target=lambda: _enter_defense_1("수동 대피"), daemon=True).start()
    elif c in ("/resume", "/복귀"):
        send_msg("🟢 복귀 실행 중...", force=True)
        threading.Thread(target=_do_resume, daemon=True).start()
    elif c in ("/kill", "/킬"):
        kill_switch_active = True
        _save_state()
        send_msg("🔴 킬 스위치 ON — 모든 매매 중단\n/unkill 로 해제", force=True)
    elif c in ("/unkill", "/킬해제"):
        kill_switch_active = False
        _save_state()
        send_msg("🟢 킬 스위치 OFF — 매매 재개", force=True)
    elif c in ("/help", "/도움말"):
        send_msg(
            "📋 명령어 목록\n"
            "/status    — 현재 상태\n"
            "/portfolio — 보유 ETF\n"
            "/scores    — 모멘텀 스코어 순위\n"
            "/rebalance — 수동 리밸런싱\n"
            "/kofr      — DEFENSE_1 대피\n"
            "/resume    — 복귀 (ETF 재매수)\n"
            "/kill      — 킬 스위치 ON\n"
            "/unkill    — 킬 스위치 OFF",
            force=True,
        )


# ============================================================
# [8] 시장 데이터
# ============================================================
def get_price_info(code):
    """현재가, 전일종가, 거래대금, 호가 조회"""
    h = kis_headers("FHKST01010100")
    if not h:
        return {}
    res = api_call(
        "get",
        f"{_prod_url()}/uapi/domestic-stock/v1/quotations/inquire-price",
        headers=h,
        params={"fid_cond_mrkt_div_code": "J", "fid_input_iscd": code},
    )
    out = res.get("output", {})
    if not out:
        return {}
    try:
        return {
            "price":      int(out.get("stck_prpr",    0) or 0),
            "prev_close": int(out.get("stck_sdpr",    0) or 0),
            "bid":        int(out.get("askp",          0) or 0),
            "ask":        int(out.get("bidp",          0) or 0),
            "volume_krw": int(out.get("acml_tr_pbmn",  0) or 0),
            "open":       int(out.get("stck_oprc",     0) or 0),
            "high":       int(out.get("stck_hgpr",     0) or 0),
            "low":        int(out.get("stck_lwpr",     0) or 0),
        }
    except Exception as e:
        cprint(f"[가격 파싱 오류 {code}] {e}", Fore.YELLOW)
        return {}


def get_daily_chart(code, n_days=70):
    """일봉 종가 리스트 반환 (오래된 것 먼저)
    KIS API는 1회 호출당 최대 30건 반환 → 필요한 만큼 반복 호출해서 합산."""
    h = kis_headers("FHKST01010400")
    if not h:
        return []

    closes_all = []
    date_to    = date.today()

    for _ in range(5):   # 최대 5회 (30개×5 = 150일치)
        date_from = date_to - timedelta(days=50)
        res = api_call(
            "get",
            f"{_prod_url()}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
            headers=h,
            params={
                "fid_cond_mrkt_div_code": "J",
                "fid_input_iscd":         code,
                "fid_input_date_1":       date_from.strftime("%Y%m%d"),
                "fid_input_date_2":       date_to.strftime("%Y%m%d"),
                "fid_period_div_code":    "D",
                "fid_org_adj_prc":        "0",
            },
        )
        output = res.get("output2", []) or res.get("output", [])
        if not output:
            break

        batch = []
        for row in output:
            try:
                c = int(row.get("stck_clpr", 0) or 0)
                if c > 0:
                    batch.append(c)
            except:
                pass

        if not batch:
            break

        # output2는 최신→과거 순서로 오므로 앞에 붙임
        closes_all = batch + closes_all

        if len(closes_all) >= n_days:
            break

        date_to = date_from - timedelta(days=1)
        time.sleep(0.2)

    return closes_all[-n_days:]


def get_kospi_info():
    """코스피 현재 정보 반환: price, change_pct(전일대비), volume"""
    h = kis_headers("FHKUP03500100")
    if not h:
        return {}
    res = api_call(
        "get",
        f"{_prod_url()}/uapi/domestic-stock/v1/quotations/inquire-index-price",
        headers=h,
        params={"fid_cond_mrkt_div_code": "U", "fid_input_iscd": "0001"},
    )
    out = res.get("output", {})
    try:
        return {
            "price":      float(out.get("bstp_nmix_prpr",      0) or 0),
            "change_pct": float(out.get("bstp_nmix_prdy_ctrt", 0) or 0),
            "volume":     int(out.get("acml_vol",              0) or 0),
        }
    except:
        return {}


def get_kospi_change_pct():
    """코스피 당일 등락률 (전일 종가 대비) — 기존 호환용"""
    return get_kospi_info().get("change_pct", 0.0)


def get_kospi_intraday_pct():
    """코스피 장중 등락률 — 당일 기준가(kospi_baseline) 대비"""
    if kospi_baseline <= 0:
        return 0.0
    price = get_kospi_info().get("price", 0.0)
    if price <= 0:
        return 0.0
    return (price - kospi_baseline) / kospi_baseline * 100


def get_kospi_volume_ratio():
    """코스피 현재 거래량 / 최근 5일 평균 거래량"""
    h = kis_headers("FHKST03010100")
    if not h:
        return 1.0
    today_str = date.today().strftime("%Y%m%d")
    past_str  = (date.today() - timedelta(days=10)).strftime("%Y%m%d")
    res = api_call(
        "get",
        f"{_prod_url()}/uapi/domestic-stock/v1/quotations/inquire-index-daily-price",
        headers=h,
        params={
            "fid_cond_mrkt_div_code": "U",
            "fid_input_iscd":         "0001",
            "fid_input_date_1":       past_str,
            "fid_input_date_2":       today_str,
            "fid_period_div_code":    "D",
        },
    )
    rows = res.get("output2", []) or res.get("output", [])
    vols = []
    for r in rows:
        try:
            v = int(r.get("acml_vol", 0) or 0)
            if v > 0:
                vols.append(v)
        except:
            pass
    if len(vols) < 2:
        return 1.0
    avg5  = sum(vols[1:6]) / min(len(vols[1:6]), 5)  # 전일 포함 최대 5일
    today = vols[0] if vols else avg5
    return today / avg5 if avg5 > 0 else 1.0


def get_kospi_ma5():
    """코스피 5일 이동평균"""
    h = kis_headers("FHKST03010100")
    if not h:
        return 0.0
    today_str = date.today().strftime("%Y%m%d")
    past_str  = (date.today() - timedelta(days=10)).strftime("%Y%m%d")
    res = api_call(
        "get",
        f"{_prod_url()}/uapi/domestic-stock/v1/quotations/inquire-index-daily-price",
        headers=h,
        params={
            "fid_cond_mrkt_div_code": "U",
            "fid_input_iscd":         "0001",
            "fid_input_date_1":       past_str,
            "fid_input_date_2":       today_str,
            "fid_period_div_code":    "D",
        },
    )
    rows = res.get("output2", []) or res.get("output", [])
    closes = []
    for r in rows[1:6]:   # 오늘 제외, 최근 5거래일
        try:
            c = float(r.get("bstp_nmix_clpr", 0) or 0)
            if c > 0:
                closes.append(c)
        except:
            pass
    return sum(closes) / len(closes) if closes else 0.0


def update_consecutive_down_days():
    """연속하락일수 갱신 — 매일 장 시작 전 1회 호출"""
    global consecutive_down_days
    h = kis_headers("FHKST03010100")
    if not h:
        return
    today_str = date.today().strftime("%Y%m%d")
    past_str  = (date.today() - timedelta(days=15)).strftime("%Y%m%d")
    res = api_call(
        "get",
        f"{_prod_url()}/uapi/domestic-stock/v1/quotations/inquire-index-daily-price",
        headers=h,
        params={
            "fid_cond_mrkt_div_code": "U",
            "fid_input_iscd":         "0001",
            "fid_input_date_1":       past_str,
            "fid_input_date_2":       today_str,
            "fid_period_div_code":    "D",
        },
    )
    rows = res.get("output2", []) or res.get("output", [])
    closes = []
    for r in rows[:8]:
        try:
            c = float(r.get("bstp_nmix_clpr", 0) or 0)
            if c > 0:
                closes.append(c)
        except:
            pass
    # 최신→과거 순 → 연속 하락일 카운트
    count = 0
    for i in range(len(closes) - 1):
        if closes[i] < closes[i + 1]:   # 오늘 < 전일 → 하락
            count += 1
        else:
            break
    consecutive_down_days = count
    cprint(f"[연속하락일] {count}일", Fore.CYAN)


def get_cash_balance():
    """주문 가능 현금 조회"""
    tr = "VTTC8908R" if IS_MOCK else "TTTC8908R"
    h  = kis_headers(tr)
    if not h:
        return 0
    acnt_no, suffix = _acnt()
    res = api_call(
        "get",
        f"{_prod_url()}/uapi/domestic-stock/v1/trading/inquire-psbl-order",
        headers=h,
        params={
            "CANO":         acnt_no,
            "ACNT_PRDT_CD": suffix,
            "PDNO":         "005930",
            "ORD_UNPR":     "0",
            "ORD_DVSN":     "02",
            "CMA_EVLU_AMT_ICLD_YN": "Y",
            "OVRS_ICLD_YN": "N",
        },
    )
    try:
        out = res.get("output", {})
        return int(out.get("max_buy_amt") or out.get("ord_psbl_cash") or 0)
    except:
        return 0


# ============================================================
# [9] 주문
# ============================================================
def get_tick_size(price):
    if price < 2000:    return 1
    if price < 5000:    return 5
    if price < 20000:   return 10
    if price < 50000:   return 50
    if price < 200000:  return 100
    if price < 500000:  return 500
    return 1000


def send_order(code, side, qty, price=0):
    """side: BUY or SELL / price=0 → 시장가"""
    if qty <= 0:
        return False
    tr_map = {
        ("BUY",  True):  "VTTC0802U",
        ("BUY",  False): "TTTC0802U",
        ("SELL", True):  "VTTC0801U",
        ("SELL", False): "TTTC0801U",
    }
    h = kis_headers(tr_map[(side, IS_MOCK)])
    if not h:
        return False
    acnt_no, suffix = _acnt()
    body = {
        "CANO":         acnt_no,
        "ACNT_PRDT_CD": suffix,
        "PDNO":         code,
        "ORD_DVSN":     "01" if price == 0 else "00",
        "ORD_QTY":      str(qty),
        "ORD_UNPR":     str(price) if price > 0 else "0",
    }
    res = api_call("post",
        f"{_prod_url()}/uapi/domestic-stock/v1/trading/order-cash",
        headers=h, json=body,
    )
    if res.get("rt_cd") == "0":
        name = ETF_UNIVERSE.get(code, {}).get("name", KOFR_NAME if code == KOFR_CODE else code)
        cprint(
            f"✅ [{side}] {name} {qty}주 @ {'시장가' if price==0 else f'{price:,}원'}",
            Fore.GREEN, bright=True,
        )
        return True
    cprint(f"❌ 주문 실패 [{side}] {code} — {res.get('msg1','')}", Fore.RED)
    return False



# ============================================================
# [PATCH] 볼린저밴드 + 투자자 동향
# ============================================================
import numpy as _np_ind

_BB_PERIOD = 20
_BB_K      = 2.0
_BB_NEAR   = 0.15   # %B 이하면 하단 근처
_INV_FILTER = True
_INV_CACHE  = {}
_INV_TTL    = 300   # 5분 캐시


def get_minute_candles(code, count=30):
    """당일 분봉 조회 FHKST03010200"""
    from datetime import datetime as _dt
    h = kis_headers("FHKST03010200")
    if not h:
        return []
    now_str = _dt.now().strftime("%H%M%S")
    res = api_call(
        "get",
        f"{_prod_url()}/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice",
        headers=h,
        params={
            "FID_ETC_CLS_CODE":       "",
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD":         code,
            "FID_INPUT_HOUR_1":       now_str,
            "FID_PW_DATA_INCU_YN":    "N",
        }
    )
    try:
        return [
            {
                "open":  int(c.get("stck_oprc", 0)),
                "high":  int(c.get("stck_hgpr", 0)),
                "low":   int(c.get("stck_lwpr", 0)),
                "close": int(c.get("stck_prpr", 0)),
            }
            for c in (res.get("output2", []) or [])[:count]
        ]
    except Exception as e:
        cprint(f"[분봉 오류] {e}", Fore.YELLOW)
        return []


def calc_bollinger(candles, period=20, k=2.0):
    """볼린저밴드 계산. 반환: (upper, mid, lower, pct_b)"""
    if len(candles) < period:
        return None, None, None, None
    closes = [c["close"] for c in candles[-period:]]
    mid    = sum(closes) / period
    std    = float(_np_ind.std(closes))
    upper  = mid + k * std
    lower  = mid - k * std
    cur    = closes[-1]
    pct_b  = (cur - lower) / (upper - lower) if (upper - lower) > 0 else 0.5
    return upper, mid, lower, pct_b


def is_hammer(c):
    body = abs(c["close"] - c["open"])
    if body == 0: return False
    lw = min(c["open"], c["close"]) - c["low"]
    uw = c["high"] - max(c["open"], c["close"])
    return lw >= body * 2 and uw <= body * 0.3


def is_bullish_reversal(candles):
    if len(candles) < 2: return False
    return candles[-2]["close"] < candles[-2]["open"] and candles[-1]["close"] > candles[-1]["open"]


def check_bollinger_signal(code):
    """볼린저 신호. 반환: (통과여부, 메시지)"""
    return True, "BB필터비활성"  # 섹터봇 ETF 매수에는 불필요


def get_investor_flow(code):
    """투자자 동향. 반환: (외국인_순매수, 기관_순매수)"""
    import time as _t
    now = _t.time()
    cached = _INV_CACHE.get(code)
    if cached and now - cached[0] < _INV_TTL:
        return cached[1], cached[2]
    h = kis_headers("FHKST01010900")
    if not h:
        return None, None
    res = api_call(
        "get",
        f"{_prod_url()}/uapi/domestic-stock/v1/quotations/inquire-investor",
        headers=h,
        params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code}
    )
    try:
        out = res.get("output", [])
        row = out[0] if isinstance(out, list) and out else out
        frgn = int(row.get("frgn_ntby_qty", 0))
        inst = int(row.get("orgn_ntby_qty", 0))
        _INV_CACHE[code] = (now, frgn, inst)
        return frgn, inst
    except Exception as e:
        cprint(f"[투자자 동향 오류] {e}", Fore.YELLOW)
        return None, None


def check_investor_signal(code):
    """투자자 필터. 반환: (통과여부, 메시지)"""
    if not _INV_FILTER:
        return True, "필터OFF"
    frgn, inst = get_investor_flow(code)
    if frgn is None:
        return True, "조회실패(통과)"
    msg = f"외국인{frgn:+,} 기관{inst:+,}"
    return (frgn > 0 or inst > 0), msg


def get_bollinger_status(code):
    candles = get_minute_candles(code, 30)
    if not candles:
        return f"{code}: 분봉 없음"
    upper, mid, lower, pct_b = calc_bollinger(candles)
    cur = candles[-1]["close"]
    if pct_b is None:
        return f"{code}: 계산 불가"
    return (
        f"📊 {code} 볼린저\n"
        f"현재가: {cur:,}원\n"
        f"상단: {upper:,.0f}  중간: {mid:,.0f}  하단: {lower:,.0f}\n"
        f"%B: {pct_b:.2f}  망치: {'✅' if candles and is_hammer(candles[-1]) else '❌'}"
        f"  양봉전환: {'✅' if is_bullish_reversal(candles) else '❌'}"
    )


def get_investor_status(code):
    frgn, inst = get_investor_flow(code)
    if frgn is None:
        return f"{code}: 조회 실패"
    return (
        f"👥 {code} 수급\n"
        f"외국인: {frgn:+,}주 {'✅' if frgn > 0 else '❌'}\n"
        f"기관:   {inst:+,}주 {'✅' if inst > 0 else '❌'}\n"
        f"신호: {'✅통과' if (frgn > 0 or inst > 0) else '❌차단'}"
    )

def buy_etf(code, budget_krw):
    """ETF 매수 — 예산 내 최대 수량"""
    info = get_price_info(code)
    if not info or info["price"] <= 0:
        cprint(f"[매수 실패] {code} 가격 조회 실패", Fore.YELLOW)
        return 0
    # ── [PATCH] 볼린저 + 투자자 필터 ──────────────────
    bb_ok,  bb_msg  = check_bollinger_signal(code)
    inv_ok, inv_msg = check_investor_signal(code)
    cprint(f"[매수필터] {code} BB:{bb_msg} 수급:{inv_msg}", Fore.CYAN)
    if not bb_ok:
        send_msg(f"⚠️ {code} 매수차단 — {bb_msg}", force=True)
        return 0
    if not inv_ok:
        send_msg(f"⚠️ {code} 수급차단 — {inv_msg}", force=True)
        return 0
    # ────────────────────────────────────────────────────
    price = info.get("ask") or info["price"]
    qty   = int(budget_krw / price)
    if qty <= 0:
        cprint(f"[매수 건너뜀] {code} 예산 {budget_krw:,}원 < 1주 ({price:,}원)", Fore.YELLOW)
        return 0
    tick        = get_tick_size(price)
    order_price = price + tick
    ok = send_order(code, "BUY", qty, order_price)
    if ok:
        _record_buy(code, order_price, qty)
        _log_trade(code, "BUY", qty, order_price)
    return qty if ok else 0


def sell_etf(code, qty=None, reason=""):
    """ETF 매도 — qty=None이면 전량"""
    pos = portfolio.get(code)
    if not pos:
        return False
    if qty is None:
        qty = pos["qty"]
    if qty <= 0:
        return False
    info  = get_price_info(code)
    price = (info.get("bid") or info.get("price", 0)) if info else 0
    tick  = get_tick_size(price) if price > 0 else 0
    order_price = max(0, price - tick) if price > 0 else 0
    ok = send_order(code, "SELL", qty, order_price)
    if ok:
        pnl = (order_price - pos["avg_price"]) * qty if order_price > 0 else 0
        _record_sell(code, qty, pnl, reason)
        _log_trade(code, "SELL", qty, order_price, pnl=pnl, reason=reason)
    return ok


# ============================================================
# [10] 포트폴리오 기록
# ============================================================
def _record_buy(code, price, qty):
    if code in portfolio:
        old   = portfolio[code]
        total = old["qty"] + qty
        avg   = (old["avg_price"] * old["qty"] + price * qty) / total
        portfolio[code]["qty"]        = total
        portfolio[code]["avg_price"]  = avg
        portfolio[code]["high_price"] = max(old["high_price"], price)
    else:
        portfolio[code] = {
            "qty":        qty,
            "avg_price":  price,
            "high_price": price,
            "entry_date": str(date.today()),
        }
    _save_state()


def _record_sell(code, qty, pnl, reason):
    global daily_pnl_krw, trade_count
    daily_pnl_krw += int(pnl)
    trade_count   += 1
    if code in portfolio:
        remaining = portfolio[code]["qty"] - qty
        if remaining <= 0:
            del portfolio[code]
        else:
            portfolio[code]["qty"] = remaining
    _save_state()


def _log_trade(code, side, qty, price, pnl=0, reason=""):
    name        = ETF_UNIVERSE.get(code, {}).get("name", KOFR_NAME if code == KOFR_CODE else code)
    header_need = not os.path.exists(LOG_FILE)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        if header_need:
            f.write("dt,code,name,side,qty,price,pnl_krw,reason\n")
        f.write(
            f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')},"
            f"{code},{name},{side},{qty},{price},{int(pnl)},{reason}\n"
        )


def _save_state():
    data = {
        "portfolio":            portfolio,
        "cooldown_list":        {k: str(v) for k, v in cooldown_list.items()},
        "kill_switch":          kill_switch_active,
        "mdd_active":           mdd_active,
        "peak_value":           peak_value,
        "initial_value":        initial_value,
        "daily_pnl_krw":        daily_pnl_krw,
        "trade_count":          trade_count,
        "last_reset_day":       str(last_reset_day) if last_reset_day else None,
        "defense_stage":        defense_stage,
        "consecutive_down_days": consecutive_down_days,
        "inverse_peak_return":  inverse_peak_return,
        "defense_down_date":    str(defense_down_date) if defense_down_date else None,
        "ts":                   time.time(),
    }
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)


def _load_state():
    global portfolio, cooldown_list, kill_switch_active, mdd_active
    global peak_value, initial_value, daily_pnl_krw, trade_count, last_reset_day
    global defense_stage, consecutive_down_days, inverse_peak_return, defense_down_date
    if not os.path.exists(STATE_FILE):
        return
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        portfolio             = data.get("portfolio", {})
        raw_cd                = data.get("cooldown_list", {})
        cooldown_list         = {k: date.fromisoformat(v) for k, v in raw_cd.items()}
        kill_switch_active    = data.get("kill_switch", False)
        mdd_active            = data.get("mdd_active", False)
        peak_value            = float(data.get("peak_value",    initial_value))
        initial_value         = float(data.get("initial_value", TOTAL_BUDGET))
        daily_pnl_krw         = int(data.get("daily_pnl_krw",   0))
        trade_count           = int(data.get("trade_count",      0))
        lrd = data.get("last_reset_day")
        last_reset_day        = date.fromisoformat(lrd) if lrd else None
        defense_stage         = data.get("defense_stage", "NORMAL")
        consecutive_down_days = int(data.get("consecutive_down_days", 0))
        inverse_peak_return   = float(data.get("inverse_peak_return", 0.0))
        ddd = data.get("defense_down_date")
        defense_down_date     = date.fromisoformat(ddd) if ddd else None
        cprint(f"✅ 상태 복원 — 포트폴리오 {len(portfolio)}종목 / 대피:{defense_stage}", Fore.GREEN)
    except Exception as e:
        cprint(f"[상태 복원 오류] {e}", Fore.YELLOW)


# ============================================================
# [11] 모멘텀 스코어 계산
# ============================================================
def calc_momentum_score(closes):
    """20일+60일 가중 모멘텀 스코어 (%) — None이면 데이터 부족"""
    if len(closes) < MOMENTUM_DAYS_LONG + 1:
        return None
    c_now = closes[-1]
    c_20  = closes[-(MOMENTUM_DAYS_SHORT + 1)]
    c_60  = closes[-(MOMENTUM_DAYS_LONG  + 1)]
    r20   = (c_now / c_20 - 1) * 100 if c_20 > 0 else 0.0
    r60   = (c_now / c_60 - 1) * 100 if c_60 > 0 else 0.0
    return round(MOMENTUM_W_SHORT * r20 + MOMENTUM_W_LONG * r60, 3)


def _ret_n(closes, n):
    if len(closes) < n + 1:
        return 0.0
    return (closes[-1] / closes[-(n + 1)] - 1) * 100


def get_all_scores():
    """전 ETF 모멘텀 스코어 계산 → {code: {score, ret5, ret20, closes}}"""
    scores = {}
    for code in ETF_UNIVERSE:
        closes = get_daily_chart(code, 70)
        if not closes:
            cprint(f"  [{code}] 데이터 없음", Fore.YELLOW)
            continue
        score = calc_momentum_score(closes)
        if score is None:
            cprint(f"  [{code}] 데이터 부족 ({len(closes)}일)", Fore.YELLOW)
            continue
        scores[code] = {
            "score":  score,
            "ret5":   _ret_n(closes, 5),
            "ret20":  _ret_n(closes, 20),
            "closes": closes,
        }
        time.sleep(0.3)
    return scores


# ============================================================
# [12] 필터
# ============================================================
def is_tradeable_time(for_buy=True):
    now  = datetime.now()
    h, m = now.hour, now.minute
    if h < TRADE_START_H or (h == TRADE_START_H and m < TRADE_START_M):
        return False
    if for_buy:
        if h > TRADE_BUY_END_H or (h == TRADE_BUY_END_H and m >= TRADE_BUY_END_M):
            return False
        if NO_BUY_FRIDAY and now.weekday() == 4:
            return False
    else:
        if h > TRADE_SELL_END_H or (h == TRADE_SELL_END_H and m >= TRADE_SELL_END_M):
            return False
    return True


def is_kospi_no_trade():
    """코스피 ±3% 이상 → 신규 매수 금지"""
    chg = get_kospi_change_pct()
    if abs(chg) >= 3.0:
        cprint(f"[No Trade Zone] 코스피 {chg:+.1f}%", Fore.YELLOW)
        return True
    return False


def is_overheat(closes):
    return _ret_n(closes, 5) > OVERHEAT_PCT


def is_cooldown(code):
    until = cooldown_list.get(code)
    if until and date.today() <= until:
        return True
    if code in cooldown_list:
        del cooldown_list[code]
    return False


def check_liquidity(info):
    if info.get("volume_krw", 0) < MIN_VOLUME_KRW:
        return False
    bid = info.get("bid", 0)
    ask = info.get("ask", 0)
    p   = info.get("price", 1)
    if bid > 0 and ask > 0 and p > 0:
        if (ask - bid) / p * 100 > MAX_SPREAD_PCT:
            return False
    return True


def register_cooldown_if_needed(code, closes):
    """20일 수익률 -10% 이하 → 쿨다운 등록"""
    if _ret_n(closes, 20) <= -10.0:
        until = date.today() + timedelta(days=COOLDOWN_DAYS)
        cooldown_list[code] = until
        cprint(f"[쿨다운 등록] {code} 20일 {_ret_n(closes,20):.1f}% → {until}까지", Fore.YELLOW)
        return True
    return False


# ============================================================
# [13] 킬 스위치 / MDD / 대피 시스템
# ============================================================
def _calc_portfolio_value():
    total = 0
    for code, pos in portfolio.items():
        info  = get_price_info(code)
        raw   = info.get("price", 0) if info else 0
        price = raw if raw > 0 else pos["avg_price"]
        total += price * pos["qty"]
    return total


def check_kill_switch():
    global kill_switch_active
    if daily_loss_base <= 0:
        return kill_switch_active
    loss_pct = daily_pnl_krw / daily_loss_base * 100
    if loss_pct <= KILL_DAY_LOSS and not kill_switch_active:
        kill_switch_active = True
        send_msg(
            f"🔴 킬 스위치 발동!\n"
            f"일일 손실: {daily_pnl_krw:+,}원 ({loss_pct:.1f}%)\n"
            f"→ DEFENSE_1 진입",
            force=True,
        )
        threading.Thread(target=lambda: _enter_defense_1("킬 스위치"), daemon=True).start()
    return kill_switch_active


def check_mdd():
    global mdd_active, peak_value
    if peak_value <= 0:
        return mdd_active
    pf_val = _calc_portfolio_value()
    cash   = get_cash_balance()
    val    = pf_val + cash
    if portfolio and val < TOTAL_BUDGET * 0.2:
        cprint(f"[MDD 스킵] 평가액 이상({val:,}원) — 데이터 오류 의심", Fore.YELLOW)
        return mdd_active
    if val > peak_value:
        peak_value = val
    dd = (val - peak_value) / peak_value * 100
    if dd <= MAX_DD_PCT and not mdd_active:
        mdd_active = True
        send_msg(
            f"🚨 MDD 한도 도달! ({dd:.1f}%)\n→ DEFENSE_1 진입",
            force=True,
        )
        threading.Thread(target=lambda: _enter_defense_1("MDD 한도"), daemon=True).start()
    return mdd_active


# ── 대피 공통 유틸 ─────────────────────────────────────────
def _sell_all_etf(exclude=None):
    """인버스 제외 전 종목 시장가 매도. exclude: 추가 제외 코드 집합."""
    excluded = {INVERSE_CODE}
    if exclude:
        excluded.update(exclude)
    failed = []
    for code in list(portfolio.keys()):
        if code in excluded:
            continue
        ok = sell_etf(code, reason="대피매도")
        if not ok:
            # 재시도 2회
            for _ in range(2):
                time.sleep(1)
                ok = sell_etf(code, reason="대피매도_재시도")
                if ok:
                    break
        if not ok:
            failed.append(code)
        else:
            time.sleep(0.5)
    if failed:
        send_msg(f"⚠️ 매도 실패: {', '.join(failed)}\n수동 처리 필요", force=True)
    return len(failed) == 0


def _get_inverse_return_pct():
    """인버스 ETF 현재 수익률(%)"""
    pos = portfolio.get(INVERSE_CODE)
    if not pos or pos["avg_price"] <= 0:
        return 0.0
    info  = get_price_info(INVERSE_CODE)
    price = (info.get("price", 0) if info else 0) or pos["avg_price"]
    return (price - pos["avg_price"]) / pos["avg_price"] * 100


def _buy_inverse_to_ratio(target_ratio):
    """전체 자산 대비 인버스 비중이 target_ratio가 되도록 추가 매수."""
    cash      = get_cash_balance()
    pf_val    = _calc_portfolio_value()
    total_val = pf_val + cash
    if total_val <= 0:
        return
    inv_pos   = portfolio.get(INVERSE_CODE)
    inv_val   = 0.0
    if inv_pos:
        info    = get_price_info(INVERSE_CODE)
        price   = (info.get("price", 0) if info else 0) or inv_pos["avg_price"]
        inv_val = price * inv_pos["qty"]
    target_val = total_val * target_ratio
    need       = target_val - inv_val
    if need < 5_000:
        return
    buy_etf(INVERSE_CODE, int(need))


def _sell_inverse_by_ratio(sell_ratio):
    """전체 자산의 sell_ratio 만큼 인버스 매도."""
    pos = portfolio.get(INVERSE_CODE)
    if not pos:
        return
    pf_val    = _calc_portfolio_value()
    cash      = get_cash_balance()
    total_val = pf_val + cash
    info      = get_price_info(INVERSE_CODE)
    price     = (info.get("price", 0) if info else 0) or pos["avg_price"]
    if price <= 0:
        return
    sell_val = total_val * sell_ratio
    qty      = min(int(sell_val / price), pos["qty"])
    if qty > 0:
        sell_etf(INVERSE_CODE, qty, reason="단계하향매도")


def _set_defense_stage(new_stage, reason=""):
    global defense_stage, inverse_peak_return, defense_down_date
    old = defense_stage
    defense_stage = new_stage
    if new_stage == "NORMAL":
        inverse_peak_return = 0.0
        defense_down_date   = None
    cprint(f"[대피단계] {old} → {new_stage}  {reason}", Fore.MAGENTA, bright=True)
    send_msg(
        f"🛡 대피 단계 변경\n{old} → {new_stage}\n{reason}",
        force=True,
    )
    _save_state()


# ── 단계 상향 ─────────────────────────────────────────────
def _enter_defense_1(reason=""):
    """NORMAL → DEFENSE_1: 전 ETF 매도 후 현금 100%"""
    if defense_stage != "NORMAL":
        return
    send_msg(f"🚨 DEFENSE_1 진입 — {reason}\nETF 전량 매도 중...", force=True)
    _sell_all_etf()
    _set_defense_stage("DEFENSE_1", reason)


def _enter_defense_2():
    """DEFENSE_1 → DEFENSE_2: 현금 30% 인버스 매수"""
    global inverse_peak_return
    if defense_stage != "DEFENSE_1":
        return
    send_msg("📉 DEFENSE_2 진입 — 인버스 30% 매수", force=True)
    inverse_peak_return = 0.0
    _buy_inverse_to_ratio(0.30)
    _set_defense_stage("DEFENSE_2")


def _enter_defense_3():
    """DEFENSE_2 → DEFENSE_3: 인버스 50%까지 확대"""
    if defense_stage != "DEFENSE_2":
        return
    send_msg("📉 DEFENSE_3 진입 — 인버스 50%까지 확대", force=True)
    _buy_inverse_to_ratio(0.50)
    _set_defense_stage("DEFENSE_3")


# ── 단계 하향 (루프마다 호출) ─────────────────────────────
def _step_down_defense():
    """단계 하향 — 1일 유지 확인 후 10%씩 단계적 매도"""
    global defense_down_date
    kospi_pct = get_kospi_intraday_pct()
    today     = date.today()

    if defense_stage == "DEFENSE_3":
        if kospi_pct > D_DOWN_3TO2:
            if defense_down_date is None:
                defense_down_date = today
            elif today > defense_down_date:        # 1일 유지 확인
                inv_ratio = _current_inverse_ratio()
                if inv_ratio > 0.40:
                    _sell_inverse_by_ratio(0.10)
                elif inv_ratio > 0.30:
                    _sell_inverse_by_ratio(0.10)
                else:
                    _set_defense_stage("DEFENSE_2", "지수 회복")
        else:
            defense_down_date = None

    elif defense_stage == "DEFENSE_2":
        if kospi_pct > D_DOWN_2TO1:
            if defense_down_date is None:
                defense_down_date = today
            elif today > defense_down_date:
                inv_ratio = _current_inverse_ratio()
                if inv_ratio > 0.20:
                    _sell_inverse_by_ratio(0.10)
                elif inv_ratio > 0.0:
                    sell_etf(INVERSE_CODE, reason="단계하향완료")
                else:
                    _set_defense_stage("DEFENSE_1", "인버스 정리 완료")
        else:
            defense_down_date = None

    elif defense_stage == "DEFENSE_1":
        if kospi_pct > D_DOWN_1WAIT:
            if defense_down_date is None:
                defense_down_date = today
            elif today > defense_down_date:
                _set_defense_stage("DEFENSE_WAIT", "복귀 대기 시작")
        else:
            defense_down_date = None


def _current_inverse_ratio():
    """현재 인버스 비중 (0.0 ~ 1.0)"""
    pos = portfolio.get(INVERSE_CODE)
    if not pos:
        return 0.0
    info  = get_price_info(INVERSE_CODE)
    price = (info.get("price", 0) if info else 0) or pos["avg_price"]
    inv_val   = price * pos["qty"]
    pf_val    = _calc_portfolio_value()
    cash      = get_cash_balance()
    total_val = pf_val + cash
    return inv_val / total_val if total_val > 0 else 0.0


# ── 인버스 익절 / 손절 (단계와 독립적으로 항상 체크) ────────
def check_inverse_exit():
    global inverse_peak_return
    if INVERSE_CODE not in portfolio:
        return
    ret = _get_inverse_return_pct()
    # 고점 갱신
    if ret > inverse_peak_return:
        inverse_peak_return = ret
    # 트레일링 익절: 고점 > 0% AND 현재 <= 고점 - 2%
    if inverse_peak_return > 0 and ret <= inverse_peak_return - INVERSE_TRAIL_GAP:
        cprint(f"[인버스 익절] 고점:{inverse_peak_return:.1f}% 현재:{ret:.1f}%", Fore.CYAN)
        send_msg(
            f"💰 인버스 트레일링 익절\n고점:{inverse_peak_return:.1f}% / 현재:{ret:.1f}%",
            force=True,
        )
        sell_etf(INVERSE_CODE, reason="인버스익절")
        inverse_peak_return = 0.0
        return
    # 손절 -4%
    if ret <= INVERSE_STOP_LOSS:
        cprint(f"[인버스 손절] {ret:.1f}%", Fore.RED)
        send_msg(f"🔴 인버스 손절 {ret:.1f}%", force=True)
        sell_etf(INVERSE_CODE, reason="인버스손절")
        inverse_peak_return = 0.0


# ── 단계 상향 체크 (루프마다 호출) ──────────────────────────
def check_defense_escalate():
    """장중 단계 상향 조건 체크"""
    kospi_pct = get_kospi_intraday_pct()
    pf_val    = _calc_portfolio_value()
    cash      = get_cash_balance()
    total_val = pf_val + cash
    pf_ret    = (total_val - initial_value) / initial_value * 100 if initial_value > 0 else 0.0

    if defense_stage == "NORMAL":
        if kospi_pct <= D1_KOSPI_PCT and pf_ret <= D1_PF_PCT:
            threading.Thread(
                target=lambda: _enter_defense_1(
                    f"코스피{kospi_pct:.1f}% / 포트{pf_ret:.1f}%"
                ), daemon=True
            ).start()

    elif defense_stage == "DEFENSE_1":
        if kospi_pct <= D2_KOSPI_PCT and consecutive_down_days >= D2_CONSEC:
            threading.Thread(target=_enter_defense_2, daemon=True).start()

    elif defense_stage == "DEFENSE_2":
        if kospi_pct <= D3_KOSPI_PCT and consecutive_down_days >= D3_CONSEC:
            threading.Thread(target=_enter_defense_3, daemon=True).start()


# ── 복귀 ────────────────────────────────────────────────────
def check_defense_resume():
    """자동 복귀 조건 체크 — DEFENSE_WAIT 상태에서만"""
    if defense_stage != "DEFENSE_WAIT":
        return
    kospi_pct = get_kospi_intraday_pct()
    if kospi_pct < RESUME_KOSPI_PCT:
        return
    vol_ratio = get_kospi_volume_ratio()
    if vol_ratio < RESUME_VOL_RATIO:
        return
    ma5   = get_kospi_ma5()
    price = get_kospi_info().get("price", 0.0)
    if ma5 > 0 and price < ma5:
        return
    send_msg("🟢 자동 복귀 조건 충족 — ETF 재매수 시작", force=True)
    _do_resume()


def _do_resume():
    global consecutive_down_days
    """인버스 매도 + ETF 재매수 + NORMAL 전환"""
    if INVERSE_CODE in portfolio:
        sell_etf(INVERSE_CODE, reason="복귀매도")
        time.sleep(2)
    _set_defense_stage("NORMAL", "복귀 완료")
    consecutive_down_days = 0
    threading.Thread(target=_do_rebalance, daemon=True).start()


# ── (하위 호환) KOFR 대피 명령을 DEFENSE_1로 연결 ───────────
def _evacuate_to_kofr(reason):
    """기존 코드 호환용 — DEFENSE_1 진입으로 처리"""
    _enter_defense_1(reason)


# ============================================================
# [14] 보유 포지션 모니터링 (트레일링 스탑 / 손절)
# ============================================================
def monitor_positions():
    if not portfolio or not is_tradeable_time(for_buy=False):
        return

    for code in list(portfolio.keys()):
        if code == INVERSE_CODE:
            continue   # 인버스는 check_inverse_exit() 에서 별도 관리
        pos  = portfolio.get(code)
        if not pos:
            continue
        info  = get_price_info(code)
        if not info or info["price"] <= 0:
            continue

        price   = info["price"]
        avg     = pos["avg_price"]
        high    = pos["high_price"]
        pnl_pct = (price - avg) / avg * 100 if avg > 0 else 0.0

        # 고점 갱신
        if price > high:
            portfolio[code]["high_price"] = price

        # 트레일링 스탑: +6% 후 고점 대비 -2%
        if pnl_pct >= TRAIL_START_PCT:
            trail_price = high * (1 - TRAIL_GAP_PCT / 100)
            if price <= trail_price:
                name = ETF_UNIVERSE.get(code, {}).get("name", code)
                cprint(f"[트레일링] {name} {pnl_pct:+.1f}% → 매도", Fore.CYAN)
                send_msg(f"💰 트레일링 스탑\n{name}\n수익: {pnl_pct:+.1f}%", force=True)
                sell_etf(code, reason="트레일링스탑")
                continue

        # 손절 -5%
        if pnl_pct <= STOP_LOSS_PCT:
            name = ETF_UNIVERSE.get(code, {}).get("name", code)
            cprint(f"[손절] {name} {pnl_pct:+.1f}%", Fore.RED)
            send_msg(f"🔴 손절\n{name}\n손실: {pnl_pct:+.1f}%", force=True)
            sell_etf(code, reason="손절")
            cooldown_list[code] = date.today() + timedelta(days=COOLDOWN_DAYS)


# ============================================================
# [15] 리밸런싱
# ============================================================
def _do_rebalance():
    """모멘텀 기반 ETF 교체 — NORMAL 상태에서만 실행"""
    global portfolio

    if defense_stage != "NORMAL":
        send_msg(f"⚠️ 리밸런싱 불가 — 현재 {defense_stage} 상태\n/resume 으로 복귀 후 재시도", force=True)
        return

    cprint("[리밸런싱] 모멘텀 스코어 계산 시작...", Fore.CYAN, bright=True)
    send_msg("🔄 리밸런싱 시작 — 스코어 계산 중 (약 30초)...", force=True)

    scores = get_all_scores()
    if not scores:
        send_msg("❌ 리밸런싱 실패 — 데이터 없음", force=True)
        return

    # 필터 적용
    filtered = {}
    for code, data in scores.items():
        closes = data["closes"]
        if is_cooldown(code):
            cprint(f"  [{code}] 쿨다운 — 제외", Fore.YELLOW)
            continue
        if register_cooldown_if_needed(code, closes):
            continue
        if is_overheat(closes):
            cprint(f"  [{code}] 과열 — 제외", Fore.YELLOW)
            continue
        info = get_price_info(code)
        if info and not check_liquidity(info):
            cprint(f"  [{code}] 유동성 부족 — 제외", Fore.YELLOW)
            continue
        if data["score"] <= MIN_SCORE_THRESHOLD:
            cprint(f"  [{code}] 스코어 {data['score']:.1f}% ≤ 0 — 제외", Fore.YELLOW)
            continue
        filtered[code] = data

    # 상위 TOP_N 선정
    ranked    = sorted(filtered.items(), key=lambda x: x[1]["score"], reverse=True)
    top_codes = [c for c, _ in ranked[:TOP_N_ETF]]

    # 교체 대상 매도
    current   = [c for c in portfolio if c != KOFR_CODE]
    to_sell   = [c for c in current if c not in top_codes]
    for code in to_sell:
        name = ETF_UNIVERSE.get(code, {}).get("name", code)
        cprint(f"  [교체 매도] {name}", Fore.YELLOW)
        sell_etf(code, reason="리밸런싱")
        time.sleep(1)

    time.sleep(2)

    # 신규 매수
    cash        = get_cash_balance()
    kofr_rsv    = int(TOTAL_BUDGET * KOFR_MIN_RATIO)
    investable  = max(0, cash - kofr_rsv)

    if not top_codes:
        send_msg("⚠️ 매수 후보 없음 → KOFR 대피", force=True)
        _evacuate_to_kofr("모멘텀 없음")
        return

    per_etf = investable // len(top_codes) if top_codes else 0
    bought  = []
    for code in top_codes:
        if code in portfolio:
            cprint(f"  [{code}] 이미 보유 — 건너뜀", Fore.CYAN)
            continue
        if per_etf < 5_000:
            cprint(f"  [{code}] 예산 부족 ({per_etf:,}원)", Fore.YELLOW)
            continue
        qty = buy_etf(code, per_etf)
        if qty > 0:
            bought.append(code)
        time.sleep(1)

    score_lines = []
    for code, data in ranked[:5]:
        name = ETF_UNIVERSE.get(code, {}).get("name", code)
        mark = "✅" if code in top_codes else "  "
        score_lines.append(f"{mark} {name}: {data['score']:+.1f}%")
    # 제외 종목 이유 표시
    excluded = {c: d for c, d in filtered.items() if c not in top_codes}
    all_universe = set(ETF_UNIVERSE.keys()) - {KOFR_CODE}
    for code in all_universe:
        name = ETF_UNIVERSE.get(code, {}).get("name", code)
        if code not in filtered:
            score_lines.append(f"  ✖ {name}: 스코어≤0 또는 유동성부족")

    send_msg(
        f"✅ 리밸런싱 완료\n"
        f"매도: {len(to_sell)}개  매수: {len(bought)}개\n\n"
        f"📊 스코어 TOP5:\n" + "\n".join(score_lines),
        force=True,
    )


def _do_weekly_rotation():
    """하위 50% 교체 — 스코어 차이 2% 미만이면 생략"""
    current = [c for c in portfolio if c != KOFR_CODE]
    if len(current) < 2:
        return
    scores = get_all_scores()
    if not scores:
        return

    held_scores = {c: scores[c]["score"] for c in current if c in scores}
    sorted_held = sorted(held_scores.items(), key=lambda x: x[1])
    bottom      = sorted_held[:max(1, len(sorted_held) // 2)]
    all_ranked  = sorted(scores.items(), key=lambda x: x[1]["score"], reverse=True)

    for b_code, b_score in bottom:
        for c_code, c_data in all_ranked:
            if c_code in current or is_cooldown(c_code):
                continue
            diff = c_data["score"] - b_score
            if diff < 2.0:
                cprint(f"[주간교체] 차이 {diff:.1f}% < 2% — 생략 ({b_code})", Fore.CYAN)
                break
            b_name = ETF_UNIVERSE.get(b_code, {}).get("name", b_code)
            c_name = ETF_UNIVERSE.get(c_code, {}).get("name", c_code)
            cprint(f"[주간교체] {b_name} → {c_name} (차이 {diff:.1f}%)", Fore.CYAN)
            sell_etf(b_code, reason="주간교체")
            time.sleep(2)
            cash = get_cash_balance()
            if cash >= 5_000:
                buy_etf(c_code, int(cash * 0.90))
            break


# ============================================================
# [16] 스케줄러
# ============================================================
_last_monthly_rebal = None
_last_weekly_rebal  = None


def check_rebalance_schedule():
    global _last_monthly_rebal, _last_weekly_rebal
    today = date.today()
    now   = datetime.now()

    # 9:15~9:30 사이에만 실행
    if not (now.hour == 9 and 15 <= now.minute <= 30):
        return

    # 월 1회 전체 교체: 이달 첫 번째 월요일
    if today.weekday() == 0 and today.day <= 7:
        if _last_monthly_rebal != today:
            _last_monthly_rebal = today
            cprint("[스케줄] 월 1회 전체 리밸런싱", Fore.CYAN, bright=True)
            threading.Thread(target=_do_rebalance, daemon=True).start()
            return

    # 주 1회 하위 50% 교체: 매주 월요일
    if today.weekday() == 0 and _last_weekly_rebal != today:
        _last_weekly_rebal = today
        cprint("[스케줄] 주 1회 하위 ETF 교체", Fore.CYAN)
        threading.Thread(target=_do_weekly_rotation, daemon=True).start()


# ============================================================
# [17] 일간 리셋 / 상태 메시지
# ============================================================
def _daily_reset():
    global daily_pnl_krw, trade_count, last_reset_day, daily_loss_base
    today = date.today()
    if last_reset_day == today:
        return
    if last_reset_day is not None:
        send_msg(
            f"📊 일간 리포트 [{last_reset_day}]\n"
            f"손익: {daily_pnl_krw:+,}원\n"
            f"거래: {trade_count}회\n"
            f"보유: {len(portfolio)}종목",
            force=True,
        )
    daily_pnl_krw   = 0
    trade_count     = 0
    last_reset_day  = today
    daily_loss_base = TOTAL_BUDGET
    cprint(f"[일간 리셋] {today}", Fore.CYAN)
    _save_state()


def _send_status():
    kospi = get_kospi_change_pct()
    val   = _calc_portfolio_value()
    cash  = get_cash_balance()
    dd    = (val + cash - peak_value) / peak_value * 100 if peak_value > 0 else 0.0
    lines = [
        f"📊 섹터봇 현황 [{datetime.now().strftime('%H:%M:%S')}]",
        f"━━━━━━━━━━━━━━━━━━",
        f"코스피:   {kospi:+.1f}%",
        f"포트폴리오: {val:,.0f}원",
        f"현금:     {cash:,.0f}원",
        f"오늘 손익: {daily_pnl_krw:+,}원",
        f"MDD:      {dd:.1f}%",
        f"킬스위치: {'🔴 ON' if kill_switch_active else '🟢 OFF'}",
        f"대피단계: {defense_stage}",
        f"━━━━━━━━━━━━━━━━━━",
    ]
    for code, pos in portfolio.items():
        info  = get_price_info(code)
        price = info.get("price", pos["avg_price"]) if info else pos["avg_price"]
        pnl_p = (price - pos["avg_price"]) / pos["avg_price"] * 100 if pos["avg_price"] > 0 else 0.0
        name  = ETF_UNIVERSE.get(code, {}).get("name", KOFR_NAME if code == KOFR_CODE else code)
        lines.append(f"  {name}: {pos['qty']}주 {pnl_p:+.1f}%")
    send_msg("\n".join(lines), force=True)


def _send_portfolio():
    if not portfolio:
        send_msg("보유 ETF 없음 (현금 대기 중)", force=True)
        return
    lines = ["📦 보유 ETF 현황"]
    for code, pos in portfolio.items():
        info  = get_price_info(code)
        price = info.get("price", pos["avg_price"]) if info else pos["avg_price"]
        pnl_p = (price - pos["avg_price"]) / pos["avg_price"] * 100 if pos["avg_price"] > 0 else 0.0
        pnl_w = int((price - pos["avg_price"]) * pos["qty"])
        name  = ETF_UNIVERSE.get(code, {}).get("name", KOFR_NAME if code == KOFR_CODE else code)
        lines.append(
            f"\n{name} ({code})\n"
            f"  {pos['qty']}주 @ 평균 {pos['avg_price']:,.0f}원\n"
            f"  현재 {price:,}원 ({pnl_p:+.1f}% / {pnl_w:+,}원)\n"
            f"  편입일: {pos['entry_date']}"
        )
    send_msg("\n".join(lines), force=True)


def _send_scores():
    send_msg("📊 스코어 계산 중... (약 30초)", force=True)
    def _calc():
        scores  = get_all_scores()
        ranked  = sorted(scores.items(), key=lambda x: x[1]["score"], reverse=True)
        lines   = ["📊 모멘텀 스코어 순위\n━━━━━━━━━━━━"]
        for i, (code, data) in enumerate(ranked, 1):
            name = ETF_UNIVERSE.get(code, {}).get("name", code)
            cd   = " 🚫쿨다운" if is_cooldown(code) else ""
            held = " 📦보유" if code in portfolio else ""
            lines.append(
                f"{i}. {name}{cd}{held}\n"
                f"   스코어: {data['score']:+.1f}%  5일: {data['ret5']:+.1f}%  20일: {data['ret20']:+.1f}%"
            )
        send_msg("\n".join(lines), force=True)
    threading.Thread(target=_calc, daemon=True).start()


# ============================================================
# [18] 메인 루프
# ============================================================
def main():
    global kospi_baseline, _kospi_baseline_set
    cprint("=" * 52, Fore.CYAN, bright=True)
    cprint(f"  {BOT_NAME} v{BOT_VERSION} 시작", Fore.CYAN, bright=True)
    cprint("=" * 52, Fore.CYAN, bright=True)

    load_config()
    get_kis_token()
    _load_state()
    _start_ipc_thread()   # 매니저 ↔ 섹터봇 IPC 수신 스레드 시작

    send_msg(
        f"🚀 섹터로테이션 봇 v{BOT_VERSION} 시작\n"
        f"예산: {TOTAL_BUDGET:,}원\n"
        f"모드: {'모의투자' if IS_MOCK else '실투자'}\n"
        f"ETF 유니버스: {len(ETF_UNIVERSE)}종\n"
        f"상위 편입: {TOP_N_ETF}개\n\n"
        f"/help 로 명령어 확인",
        force=True,
    )

    last_monitor      = 0.0
    last_tg_poll      = 0.0
    last_schedule     = 0.0
    last_defense_chk  = 0.0
    last_token_check  = time.time()
    monitor_interval  = 120   # 포지션 모니터링 2분마다
    defense_interval  = 300   # 대피 체크 5분마다

    while True:
        try:
            now_ts = time.time()
            now_dt = datetime.now()

            # 토큰 갱신 (10시간마다)
            if now_ts - last_token_check > 36_000:
                get_kis_token()
                last_token_check = now_ts

            # 텔레그램 폴링 (3초마다) — 매니저 하위에서 실행 중이면 건너뜀
            # (manager가 callback_query를 처리해야 하므로 sector_bot이 먼저 소비하면 안 됨)
            if now_ts - last_tg_poll >= 3:
                if not _manager_is_running():
                    poll_telegram()
                last_tg_poll = now_ts

            # 일간 리셋
            _daily_reset()

            # 장 시작 시 코스피 기준가 캡처 (9:10~9:15 사이 1회)
            if now_dt.hour == 9 and 10 <= now_dt.minute <= 15:
                if not _kospi_baseline_set:
                    info = get_kospi_info()
                    p    = info.get("price", 0.0)
                    if p > 0:
                        kospi_baseline      = p
                        _kospi_baseline_set = True
                        cprint(f"[코스피 기준가] {kospi_baseline:,.2f}", Fore.CYAN)
                        update_consecutive_down_days()
            elif now_dt.hour < 9:
                _kospi_baseline_set = False   # 익일 초기화

            # 킬스위치 / MDD 체크 (1분마다)
            if now_ts - last_monitor >= 60:
                if not kill_switch_active:
                    check_kill_switch()
                if not kill_switch_active and not mdd_active:
                    check_mdd()

            # 장중 루틴
            if is_tradeable_time(for_buy=False):
                # 포지션 모니터링 (2분마다)
                if now_ts - last_monitor >= monitor_interval:
                    if not kill_switch_active and not mdd_active and defense_stage == "NORMAL":
                        monitor_positions()
                    if defense_stage != "NORMAL":
                        check_inverse_exit()
                    last_monitor = now_ts

                # 대피 단계 체크 (5분마다)
                if now_ts - last_defense_chk >= defense_interval:
                    if not kill_switch_active:
                        check_defense_escalate()
                        _step_down_defense()
                        check_defense_resume()
                    last_defense_chk = now_ts

                # 리밸런싱 스케줄 체크 (5분마다)
                if now_ts - last_schedule >= 300:
                    if not kill_switch_active and not mdd_active:
                        check_rebalance_schedule()
                    last_schedule = now_ts

            time.sleep(2)

        except KeyboardInterrupt:
            cprint("\n[종료] Ctrl+C", Fore.YELLOW)
            send_msg("⏹ 섹터봇 수동 종료", force=True)
            _save_state()
            break
        except Exception as e:
            cprint(f"[메인 루프 오류] {e}", Fore.RED)
            traceback.print_exc()
            time.sleep(10)


if __name__ == "__main__":
    main()
