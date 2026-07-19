# 변경 이력

## 2026-07-19

- 변경 파일: `AGENTS.md`, `CHANGELOG.md`
- 수정 내용: 모든 모델 작업에서 날짜·파일·구체적 변경 내용·영향·검증 결과와 남은 위험을 `CHANGELOG.md`에 기록하도록 규칙을 추가했다.
- 영향: 이후 주식/전략 봇 업데이트의 변경 내역과 주문 영향 범위를 커밋과 함께 추적할 수 있다.
- 검증: 가이드 문서 변경만 수행했으며, 다음 코드 변경 시 주문 없는 검증과 Python 구문 검증 규칙을 적용한다.

## 2026-07-20

- 변경 파일: `sector_bot.py`, `manager.py`
- 수정 내용: Telegram 전송 시도·성공·실패와 HTTP 응답을 `logs/sector/telegram.log`, `logs/manager_telegram.log`에 기록한다.
- 영향: 주식 봇 알림 누락 시 전송 경로와 실패 원인을 확인할 수 있다.
- 검증: Python 구문 검증 완료.

## 2026-07-20 (individual-stock trend alerts)

- 변경 파일: `watchlist_observer.py`, `README.md`
- 수정 내용: 관찰 대상을 12개 개별 종목으로 확대하고, 1분 CSV 기록은 유지하면서 6시간 요약 알림을 제거했다. 종목별 추세 상태 전환 시 개별 Telegram 알림을 보내며, 자정에는 전날 일일 요약을 1회 보낸다.
- 영향: Telegram 도배를 줄이면서 추세 전환을 종목 단위로 확인할 수 있다.
- 검증: Python 구문 검증 예정.

## 2026-07-20 (follow-up)

- 변경 파일: `sector_bot.py`
- 수정 내용: 메인 루프 예외를 `logs/sector/telegram.log`에 기록한다.
- 영향: 주식 봇이 반복 실행 중 멈추거나 알림 경로를 건너뛴 원인을 확인할 수 있다.
- 검증: Python 구문 검증 완료.
