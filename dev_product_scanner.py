"""dev-product-scanner: posts to Discord when a Roblox game adds, removes or changes a developer product, game pass
or badge. Standard library only, no database: what it has seen lives in one JSON file.

    python dev_product_scanner.py              watch forever (config.json, state.json next to this file)
    python dev_product_scanner.py --once       check every game once and exit (for cron or GitHub Actions)
    python dev_product_scanner.py --dry-run    print what it would post instead of posting

The config can also come from the DEV_PRODUCT_SCANNER_CONFIG environment variable (the whole JSON), e.g. a GitHub secret.
"""
import argparse
import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UA = 'dev-product-scanner (+https://github.com/birchir/dev-product-scanner)'
KINDS = ('products', 'passes', 'badges')
COLORS = {'new': 0x3BA55D, 'changed': 0xFAA61A, 'removed': 0xED4245}
LABEL = {'products': 'Developer Product', 'passes': 'Game Pass', 'badges': 'Badge'}


# ── Roblox ────────────────────────────────────────────────────────────────────

def get_json(url, tries=5):
    """GET with backoff on rate limits and server errors; raises after the last try."""
    for attempt in range(tries):
        req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504) or attempt == tries - 1:
                raise
            wait = float(e.headers.get('Retry-After') or 0) or 2 ** attempt + random.random()
        except (urllib.error.URLError, TimeoutError):
            if attempt == tries - 1:
                raise
            wait = 2 ** attempt + random.random()
        time.sleep(wait)


def pages(url, items_key, cursor_key, cursor_param, get=get_json):
    """Every item across Roblox's cursor pages."""
    out, cursor = [], ''
    while True:
        data = get(url + (f'&{cursor_param}={urllib.parse.quote(cursor)}' if cursor else ''))
        out += data.get(items_key) or []
        cursor = data.get(cursor_key)
        if not cursor:
            return out


def universe_of(game, get=get_json):
    """A config entry's universe ID: given directly, or looked up from a place ID."""
    if game.get('universe'):
        return int(game['universe'])
    return int(get(f'https://apis.roblox.com/universes/v1/places/{int(game["place"])}/universe')['universeId'])


def fetch(kind, universe, get=get_json):
    """{id: item} for one kind, each item reduced to the fields worth watching."""
    if kind == 'products':
        raw = pages(f'https://apis.roblox.com/developer-products/v2/universes/{universe}/developerproducts?limit=100',
                    'developerProducts', 'nextPageCursor', 'cursor', get)
        return {str(p['ProductId']): {'name': p.get('Name') or '', 'description': p.get('Description') or '',
                                      'price': p.get('PriceInRobux'), 'icon': p.get('IconImageAssetId') or None,
                                      'for_sale': bool(p.get('IsForSale'))} for p in raw if p.get('ProductId')}
    if kind == 'passes':
        raw = pages(f'https://apis.roblox.com/game-passes/v1/universes/{universe}/game-passes?passView=Full&pageSize=100',
                    'gamePasses', 'nextPageToken', 'pageToken', get)
        return {str(p['id']): {'name': p.get('displayName') or p.get('name') or '', 'description': p.get('displayDescription') or '',
                               'price': p.get('price'), 'icon': p.get('displayIconImageAssetId') or None,
                               'for_sale': bool(p.get('isForSale'))} for p in raw if p.get('id')}
    raw = pages(f'https://badges.roblox.com/v1/universes/{universe}/badges?limit=100&sortOrder=Asc',
                'data', 'nextPageCursor', 'cursor', get)
    return {str(b['id']): {'name': b.get('displayName') or b.get('name') or '', 'description': b.get('displayDescription') or '',
                           'icon': b.get('displayIconImageId') or None, 'enabled': bool(b.get('enabled'))} for b in raw if b.get('id')}


def game_name(universe, get=get_json):
    try:
        return get(f'https://games.roblox.com/v1/games?universeIds={universe}')['data'][0]['name']
    except Exception:
        return f'Universe {universe}'


def icon_urls(asset_ids, get=get_json):
    """{asset id: image URL} for item icons (one request per 100)."""
    ids, out = sorted({str(i) for i in asset_ids if i}), {}
    for i in range(0, len(ids), 100):
        try:
            data = get('https://thumbnails.roblox.com/v1/assets?size=420x420&format=Png&assetIds=' + ','.join(ids[i:i + 100]))
            out.update({str(t['targetId']): t['imageUrl'] for t in data.get('data', []) if t.get('state') == 'Completed'})
        except Exception:
            pass   # an icon is nice to have, never worth missing an alert over
    return out


def link(kind, universe, item_id):
    if kind == 'products':
        return f'https://www.roblox.com/developer-product/{universe}/product/{item_id}'
    return f'https://www.roblox.com/{"game-pass" if kind == "passes" else "badges"}/{item_id}'


# ── What changed ──────────────────────────────────────────────────────────────

def diff(old, new):
    """[(event, id, before, after, changed fields)] between two {id: item} snapshots."""
    events = []
    for i, item in new.items():
        if i not in old:
            events.append(('new', i, None, item, []))
        elif item != old[i]:
            events.append(('changed', i, old[i], item, [k for k in item if item.get(k) != old[i].get(k)]))
    events += [('removed', i, old[i], None, []) for i in old if i not in new]
    return events


