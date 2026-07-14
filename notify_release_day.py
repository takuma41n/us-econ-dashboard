#!/usr/bin/env python3
"""公開予定日の当日朝（日本時間）に ntfy.sh へ予告通知を送る（GitHub Actions用）。

data.json 内の release.next_date が日本時間の今日と一致する指標を1通にまとめる。
米指標の多くは 8:30 ET（日本時間 21:30/22:30 ごろ）発表なので、当日朝の予告になる。

ワークフローは6時間ごとに走るため、日本時間 5〜8時台の実行のみ送信して
1日1回に抑える（20:45 UTC = 翌朝 5:45 JST の回が該当）。

使い方:    NTFY_TOPIC=xxx python3 notify_release_day.py --data _site/data.json
テスト用:  NOTIFY_FORCE=1 で時間帯ガードを無視
失敗してもデプロイを壊さないよう、常に exit 0 で終わる。
"""
import argparse
import datetime
import json
import os
import sys

from notify_update import SERIES_INFO, fmt_value, latest, send

JST = datetime.timezone(datetime.timedelta(hours=9))


def build_message(bundle, today):
    lines = []
    for sid, (name, fmt, _freq) in SERIES_INFO.items():
        s = (bundle.get("series") or {}).get(sid)
        if not s or (s.get("release") or {}).get("next_date") != today:
            continue
        last = latest(s)
        prev = f"（前回 {fmt_value(fmt, last['value'])}）" if last else ""
        lines.append(f"{name}{prev}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="_site/data.json")
    args = ap.parse_args()

    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        print("NTFY_TOPIC 未設定 — 通知をスキップ")
        return

    now = datetime.datetime.now(JST)
    if not (5 <= now.hour <= 8) and os.environ.get("NOTIFY_FORCE") != "1":
        print(f"JST {now:%H:%M} は通知時間帯（5〜8時台）外 — スキップ")
        return

    with open(args.data, encoding="utf-8") as f:
        bundle = json.load(f)

    message = build_message(bundle, now.date().isoformat())
    if not message:
        print("本日公開予定の指標なし")
        return

    try:
        send(topic, message, title="本日公開の米経済指標", tags=["calendar"])
        print("通知を送信:\n" + message)
    except Exception as e:  # 通知失敗でもデプロイは成功扱い
        print(f"ntfy送信失敗（デプロイには影響なし）: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
