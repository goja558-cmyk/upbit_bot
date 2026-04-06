#!/usr/bin/env python3
"""
merge_logs.py — 거래 로그 + 상태 로그 합치기
Claude에게 분석 요청 시 사용

사용법:
  python3 merge_logs.py            # 오늘 로그
  python3 merge_logs.py 7          # 최근 7일
  python3 merge_logs.py 2025-04-06 # 특정 날짜
  python3 merge_logs.py all        # 전체

출력: /tmp/merged_log_YYYYMMDD.txt
"""

import os, sys, csv, json
from datetime import date, datetime, timedelta
from collections import defaultdict

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
# 서버 실행 시 경로
BOT_DIR       = "/home/trade/upbit_bot"
LOG_DAILY     = os.path.join(BOT_DIR, "logs", "daily")
LOG_STATE     = os.path.join(BOT_DIR, "logs", "state")
LOG_INDICATOR = os.path.join(BOT_DIR, "logs", "indicator")

# ── 날짜 범위 결정 ──────────────────────────────────────────
def get_date_range(arg):
    today = date.today()
    if arg is None or arg == "today":
        return [today.strftime("%Y-%m-%d")]
    if arg == "all":
        dates = []
        for f in sorted(os.listdir(LOG_DAILY)):
            if f.endswith(".csv"):
                dates.append(f.replace(".csv", ""))
        return dates
    try:
        n = int(arg)
        return [(today - timedelta(days=i)).strftime("%Y-%m-%d")
                for i in range(n-1, -1, -1)]
    except ValueError:
        return [arg]  # 특정 날짜 직접 입력

# ── CSV 읽기 ────────────────────────────────────────────────
def read_csv(path):
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rows.append(row)
    return rows

# ── 요약 생성 ────────────────────────────────────────────────
def summarize(trade_rows, state_rows):
    lines = []

    # 거래 요약
    buys  = [r for r in trade_rows if r.get("side") == "BUY"]
    sells = [r for r in trade_rows if r.get("side") == "SELL"]
    total_pnl = sum(float(r.get("pnl_krw") or 0) for r in sells)
    wins  = [r for r in sells if float(r.get("pnl_krw") or 0) >= 0]
    lines.append("=" * 50)
    lines.append(f"📊 거래 요약")
    lines.append(f"  매수: {len(buys)}건  매도: {len(sells)}건")
    lines.append(f"  승률: {len(wins)}/{len(sells)} = {len(wins)/len(sells)*100:.1f}%" if sells else "  승률: -")
    lines.append(f"  총 손익: {total_pnl:+,.0f}원")
    lines.append("")

    # 종목별 성과
    by_market = defaultdict(list)
    for r in sells:
        by_market[r.get("market","")].append(float(r.get("pnl_krw") or 0))
    if by_market:
        lines.append("📈 종목별 손익")
        for m, pnls in sorted(by_market.items()):
            s = sum(pnls)
            lines.append(f"  {m.replace('KRW-',''):10s}: {s:+,.0f}원 ({len(pnls)}건)")
        lines.append("")

    # 장세별 성과
    by_regime = defaultdict(list)
    for r in sells:
        by_regime[r.get("regime","")].append(float(r.get("pnl_krw") or 0))
    if by_regime:
        lines.append("🌐 장세별 손익")
        for reg, pnls in by_regime.items():
            lines.append(f"  {reg:8s}: {sum(pnls):+,.0f}원 ({len(pnls)}건)")
        lines.append("")

    # 전체 거래 로그
    lines.append("=" * 50)
    lines.append("📋 전체 거래 로그")
    for r in trade_rows:
        side  = r.get("side","")
        emoji = "🛒" if side == "BUY" else ("🟢" if float(r.get("pnl_krw") or 0) >= 0 else "🔴")
        pnl   = f"  손익: {float(r.get('pnl_krw') or 0):+,.0f}원 ({float(r.get('pnl_pct') or 0):+.2f}%)" if side == "SELL" else ""
        lines.append(
            f"{r.get('datetime','')} {emoji} {r.get('market','').replace('KRW-','')} {side}"
            f"  가격: {float(r.get('price') or 0):,.2f}원"
            f"  RSI: {r.get('rsi','')}"
            f"  눌림: {r.get('drop_pct','')}"
            f"  변동성: {r.get('volatility_regime','')}"
            f"  장세: {r.get('regime','')}"
            f"  슬리피지: {r.get('slippage_pct','')}%"
            f"  지연: {r.get('entry_delay_sec','')}s"
            f"  MFE: {r.get('highest_profit','')}%"
            f"  MAE: {r.get('max_drawdown_pct','')}%"
            f"{pnl}"
            f"  이유: {r.get('reason','')}"
        )

    # 상태 로그 (이벤트 태그 있는 것만)
    event_states = [r for r in state_rows if r.get("event_tag","") not in ("모니터링","")]
    if event_states:
        lines.append("")
        lines.append("=" * 50)
        lines.append("🔍 이벤트 상태 로그 (진입/봉체크)")
        for r in event_states:
            lines.append(
                f"{r.get('datetime','')} [{r.get('event_tag','')}]"
                f"  {r.get('market','').replace('KRW-','')}"
                f"  가격: {float(r.get('price') or 0):,.2f}원"
                f"  손익: {r.get('pnl_pct','')}%"
                f"  RSI: {r.get('rsi','')}  Δ{r.get('rsi_delta','')}"
                f"  MFE: {r.get('highest_profit','')}%"
                f"  MAE: {r.get('max_drawdown_pct','')}%"
            )

    return "\n".join(lines)

