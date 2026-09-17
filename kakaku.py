#!/usr/bin/env python3
# 価格.com 買取価格スナップショット
#   - 全モデル×全容量×全ランク×全業者を毎回記録 → price_history.jsonl に追記
#   - 更新日が STALE_DAYS より古い業者は自動で除外（アメモバが2024/11で止まっていた事故の再発防止）
#   - 前回比で MOVE_MIN 以上動いたらntfyに通知
#   - latest.json に「保守基準=細かなキズあり」の各モデル最高値を書き出す（巡回はこれを読む）
import urllib.request, gzip, re, json, os, time, datetime, sys

BASE = os.path.dirname(os.path.abspath(__file__))
HIST = os.path.join(BASE, 'price_history.jsonl')
LATEST = os.path.join(BASE, 'latest.json')
STALE_DAYS = 120          # これより古い更新日の業者は使わない
MOVE_MIN   = 2000         # この額以上動いたら通知
CONSERVATIVE_RANK = '細かなキズあり'   # 上限計算に使うランク（査定が一段下がる前提）

MODELS = {
    'iPhone 17 Pro Max': 'M0000001163', 'iPhone 17 Pro': 'M0000001162',
    'iPhone 17':         'M0000001160',
    'iPhone 16 Pro Max': 'M0000001095', 'iPhone 16 Pro': 'M0000001094',
    'iPhone 16':         'M0000001092',
    'iPhone 15 Pro Max': 'M0000001027', 'iPhone 15 Pro': 'M0000001026',
    'iPhone 15':         'M0000001024',
    'iPhone 14 Pro Max': 'M0000000967', 'iPhone 14 Pro': 'M0000000966',
    'iPhone 14':         'M0000000964',
    'iPhone 13':         'M0000000902',
}
UA = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/125 Safari/537.36',
      'Accept-Language': 'ja'}

def fetch(url):
    r = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30)
    raw = r.read()
    if r.headers.get('Content-Encoding') == 'gzip':
        raw = gzip.decompress(raw)
    return raw.decode('cp932', 'replace')

def strip(s):
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', s)).strip()

TBL = re.compile(r'<h4 class="p-kaitori_title">(.*?)</h4>.*?<table class="p-kaitori_table"(.*?)</table>', re.S)
TR  = re.compile(r'<tr>(.*?)</tr>', re.S)
YEN = re.compile(r'([\d,]+)\s*円')
DATE= re.compile(r'(20\d\d)/(\d\d)/(\d\d)')

def parse(model, html):
    """→ [{model,capacity,rank,vendor,price,updated}]"""
    out = []
    for cap_raw, body in TBL.findall(html):
        capacity = strip(cap_raw).replace(model, '').strip()
        rows = TR.findall(body)
        if not rows:
            continue
        vendors = [re.sub(r'\s*ショップの\s*買取ページへ\s*', '', strip(c)).strip()
                   for c in re.findall(r'<th class="-head[^"]*"[^>]*>(.*?)</th>', rows[0], re.S)]
        updated = {}
        ranks = []
        for row in rows[1:]:
            label_m = re.search(r'<td class="-fixedRight[^"]*"[^>]*>(.*?)</td>', row, re.S)
            cells = re.findall(r'<td class="s-tableLink"[^>]*>(.*?)</td>', row, re.S)
            if not cells:
                continue
            label = strip(label_m.group(1)) if label_m else ''
            if '更新' in row or (cells and DATE.search(strip(cells[0]))):
                for v, c in zip(vendors, cells):
                    d = DATE.search(strip(c))
                    if d:
                        updated[v] = f'{d.group(1)}-{d.group(2)}-{d.group(3)}'
                continue
            ranks.append((label, cells))
        for label, cells in ranks:
            for v, c in zip(vendors, cells):
                m = YEN.search(strip(c))
                if not m:
                    continue
                out.append({'model': model, 'capacity': capacity, 'rank': label,
                            'vendor': v, 'price': int(m.group(1).replace(',', '')),
                            'updated': updated.get(v)})
    return out

def is_fresh(rec, today):
    if not rec.get('updated'):
        return False
    d = datetime.date.fromisoformat(rec['updated'])
    return (today - d).days <= STALE_DAYS

def notify(title, body):
    topic = os.environ.get('NTFY_TOPIC')
    if not topic:
        print('[no NTFY_TOPIC]', title); return
    req = urllib.request.Request(f'https://ntfy.sh/{topic}', data=body.encode('utf-8'), method='POST')
    req.add_header('Title', title); req.add_header('Priority', 'default'); req.add_header('Tags', 'chart_with_downwards_trend')
    try: urllib.request.urlopen(req, timeout=15)
    except Exception as e: print('ntfy error:', e)

def main():
    today = datetime.date.today()
    stamp = today.isoformat()
    # 前回スナップショット（比較用）
    prev = {}
    if os.path.exists(HIST):
        with open(HIST, encoding='utf-8') as f:
            for line in f:
                try: r = json.loads(line)
                except Exception: continue
                if r.get('date') == stamp:   # 同日再実行は比較対象にしない
                    continue
                prev[(r['model'], r['capacity'], r['rank'], r['vendor'])] = (r['date'], r['price'])

    all_recs, moves, stale = [], [], set()
    for name, mid in MODELS.items():
        try:
            html = fetch(f'https://kakaku.com/keitai/smartphone/model/{mid}/kaitori/')
        except Exception as e:
            print(f'  ! {name}: {type(e).__name__} {e}'); continue
        recs = parse(name, html)
        if not recs:
            print(f'  ! {name}: 表を取得できず（ページ構造が変わった可能性）'); continue
        for r in recs:
            r['date'] = stamp
            if not is_fresh(r, today):
                stale.add(f"{r['vendor']}({r.get('updated') or '不明'})")
                continue
            all_recs.append(r)
            k = (r['model'], r['capacity'], r['rank'], r['vendor'])
            if k in prev:
                pd, pp = prev[k]
                if abs(r['price'] - pp) >= MOVE_MIN:
                    moves.append((r, pp, pd))
        print(f'  {name}: {len(recs)}件 (有効 {len([x for x in recs if is_fresh(x, today)])})')
        time.sleep(1.5)

    if not all_recs:
        print('取得ゼロ。中止'); return 1

    with open(HIST, 'a', encoding='utf-8') as f:
        for r in all_recs:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')

    # 保守基準（細かなキズあり）の最高値 → 巡回が読む
    latest = {}
    for r in all_recs:
        if r['rank'] != CONSERVATIVE_RANK:
            continue
        k = f"{r['model']} {r['capacity']}"
        if k not in latest or r['price'] > latest[k]['price']:
            latest[k] = {'price': r['price'], 'vendor': r['vendor'], 'updated': r['updated']}
    json.dump({'date': stamp, 'rank': CONSERVATIVE_RANK, 'stale_excluded': sorted(stale),
               'buyback': latest}, open(LATEST, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

    print(f'\n記録 {len(all_recs)}件 / 除外(古い) {sorted(stale)}')
    if moves:
        moves.sort(key=lambda x: x[0]['price'] - x[1])
        lines = [f"{r['model']} {r['capacity']} {r['rank']} {r['vendor']}: "
                 f"{pp:,} → {r['price']:,} ({r['price']-pp:+,}) 前回{pd}"
                 for r, pp, pd in moves[:20]]
        print('\n'.join(lines))
        notify(f'買取相場が動きました（{len(moves)}件）', '\n'.join(lines[:12]))
    else:
        print('前回比の変動なし')
    return 0

if __name__ == '__main__':
    sys.exit(main())
