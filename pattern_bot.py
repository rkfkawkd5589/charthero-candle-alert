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


def _from_coinbase():
    """Coinbase 4시간봉. [time, low, high, open, close, volume], 최신 먼저. time=초(시작)."""
    pair = "BTC-USD" if SYMBOL.upper().startswith("BTC") else SYMBOL
    url = f"https://api.exchange.coinbase.com/products/{pair}/candles?granularity=14400"
    rows = json.loads(http_get(url))
    return [{
        "start": int(x[0]) * 1000,
        "open":  float(x[3]),
        "high":  float(x[2]),
        "low":   float(x[1]),
        "close": float(x[4]),
    } for x in rows]


def _from_kraken():
    """Kraken 4시간봉. [time, o, h, l, c, ...], 오래된→최신. time=초(시작)."""
    pair = "XBTUSD" if SYMBOL.upper().startswith("BTC") else SYMBOL
    url = f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval=240"
    data = json.loads(http_get(url))["result"]
    key = next(k for k in data if k != "last")
    return [{
        "start": int(x[0]) * 1000,
        "open":  float(x[1]),
        "high":  float(x[2]),
        "low":   float(x[3]),
        "close": float(x[4]),
    } for x in data[key]]


def get_4h_candles():
    """마감된 4시간봉만 오래된→최신 순으로 반환 (여러 소스 폴백)."""
    last_err = None
    for src in (_from_coinbase, _from_kraken):
        try:
            candles = src()
            candles.sort(key=lambda c: c["start"])
            now_ms = int(time.time() * 1000)
            four_h = 4 * 3600 * 1000
            closed = [c for c in candles if c["start"] + four_h <= now_ms]
            if len(closed) >= 3:
                return closed
        except Exception as e:
            last_err = e
    if last_err:
        raise last_err
    return []


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
    header = ("[캔들]\n"
              "🟩 : 양봉 / 🟥 : 음봉   🐕 : 도지 캔들   🐌 : CME 갭 발생   🧲 : FVG 캔들")

    body = []
    body.append("🟩 양봉" if r["bull"] else ("🟥 음봉" if r["bear"] else "➖ 보합"))
    if r["doji"]:
        body.append("🐕 도지 캔들")
    if r["fvg"] == "bull":
        body.append("🧲 상승 FVG 발생")
    elif r["fvg"] == "bear":
        body.append("🧲 하락 FVG 발생")
    if r["cme"] is not None:
        body.append(f"🐌 CME 갭 발생 ({r['cme']:+.2f}%)")

    return header + "\n\n" + "\n".join(body)


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
