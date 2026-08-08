#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
차트히어로_비트시황 채널용 '하루 1회(오후 8시 KST)' 시황 정리 봇
  [캔들]  최근 마감 4H 캔들 상태 (🟩/🟥 + 🐕/🐌/🧲)
  [보조 지표]  🍟 거래량 / 🌈 200EMA 추세 / 🍉 RSI div / 🍒 MFI div
  → 최근 24시간(4H×6봉) 기준으로 정리
"""
import os
import json
import urllib.request

import signal_alert as s
import pattern_bot as p

BOT_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
DIGEST_CHAT = os.environ.get("DIGEST_CHAT_ID", "").strip()


def recent_fire(fn, candles, bars=6):
    """최근 bars개 4H봉 중 하나라도 신호가 있으면 그 결과 반환."""
    for k in range(bars):
        sub = candles[:len(candles) - k]
        try:
            r = fn(sub)
        except Exception:
            r = None
        if r:
            return r
    return None


def build_digest(c):
    # ---------------- [캔들] : 최근 마감 4H 캔들 ----------------
    last = c[-1]
    o, h, l, cl = last["open"], last["high"], last["low"], last["close"]
    rng, body = h - l, abs(cl - o)

    candle = ["🟩 : 양봉" if cl > o else ("🟥 : 음봉" if cl < o else "➖ : 보합")]
    if rng > 0 and body <= rng * 0.05:
        candle.append("🐕 : 도지 캔들")
    gap = p.get_cme_gap_pct()
    if gap is not None and abs(gap) >= 0.25:
        candle.append(f"🐌 : CME 갭 발생 ({gap:+.2f}%)")
    if len(c) >= 3:
        if c[-1]["low"] > c[-3]["high"]:
            candle.append("🧲 : 상승 FVG 캔들")
        elif c[-1]["high"] < c[-3]["low"]:
            candle.append("🧲 : 하락 FVG 캔들")

    # ---------------- [보조 지표] ----------------
    ind = []
    # 🍟 거래량
    v = recent_fire(s.sig_volume, c)
    ind.append(v if v else "🍟 : 거래량 보통")
    # 🌈 200 EMA 추세
    closes = [x["close"] for x in c]
    e = s.ema(closes, 200)
    if e[-1] is not None and e[-4] is not None:
        ind.append("🌈 : 200 EMA 상승추세" if e[-1] > e[-4] else "🌈 : 200 EMA 하락추세")
    else:
        ind.append("🌈 : 200 EMA -")
    # 🍉 RSI 다이버전스
    r = recent_fire(s.sig_rsi_div, c)
    ind.append(r[0] if r else "🍉 : RSI 다이버전스 없음")
    # 🍒 MFI 다이버전스
    m = recent_fire(s.sig_mfi_div, c)
    ind.append(m[0] if m else "🍒 : MFI 다이버전스 없음")

    return ("[캔들]\n" + "\n".join(candle)
            + "\n\n────────────────\n"
            + "[보조 지표]\n" + "\n".join(ind))


def send(text):
    body = json.dumps({"chat_id": DIGEST_CHAT, "text": text}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                                 data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode()


def main():
    if not BOT_TOKEN or not DIGEST_CHAT:
        raise SystemExit("TELEGRAM_BOT_TOKEN / DIGEST_CHAT_ID 필요")
    c = s.get_candles()
    if not c:
        raise SystemExit("캔들 데이터를 가져오지 못했습니다.")
    msg = build_digest(c)
    print(msg)
    print("---")
    print(send(msg))


if __name__ == "__main__":
    main()
