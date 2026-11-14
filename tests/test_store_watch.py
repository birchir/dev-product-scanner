"""Offline tests: a fake Roblox answers by URL, so nothing touches the network."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import store_watch as sw


def fake_roblox(products, passes=(), badges=()):
    """A get() that serves products in pages of 2 (to exercise paging) and everything else in one page."""
    def get(url):
        if '/universes/v1/places/' in url:
            return {'universeId': 42}
        if 'games.roblox.com/v1/games' in url:
            return {'data': [{'name': 'Test Game'}]}
        if 'developerproducts' in url:
            start = int(url.split('cursor=')[1]) if 'cursor=' in url else 0
            page = list(products)[start:start + 2]
            return {'developerProducts': page, 'nextPageCursor': str(start + 2) if start + 2 < len(products) else None}
        if 'game-passes' in url:
            return {'gamePasses': list(passes), 'nextPageToken': None}
        if 'badges.roblox.com' in url:
            return {'data': list(badges), 'nextPageCursor': None}
        if 'thumbnails' in url:
            return {'data': [{'targetId': 7, 'state': 'Completed', 'imageUrl': 'https://img/7.png'}]}
        raise AssertionError('unexpected URL ' + url)
    return get


def product(pid, name='Pack', price=99, icon=7, for_sale=True):
    return {'ProductId': pid, 'Name': name, 'Description': '', 'PriceInRobux': price, 'IconImageAssetId': icon, 'IsForSale': for_sale}


class Run:
    """Runs check() against a fake Roblox and keeps what it would have posted."""
    def __init__(self):
        self.state, self.sent = {}, []

    def __call__(self, get, game=None):
        self.sent.clear()
        return sw.check(game or {'place': 1}, self.state, {'webhook': 'w', 'watch': ['products']}, get=get,
                        send=lambda hook, embeds, dry: self.sent.extend(embeds))


class Tests(unittest.TestCase):
    def test_first_look_only_records(self):
        run = Run()
        self.assertEqual(run(fake_roblox([product(1), product(2), product(3)])), ('Test Game', 0))
        self.assertEqual(len(run.state['42']['products']), 3)   # all three pages read

    def test_new_changed_removed(self):
        run = Run()
        run(fake_roblox([product(1), product(2, price=50)]))
        name, n = run(fake_roblox([product(2, price=75), product(3, name='Mega Pack')]))
        self.assertEqual(n, 3)
        titles = sorted(e['title'] for e in run.sent)
        self.assertEqual(titles, ['Changed Developer Product: Pack', 'New Developer Product: Mega Pack', 'Removed Developer Product: Pack'])
        changed = next(e for e in run.sent if e['title'].startswith('Changed'))
        self.assertIn({'name': 'Price', 'value': '50 R$ → 75 R$'}, changed['fields'])
        self.assertEqual(changed['url'], 'https://www.roblox.com/developer-product/42/product/2')

    def test_no_change_no_post(self):
        run = Run()
        run(fake_roblox([product(1)]))
        self.assertEqual(run(fake_roblox([product(1)])), ('Test Game', 0))
        self.assertEqual(run.sent, [])

    def test_icon_and_sale_changes(self):
        run = Run()
        run(fake_roblox([product(1, icon=5, for_sale=False)]))
        run(fake_roblox([product(1, icon=7, for_sale=True)]))
        (e,) = run.sent
        self.assertIn({'name': 'For Sale', 'value': 'No → Yes'}, e['fields'])
        self.assertEqual(e['thumbnail'], {'url': 'https://img/7.png'})

    def test_passes_and_badges(self):
        p = {'id': 9, 'displayName': 'VIP', 'price': 400, 'isForSale': True}
        b = {'id': 5, 'displayName': 'Welcome', 'enabled': True}
        state = {}
        cfg = {'webhook': 'w'}
        sent = []
        send = lambda hook, embeds, dry: sent.extend(embeds)
        sw.check({'universe': 42}, state, cfg, get=fake_roblox([], [], []), send=send)
        sw.check({'universe': 42}, state, cfg, get=fake_roblox([], [p], [b]), send=send)
        self.assertEqual(sorted(e['title'] for e in sent), ['New Badge: Welcome', 'New Game Pass: VIP'])
        self.assertIn('https://www.roblox.com/game-pass/9', [e['url'] for e in sent])

    def test_failed_post_is_retried_next_check(self):
        run = Run()
        run(fake_roblox([product(1)]))
        def broken(hook, embeds, dry):
            raise OSError('discord is down')
        with self.assertRaises(OSError):
            sw.check({'place': 1}, run.state, {'webhook': 'w', 'watch': ['products']}, get=fake_roblox([product(1), product(2)]), send=broken)
        self.assertEqual(run(fake_roblox([product(1), product(2)]))[1], 1)   # the new product is still announced

    def test_webhook_waits_out_rate_limit(self):
        import http.server, threading
        hits = []
        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                hits.append(1)
                if len(hits) == 1:
                    body = b'{"retry_after": 0.1}'
                    self.send_response(429); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
                else:
                    self.send_response(204); self.end_headers()
            def log_message(self, *a):
                pass
        srv = http.server.HTTPServer(('127.0.0.1', 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        sw.post(f'http://127.0.0.1:{srv.server_port}/hook', [{'title': str(i)} for i in range(12)])   # 12 embeds = 2 messages
        srv.shutdown()
        self.assertEqual(len(hits), 3)   # first message rate-limited once, then both delivered

    def test_price_display(self):
        self.assertEqual([sw.show('price', v) for v in (None, 0, 1500)], ['Free', 'Free', '1,500 R$'])


if __name__ == '__main__':
    unittest.main()