def show(field, value):
    if field == 'price':
        return 'Free' if value in (None, 0) else f'{value:,} R$'
    if isinstance(value, bool):
        return 'Yes' if value else 'No'
    return str(value) if value not in (None, '') else '(none)'


def embed(event, kind, universe, game, item_id, before, after, fields, icons):
    item = after or before
    e = {'title': f'{event.title()} {LABEL[kind]}: {item["name"] or item_id}'[:256], 'url': link(kind, universe, item_id),
         'color': COLORS[event], 'footer': {'text': game[:2048]}, 'fields': []}
    if event == 'new':
        if 'price' in item:
            e['fields'].append({'name': 'Price', 'value': show('price', item['price']), 'inline': True})
        if item.get('description'):
            e['description'] = item['description'][:1000]
    for f in fields:
        if f != 'icon':
            e['fields'].append({'name': f.replace('_', ' ').title(), 'value': f'{show(f, before.get(f))} → {show(f, after.get(f))}'[:1024]})
    if 'icon' in fields:
        e['fields'].append({'name': 'Icon', 'value': 'Changed (new icon shown)'})
    e['fields'].append({'name': 'ID', 'value': f'`{item_id}`', 'inline': True})
    if icons.get(str(item.get('icon'))):
        e['thumbnail'] = {'url': icons[str(item['icon'])]}
    return e


# ── Discord ───────────────────────────────────────────────────────────────────

def post(webhook, embeds, dry=False):
    """Embeds to a Discord webhook, 10 per message, waiting out Discord's rate limit."""
    for i in range(0, len(embeds), 10):
        body = json.dumps({'embeds': embeds[i:i + 10]}).encode()
        if dry:
            print(json.dumps(embeds[i:i + 10], indent=2, ensure_ascii=False))
            continue
        for attempt in range(5):
            req = urllib.request.Request(webhook, body, {'Content-Type': 'application/json', 'User-Agent': UA})
            try:
                urllib.request.urlopen(req, timeout=20).close()
                break
            except urllib.error.HTTPError as e:
                if e.code != 429 or attempt == 4:
                    raise
                time.sleep(float(json.load(e).get('retry_after', 1)) + 0.25)


# ── The loop ──────────────────────────────────────────────────────────────────

def load(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def save(path, data):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)   # a crash mid-write never leaves a broken state file


def check(game, state, cfg, dry=False, get=get_json, send=post):
    """One game, every watched kind. The first look at a game only records it, so you don't get hundreds of alerts."""
    universe = universe_of(game, get)
    seen = state.setdefault(str(universe), {})
    name = game.get('name') or seen.get('_name') or game_name(universe, get)
    seen['_name'] = name
    out, now = [], {}
    for kind in game.get('watch') or cfg.get('watch') or KINDS:
        now[kind] = fetch(kind, universe, get)
        if kind in seen:
            out += [(kind, *ev) for ev in diff(seen[kind], now[kind])]
    if out:
        icons = icon_urls([(a or b).get('icon') for _, _, _, b, a, _ in out], get)
        embeds = [embed(ev, kind, universe, name, i, b, a, f, icons) for kind, ev, i, b, a, f in out]
        send(game.get('webhook') or cfg.get('webhook'), embeds, dry)
    seen.update(now)   # only after the alerts went out: if Discord is down, the next check sends them again
    return name, len(out)


def main(argv=None):
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description='Discord alerts for new and changed Roblox developer products, game passes and badges.')
    ap.add_argument('--config', default=os.path.join(here, 'config.json'))
    ap.add_argument('--state', default=os.path.join(here, 'state.json'))
    ap.add_argument('--once', action='store_true', help='check every game once, then exit')
    ap.add_argument('--dry-run', action='store_true', help='print alerts instead of posting them')
    args = ap.parse_args(argv)
    cfg = json.loads(os.environ['DEV_PRODUCT_SCANNER_CONFIG']) if os.environ.get('DEV_PRODUCT_SCANNER_CONFIG') else load(args.config, None)
    if not cfg or not cfg.get('games'):
        sys.exit(f'No games to watch. Copy config.example.json to {args.config} and fill it in.')
    if not args.dry_run and not cfg.get('webhook') and not all(g.get('webhook') for g in cfg['games']):
        sys.exit('Set "webhook" in the config (or one per game).')
    state = load(args.state, {})
    while True:
        for game in cfg['games']:
            try:
                name, n = check(game, state, cfg, args.dry_run)
                if n:
                    print(f'{time.strftime("%H:%M:%S")} {name}: {n} change(s)', flush=True)
            except Exception as e:   # one broken game or a Roblox hiccup never stops the others
                print(f'{time.strftime("%H:%M:%S")} {game}: {e}', file=sys.stderr, flush=True)
            save(args.state, state)
        if args.once:
            return
        time.sleep(max(15, int(cfg.get('interval', 60))))


if __name__ == '__main__':
    main()
