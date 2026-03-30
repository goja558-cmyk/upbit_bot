import os
import pandas as pd

BASE_PATH = "/home/trade/upbit_bot/logs"

all_data = []

# 1. 데이터 로드
for coin in os.listdir(BASE_PATH):
    file_path = os.path.join(BASE_PATH, coin, "indicator_log.csv")
    
    if os.path.exists(file_path):
        df = pd.read_csv(file_path)
        df["coin"] = coin
        all_data.append(df)

df = pd.concat(all_data, ignore_index=True)

# 2. 시간 처리
df["datetime"] = pd.to_datetime(df["datetime"])
df = df.sort_values("datetime")

# 3. 최근 데이터만 사용 (중요 ⭐)
df = df.groupby("coin").tail(200)

# 4. 이벤트만 추출 (핵심 ⭐)
ai_input = df[
    (df["rsi"] < 30) |
    (df["rsi"] > 70) |
    (df["vol_ratio"] > 1.5) |
    (df["drop_pct"] < -2)
]

# 5. 컬럼 정리
ai_input = ai_input[[
    "datetime", "coin", "price", "rsi",
    "ma20", "ma60", "vwap",
    "vol_pct", "vol_ratio", "drop_pct"
]]

# 6. 저장
ai_input.to_csv("ai_input.csv", index=False)

print("완료: ai_input.csv 생성")
