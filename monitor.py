#!/usr/bin/env python3
# メルカリ新着監視(クラウド版) → ntfyプッシュ。GitHub Actionsで10分おき実行。
import asyncio, json, os, datetime
import urllib.request
from mercapi import Mercapi
from mercapi.requests import SearchRequestData

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG = json.load(open(os.path.join(BASE, 'config.json')))
CONFIG['ntfy_topic'] = os.environ.get('NTFY_TOPIC') or CONFIG.get('ntfy_topic')
STATE_PATH = os.path.join(BASE, 'state.json')

def notify(title, body, link):
    req = urllib.request.Request(
        f"https://ntfy.sh/{CONFIG['ntfy_topic']}",
        data=body.encode('utf-8'), method='POST')
    req.add_header('Title', title)
    req.add_header('Click', link)
    req.add_header('Priority', 'high')
    req.add_header('Tags', 'moneybag')
    try:
        urllib.request.urlopen(req, timeout=15)
    except Exception as e:
        print('ntfy error:', e)

async def run():
    state = {}
    if os.path.exists(STATE_PATH):
        try: state = json.load(open(STATE_PATH))
        except Exception: state = {}
    first_run = not state
    m = Mercapi()
    hits = 0
    DIAG = {}
    for w in CONFIG['watches']:
        seen = set(state.get(w['name'], []))
        try:
            r = await m.search(
                w['query'],
                sort_by=SearchRequestData.SortBy.SORT_CREATED_TIME,
                sort_order=SearchRequestData.SortOrder.ORDER_DESC,
                price_min=w['price_min'], price_max=w['price_max'],
                status=[SearchRequestData.Status.STATUS_ON_SALE],
                exclude=w.get('exclude'))
        except Exception as e:
            print(w['name'], 'search error:', e)
            DIAG[w['name']] = 'ERR:' + str(e)[:80]
            continue
        items = r.items[:40]
        DIAG[w['name']] = f"{len(items)}items" 
        new_ids = []
        for it in items:
            iid = it.id_
            new_ids.append(iid)
            if iid in seen or first_run:
                continue
            price = it.price or 0
            if price < w['price_min'] or price > w['price_max']:
                continue
            link = f"https://jp.mercari.com/item/{iid}"
            notify(f"{w['name']} ¥{price:,}", (it.name or '')[:80], link)
            print('HIT', w['name'], iid, price)
            hits += 1
        state[w['name']] = list(dict.fromkeys(new_ids + list(seen)))[:200]
        await asyncio.sleep(3)
    json.dump(state, open(STATE_PATH, 'w'))
    print(datetime.datetime.utcnow().isoformat(), 'done hits=', hits, '(baseline)' if first_run else '')
    # 診断ログを -log トピックへ（購読不要・デバッグ用）
    try:
        req = urllib.request.Request(
            f"https://ntfy.sh/{CONFIG['ntfy_topic']}-log",
            data=json.dumps(DIAG).encode(), method='POST')
        req.add_header('Priority', 'min')
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass

if __name__ == '__main__':
    asyncio.run(run())
