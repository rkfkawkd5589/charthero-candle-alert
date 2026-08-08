#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
4시간봉 캔들 패턴 → 텔레그램 자동 발송 봇
- 🟩 양봉 / 🟥 음봉 / 🐕 도지 / 🐌 CME 갭 / 🧲 FVG
- 거래소(Bybit) + Yahoo(CME 선물) 무료 공개 API 사용, 트레이딩뷰 불필요
- GitHub Actions 크론으로 4시간마다 실행
"""
import os
import time
import json
import urllib.request
from datetime import datetime, timezone, timedelta

# ---------------- 설정 (환경변수로 덮어쓰기 가능) ----------------
BOT_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID     = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
SYMBOL      = os.environ.get("SYMBOL", "BTCUSDT").strip()
DOJI_PCT    = float(os.environ.get("DOJI_PCT", "5"))        # 몸통 <= 전체범위 * 5% 이면 도지
CME_GAP_PCT = float(os.environ.get("CME_GAP_PCT", "0.25"))  # CME 갭 최소 크기(%)
KST         = timezone(timedelta(hours=9))


def http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode()


def get_4h_candles():
    """Bybit 스팟 4시간봉. 마감된 캔들만 오래된→최신 순으로 반환."""
    url = (f"https://api.bybit.com/v5/market/kline"
           f"?category=spot&symbol={SYMBOL}&interval=240&limit=6")
    data = json.loads(http_get(url))
    rows = data["result"]["list"]  # [startMs, o, h, l, c, vol, turnover], 최신 먼저
    candles = [{
        "start": int(x[0]),
        "open":  float(x[1]),
        "high":  float(x[2]),
        "low":   float(x[3]),
        "close": float(x[4]),
    } for x in rows]
    candles.sort(key=lambda c: c["start"])
    now_ms = int(time.time() * 1000)
    four_h = 4 * 3600 * 1000
    return [c for c in candles if c["start"] + four_h <= now_ms]  # 마감된 것만


def get_cme_gap_pct():
    """CME 비트코인 선물(BTC=F) 일봉의 직전 종가 대비 최근 시가 갭(%). 실패 시 None."""
    try:
        url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
               "BTC=F?interval=1d&range=1mo")
        data = json.loads(http_get(url))
        q = data["chart"]["result"][0]["indicators"]["quote"][0]
        sess = [(o, c) for o, c in zip(q["open"], q["close"])
                if o is not None and c is not None]
        if len(sess) < 2:
            return None
        prev_close = sess[-2][1]
        cur_open = sess[-1][0]
        if not prev_close:
            return None
        return (cur_open - prev_close) / prev_close * 100.0
    except Exception:
        return None


def classify(closed):
    """가장 최근 마감된 4시간봉을 판정."""
    c = closed[-1]
    o, h, l, cl = c["open"], c["high"], c["low"], c["close"]
    rng = h - l
    body = abs(cl - o)

    is_bull = cl > o
    is_bear = cl < o
    is_doji = rng > 0 and body <= rng * (DOJI_PCT / 100.0)

    # 🧲 FVG (3캔들 불균형): 현재 저가 > 2봉전 고가(상승) / 현재 고가 < 2봉전 저가(하락)
    fvg = None
    if len(closed) >= 3:
        a, cur = closed[-3], closed[-1]
        if cur["low"] > a["high"]:
            fvg = "bull"
        elif cur["high"] < a["low"]:
            fvg = "bear"

    # 🐌 CME 갭
    gap = get_cme_gap_pct()
    cme = gap if (gap is not None and abs(gap) >= CME_GAP_PCT) else None

    end_kst = datetime.fromtimestamp(c["start"] / 1000 + 4 * 3600, KST)
    return {
        "o": o, "c": cl, "rng": rng,
        "bull": is_bull, "bear": is_bear, "doji": is_doji,
        "fvg": fvg, "cme": cme, "end_kst": end_kst,
    }


def build_message(r):
    dir_line = "🟩 양봉" if r["bull"] else ("🟥 음봉" if r["bear"] else "➖ 보합")
    doji_line = "🐕 도지: ✅" if r["doji"] else "🐕 도지: ❌"

    if r["fvg"] == "bull":
        fvg_line = "🧲 FVG: 상승 FVG 발생"
    elif r["fvg"] == "bear":
        fvg_line = "🧲 FVG: 하락 FVG 발생"
    else:
        fvg_line = "🧲 FVG: 없음"

    if r["cme"] is not None:
        cme_line = f"🐌 CME 갭: {r['cme']:+.2f}% 발생"
    else:
        cme_line = "🐌 CME 갭: 없음"

    return (
        f"📊 [{SYMBOL}] 4시간봉 마감\n\n"
        f"{dir_line}\n"
        f"{doji_line}\n"
        f"{fvg_line}\n"
        f"{cme_line}"
    )


def send_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = json.dumps({"chat_id": CHAT_ID, "text": text}).encode()
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode()


def main():
    if not BOT_TOKEN or not CHAT_ID:
        raise SystemExit("환경변수 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 가 필요합니다.")
    closed = get_4h_candles()
    if not closed:
        raise SystemExit("마감된 4시간봉 데이터를 가져오지 못했습니다.")
    result = classify(closed)
    msg = build_message(result)
    print(msg)
    print("---")
    print(send_telegram(msg))


if __name__ == "__main__":
    main()
