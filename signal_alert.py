#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
4시간봉 보조지표 신호 → 텔레그램 (하나라도 뜨면 발송, 없으면 미발송)
  🍟 거래량 급증 (SMA600/STDEV600, 2.5σ↑)
  🌈 200 EMA 추세 전환 (offset 3)
  🍉 RSI 다이버전스 (RSI14, pivot L/R 5, range 5~60)
  🍒 MFI 다이버전스 (MFI13 + Stoch14/3 필터)
데이터: Kraken 4시간봉(최대 720개) → Coinbase(300개) 폴백
"""
import os
import time
import json
import math
import urllib.request

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
SYMBOL    = os.environ.get("SYMBOL", "BTCUSDT").strip()


# ----------------------------- 데이터 -----------------------------
def http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read().decode()


def _kraken():
    pair = "XBTUSD" if SYMBOL.upper().startswith("BTC") else SYMBOL
    d = json.loads(http_get(
        f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval=240"))["result"]
    key = next(k for k in d if k != "last")
    return [{"start": int(x[0]) * 1000, "open": float(x[1]), "high": float(x[2]),
             "low": float(x[3]), "close": float(x[4]), "vol": float(x[6])} for x in d[key]]


def _coinbase():
    pair = "BTC-USD" if SYMBOL.upper().startswith("BTC") else SYMBOL
    rows = json.loads(http_get(
        f"https://api.exchange.coinbase.com/products/{pair}/candles?granularity=14400"))
    return [{"start": int(x[0]) * 1000, "open": float(x[3]), "high": float(x[2]),
             "low": float(x[1]), "close": float(x[4]), "vol": float(x[5])} for x in rows]


def get_candles():
    last_err = None
    for src in (_kraken, _coinbase):
        try:
            c = src()
            c.sort(key=lambda z: z["start"])
            now = int(time.time() * 1000)
            closed = [z for z in c if z["start"] + 4 * 3600 * 1000 <= now]
            if len(closed) >= 60:
                return closed
        except Exception as e:
            last_err = e
    if last_err:
        raise last_err
    return []


# ----------------------------- 지표 헬퍼 -----------------------------
def ema(v, n):
    out = [None] * len(v)
    k = 2.0 / (n + 1)
    out[0] = v[0]
    for i in range(1, len(v)):
        out[i] = v[i] * k + out[i - 1] * (1 - k)
    return out


def rma(v, n):
    out = [None] * len(v)
    if len(v) < n:
        return out
    seed = sum(v[:n]) / n
    out[n - 1] = seed
    for i in range(n, len(v)):
        out[i] = (v[i] + (n - 1) * out[i - 1]) / n
    return out


def rsi(closes, n):
    up = [0.0] * len(closes)
    dn = [0.0] * len(closes)
    for i in range(1, len(closes)):
        ch = closes[i] - closes[i - 1]
        up[i] = ch if ch > 0 else 0.0
        dn[i] = -ch if ch < 0 else 0.0
    ru, rd = rma(up, n), rma(dn, n)
    out = [None] * len(closes)
    for i in range(len(closes)):
        if ru[i] is None or rd[i] is None:
            continue
        out[i] = 100.0 if rd[i] == 0 else 100.0 - 100.0 / (1.0 + ru[i] / rd[i])
    return out


def stoch_k(closes, highs, lows, n, smooth):
    raw = [None] * len(closes)
    for i in range(len(closes)):
        if i < n - 1:
            continue
        hh = max(highs[i - n + 1:i + 1])
        ll = min(lows[i - n + 1:i + 1])
        raw[i] = 50.0 if hh == ll else 100.0 * (closes[i] - ll) / (hh - ll)
    out = [None] * len(closes)
    for i in range(len(closes)):
        w = [raw[j] for j in range(max(0, i - smooth + 1), i + 1) if raw[j] is not None]
        if len(w) == smooth:
            out[i] = sum(w) / smooth
    return out


# ----------------------------- 신호 판정 -----------------------------
def sig_volume(c):
    """🍟 거래량 급증: (vol-sma600)/stdev600 > 2.5, 최근 마감봉 기준."""
    if len(c) < 601:
        return None
    vols = [z["vol"] for z in c]
    win = vols[-601:-1]  # 마지막(최근 마감봉) 직전 600개
    m = sum(win) / 600.0
    sd = math.sqrt(sum((x - m) ** 2 for x in win) / 600.0)
    if sd == 0:
        return None
    z = (vols[-1] - m) / sd
    return "🍟 : 거래량 급증 (%.1fσ)" % z if z > 2.5 else None


def sig_ema200(c):
    """🌈 200 EMA 추세 전환: ema>ema[3] 부호가 직전봉 대비 바뀌면."""
    if len(c) < 210:
        return None
    e = ema([z["close"] for z in c], 200)
    off = 3
    up_now  = e[-1] > e[-1 - off]
    up_prev = e[-2] > e[-2 - off]
    if up_now and not up_prev:
        return "🌈 : 200 EMA 상승 전환"
    if (not up_now) and up_prev:
        return "🌈 : 200 EMA 하락 전환"
    return None


def _is_pivot_low(o, i, left, right):
    if i - left < 0 or i + right >= len(o):
        return False
    p = o[i]
    for k in range(i - left, i + right + 1):
        if k != i and o[k] <= p:
            return False
    return True


def _is_pivot_high(o, i, left, right):
    if i - left < 0 or i + right >= len(o):
        return False
    p = o[i]
    for k in range(i - left, i + right + 1):
        if k != i and o[k] >= p:
            return False
    return True


def sig_rsi_div(c, length=14, lbL=5, lbR=5, rlo=5, rhi=60):
    """🍉 RSI 정규 다이버전스: 최근 마감봉에서 피봇 확정 시."""
    if len(c) < length + lbL + lbR + rhi + 5:
        return None
    closes = [z["close"] for z in c]
    lows   = [z["low"] for z in c]
    highs  = [z["high"] for z in c]
    osc = rsi(closes, length)
    if any(osc[i] is None for i in range(len(osc) - (rhi + lbL + lbR + 2), len(osc))):
        return None
    t = len(c) - 1
    piv = t - lbR  # 이번 봉에서 확정되는 피봇 위치

    out = []
    # Regular Bullish: 가격 LL + RSI HL
    if _is_pivot_low(osc, piv, lbL, lbR):
        prev = next((j for j in range(piv - 1, -1, -1) if _is_pivot_low(osc, j, lbL, lbR)), None)
        if prev is not None and rlo <= (piv - prev) <= rhi:
            if osc[piv] > osc[prev] and lows[piv] < lows[prev]:
                out.append("🍉 : RSI 강세(Bull) 다이버전스")
    # Regular Bearish: 가격 HH + RSI LH
    if _is_pivot_high(osc, piv, lbL, lbR):
        prev = next((j for j in range(piv - 1, -1, -1) if _is_pivot_high(osc, j, lbL, lbR)), None)
        if prev is not None and rlo <= (piv - prev) <= rhi:
            if osc[piv] < osc[prev] and highs[piv] > highs[prev]:
                out.append("🍉 : RSI 약세(Bear) 다이버전스")
    return out or None


def sig_mfi_div(c, mfiLen=13, ob=75, os_=25, periodK=14, smoothK=3, xbars=10):
    """🍒 MFI 다이버전스 (v3 로직 이식) + Stoch 과매수/과매도 필터."""
    n = len(c)
    if n < mfiLen + xbars + periodK + 5:
        return None
    closes = [z["close"] for z in c]
    highs  = [z["high"] for z in c]
    lows   = [z["low"] for z in c]
    src = [(c[i]["high"] + c[i]["low"] + c[i]["close"]) / 3.0 for i in range(n)]

    mfi = [None] * n
    for i in range(n):
        if i < mfiLen:
            continue
        up = dn = 0.0
        for j in range(i - mfiLen + 1, i + 1):
            ch = src[j] - src[j - 1]
            up += c[j]["vol"] * (0 if ch <= 0 else src[j])
            dn += c[j]["vol"] * (0 if ch >= 0 else src[j])
        mfi[i] = 100.0 if dn == 0 else 100.0 - 100.0 / (1.0 + up / dn)

    k = stoch_k(closes, highs, lows, periodK, smoothK)

    # 상태 변수 루프 (Pine 원본과 동일)
    mMax = mMaxV = mMin = mMinV = None
    hist = []  # (mMax, mMaxV, mMin, mMinV)
    for i in range(n):
        if mfi[i] is None:
            hist.append((mMax, mMaxV, mMin, mMinV))
            continue
        # highestbars/lowestbars 오프셋
        lo = max(0, i - xbars + 1)
        seg = [(mfi[j], j) for j in range(lo, i + 1) if mfi[j] is not None]
        hbar = i - max(seg, key=lambda p: p[0])[1] if seg else None
        lbar = i - min(seg, key=lambda p: p[0])[1] if seg else None

        mMax  = closes[i] if hbar == 0 else (closes[i] if mMax is None else mMax)
        mMaxV = mfi[i]    if hbar == 0 else (mfi[i]    if mMaxV is None else mMaxV)
        mMin  = closes[i] if lbar == 0 else (closes[i] if mMin is None else mMin)
        mMinV = mfi[i]    if lbar == 0 else (mfi[i]    if mMinV is None else mMinV)
        if closes[i] > mMax:
            mMax = closes[i]
        if mfi[i] > mMaxV:
            mMaxV = mfi[i]
        if closes[i] < mMin:
            mMin = closes[i]
        if mfi[i] < mMinV:
            mMinV = mfi[i]
        hist.append((mMax, mMaxV, mMin, mMinV))

    t = n - 1
    if any(hist[t - x][j] is None for x in (0, 1, 2) for j in range(4)):
        return None
    if mfi[t] is None or mfi[t - 1] is None or k[t] is None or k[t - 1] is None:
        return None

    mMax_t, mMaxV_t = hist[t][0], hist[t][1]
    mMin_t, mMinV_t = hist[t][2], hist[t][3]
    mMax_1 = hist[t - 1][0]
    mMax_2 = hist[t - 2][0]
    mMin_1 = hist[t - 1][2]
    mMin_2 = hist[t - 2][2]

    isOB = (k[t - 1] > ob) or (k[t] > ob)
    isOS = (k[t - 1] < os_) or (k[t] < os_)
    divbear = (mMax_1 > mMax_2) and (mfi[t - 1] < mMaxV_t) and (mfi[t] <= mfi[t - 1])
    divbull = (mMin_1 < mMin_2) and (mfi[t - 1] > mMinV_t) and (mfi[t] >= mfi[t - 1])

    out = []
    if divbear and isOB:
        out.append("🍒 : MFI 약세(Bear) 다이버전스")
    if divbull and isOS:
        out.append("🍒 : MFI 강세(Bull) 다이버전스")
    return out or None


# ----------------------------- 발송 -----------------------------
def send(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    body = json.dumps({"chat_id": CHAT_ID, "text": text}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode()


def main():
    if not BOT_TOKEN or not CHAT_ID:
        raise SystemExit("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 필요")
    c = get_candles()
    if not c:
        raise SystemExit("캔들 데이터를 가져오지 못했습니다.")

    fired = []
    for fn in (sig_volume, sig_ema200, sig_rsi_div, sig_mfi_div):
        try:
            r = fn(c)
        except Exception as e:
            print(f"[warn] {fn.__name__}: {e}")
            r = None
        if r:
            fired.extend(r if isinstance(r, list) else [r])

    if not fired:
        print("신호 없음 — 발송 생략")
        return

    footer = ("• 🦸🏻‍♂️ 차트히어로 소통방에는 4시간봉 맛보기 알람만 제공됩니다.\n"
              "• 그 외 타임프레임 알람은 차트히어로 전용 알람방에서 확인하실 수 있습니다.")
    msg = "[보조 지표]\n\n" + "\n".join(fired) + "\n\n" + footer
    print(msg)
    print("---")
    print(send(msg))


if __name__ == "__main__":
    main()
