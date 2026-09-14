#!/usr/bin/env python3
# メルカリ監視(クラウド版) → ntfyプッシュ。GitHub Actionsで10分おき実行。
# 検知する3種:
#   [新着] 帯に新しく入った出品
#   [値下] 追跡中の出品が値下げされた瞬間  ← 横取りされた側から、する側へ
#   [専用] 他人の交渉で「○○様専用」になった玉（メルカリ公式は専用を認めない=先に買った者勝ち）
import asyncio, json, os, datetime, re
import urllib.request
from mercapi import Mercapi
from mercapi.requests import SearchRequestData

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG = json.load(open(os.path.join(BASE, 'config.json')))
CONFIG['ntfy_topic'] = os.environ.get('NTFY_TOPIC') or CONFIG.get('ntfy_topic')
STATE_PATH = os.path.join(BASE, 'state.json')
DROP_MIN = CONFIG.get('drop_min', 1000)   # これ以上下がったら通知
KEEP = 300                                 # watchごとに保持するID数

def notify(title, body, link, tags='moneybag', priority='high'):
    req = urllib.request.Request(
        f"https://ntfy.sh/{CONFIG['ntfy_topic']}",
        data=body.encode('utf-8'), method='POST')
    req.add_header('Title', title)
    req.add_header('Click', link)
    req.add_header('Priority', priority)
    req.add_header('Tags', tags)
    try:
        urllib.request.urlopen(req, timeout=15)
    except Exception as e:
        print('ntfy error:', e)

def load_state():
    """旧形式 {name:[id,...]} を新形式 {name:{id:price}} へ移行。旧IDはprice=null=値下げ判定の基準なし。"""
    if not os.path.exists(STATE_PATH):
        return {}
    try:
        raw = json.load(open(STATE_PATH))
    except Exception:
        return {}
    out = {}
    for k, v in raw.items():
        if isinstance(v, list):
            out[k] = {i: None for i in v}
        elif isinstance(v, dict):
            out[k] = v
    return out

async def search(m, w, sort_by, order):
    return await m.search(
        w['query'], sort_by=sort_by, sort_order=order,
        price_min=w['price_min'], price_max=w['price_max'],
        status=[SearchRequestData.Status.STATUS_ON_SALE],
        exclude=w.get('exclude'))

async def run():
    state = load_state()
    first_run = not state
    m = Mercapi()
    hits = 0
    DIAG = {}
    for w in CONFIG['watches']:
        known = dict(state.get(w['name'], {}))
        watch_first = w['name'] not in state
        items, errs = {}, []
        # 2パス: 新着順=新規入荷を拾う / 安い順=値下がりした玉は必ず上位に来る
        for label, sb, so in (
            ('new',  SearchRequestData.SortBy.SORT_CREATED_TIME, SearchRequestData.SortOrder.ORDER_DESC),
            ('cheap', SearchRequestData.SortBy.SORT_PRICE,        SearchRequestData.SortOrder.ORDER_ASC)):
            try:
                r = await search(m, w, sb, so)
                for it in r.items[:40]:
                    items[it.id_] = it
            except Exception as e:
                errs.append(f'{label}:{str(e)[:40]}')
            await asyncio.sleep(2)
        if errs and not items:
            print(w['name'], 'search error:', errs)
            DIAG[w['name']] = 'ERR:' + ';'.join(errs)
            continue
        DIAG[w['name']] = f"{len(items)}items" + (('/' + ';'.join(errs)) if errs else '')

        seen_now = {}
        for iid, it in items.items():
            # 鮮度フィルタ: 出品から48時間超は通知しない（古い売れ残りを排除）
            try:
                age_h = (datetime.datetime.now() - it.created).total_seconds() / 3600
                if age_h > CONFIG.get('max_age_hours', 48):
                    continue
            except Exception:
                pass
            name = it.name or ''
            price = it.price or 0
            seen_now[iid] = price
            if iid in known:
                old = known[iid]
                # --- 追跡中の玉の値下げ ---
                # title_require はかけない: 「○○様専用」に改名されると必ず落ちるため。
                # この玉は前回この帯で捕捉済み＝機種は確認済みとみなす。
                if first_run or old is None or price >= old - DROP_MIN:
                    continue
                if w.get('title_block') and re.search(w['title_block'], name, re.I):
                    continue
                cut = old - price
                senyou = '専用' in name
                link = f"https://jp.mercari.com/item/{iid}"
                tag = 'rotating_light' if senyou else 'chart_with_downwards_trend'
                head = '専用化' if senyou else '値下'
                notify(
                    f"[{head}] {w['name']} ¥{old:,}→¥{price:,} (-{cut:,})",
                    (('⚠他人の交渉玉。専用は予約ではない=先着順。即購入可\n' if senyou else '') + name)[:120],
                    link, tags=tag, priority='urgent' if senyou else 'high')
                print('DROP', w['name'], iid, old, '->', price, 'senyou' if senyou else '')
                hits += 1
            else:
                # --- 新規に帯へ入った玉 ---
                if first_run or watch_first:
                    continue
                senyou = '専用' in name
                if w.get('title_require') and not senyou and not re.search(w['title_require'], name, re.I):
                    continue
                if w.get('title_block') and re.search(w['title_block'], name, re.I):
                    continue
                if price < w['price_min'] or price > w['price_max']:
                    continue
                link = f"https://jp.mercari.com/item/{iid}"
                if senyou:
                    notify(f"[専用] {w['name']} ¥{price:,}",
                           ('⚠他人の交渉玉。専用は予約ではない=先着順。即購入可\n' + name)[:120],
                           link, tags='rotating_light', priority='urgent')
                else:
                    notify(f"[新着] {w['name']} ¥{price:,}", name[:80], link)
                print('HIT', w['name'], iid, price, 'senyou' if senyou else '')
                hits += 1

        merged = dict(seen_now)
        for iid, p in known.items():
            if iid not in merged:
                merged[iid] = p
        state[w['name']] = dict(list(merged.items())[:KEEP])

    json.dump(state, open(STATE_PATH, 'w'))
    print(datetime.datetime.utcnow().isoformat(), 'done hits=', hits, '(baseline)' if first_run else '')
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