# ── 메인 ────────────────────────────────────────────────────
def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    dates = get_date_range(arg)

    all_trade = []
    all_state = []
    all_indic = []
    for d in dates:
        all_trade += read_csv(os.path.join(LOG_DAILY,     f"{d}.csv"))
        all_state += read_csv(os.path.join(LOG_STATE,     f"{d}.csv"))
        all_indic += read_csv(os.path.join(LOG_INDICATOR, f"{d}.csv"))

    if not all_trade and not all_state:
        print("❌ 로그 없음")
        return

    tag    = dates[0] if len(dates) == 1 else f"{dates[0]}~{dates[-1]}"
    out    = f"/tmp/merged_log_{tag.replace('-','').replace('~','_')}.txt"
    result = summarize(all_trade, all_state)

    # 인디케이터 요약 추가
    if all_indic:
        near_rows   = [r for r in all_indic if r.get("log_type") == "near"]
        entry_rows  = [r for r in all_indic if r.get("entry_possible") == "1"]
        fwd_rows    = [r for r in all_indic if r.get("fwd_return_5m") not in ("", None)]
        indic_lines = ["\n" + "=" * 50, "📡 인디케이터 요약"]
        indic_lines.append(f"  전체 기록: {len(all_indic)}행  근접: {len(near_rows)}행  진입가능: {len(entry_rows)}행")

        # 시간대별 near_trigger 평균
        by_hour = defaultdict(list)
        for r in near_rows:
            try: by_hour[int(r.get("hour","0"))].append(float(r.get("near_trigger",1)))
            except: pass
        if by_hour:
            indic_lines.append("\n  ⏰ 시간대별 평균 근접도 (낮을수록 진입 직전)")
            for h in sorted(by_hour):
                avg = sum(by_hour[h]) / len(by_hour[h])
                indic_lines.append(f"    {h:02d}시: {avg:.3f} ({len(by_hour[h])}건)")

        # fwd_return 유효성
        if fwd_rows:
            fwd5_vals = []
            for r in fwd_rows:
                try: fwd5_vals.append(float(r["fwd_return_5m"]))
                except: pass
            if fwd5_vals:
                pos = sum(1 for v in fwd5_vals if v > 0)
                indic_lines.append(f"\n  📈 5분 후 상승 비율: {pos}/{len(fwd5_vals)} = {pos/len(fwd5_vals)*100:.1f}%")
                indic_lines.append(f"     평균 5분 수익률: {sum(fwd5_vals)/len(fwd5_vals):+.4f}%")

        result += "\n".join(indic_lines)

    with open(out, "w", encoding="utf-8") as f:
        f.write(f"# 멀티코인봇 로그 분석 ({tag})\n")
        f.write(f"# 생성: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write(result)

    print(f"✅ 저장 완료: {out}")
    print(f"   거래 로그: {len(all_trade)}행  상태 로그: {len(all_state)}행  인디케이터: {len(all_indic)}행")
    print(f"\n📎 Claude에게 보낼 때:")
    print(f"   cat {out}  →  내용 복사해서 붙여넣기")

if __name__ == "__main__":
    main()
