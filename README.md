# sedori-monitor
メルカリ新着監視 → ntfy通知（10分おき・GitHub Actions）。条件は config.json。

# 価格.com買取スナップショット（kakaku.py）が生成
price_history.jsonl   相場の時系列。1行=1日1モデル1容量1ランク1業者
latest.json           保守基準(細かなキズあり)の各モデル最高値。巡回はこれを読む
