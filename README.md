# Dev Product Scanner

Get a Discord message the moment a Roblox game adds, removes or changes a **developer product**, **game pass** or **badge**: new packs before they're announced, price changes, renames, new icons, items going on or off sale.

- **No setup beyond a webhook.** No bot to create, no database, no `npm install`. One Python file using only the standard library (Python 3.9+).
- **Every kind of change.** New, removed, name, description, price, icon, for sale, and badges being turned on or off.
- **Any number of games.** Give a place ID (the number in the game's URL) or a universe ID. Each game can post to its own channel.
- **Quiet first run.** The first check of a game only records what's there, so you don't get hundreds of alerts at once.
- **Never misses one.** It only marks something as seen after Discord accepted the message, and it backs off on Roblox and Discord rate limits.
- **Runs anywhere.** Your PC, a server, or free on GitHub Actions.

## Quick Start

1. In Discord, open the channel's settings › **Integrations** › **Webhooks** › **New Webhook**, then **Copy Webhook URL**.
2. Download this repo and copy `config.example.json` to `config.json`.
3. Put your webhook URL in `config.json`, and the games you want to watch:

```json
{
  "webhook": "https://discord.com/api/webhooks/...",
  "interval": 60,
  "games": [
    { "place": 8737899170 },
    { "universe": 3317771874, "name": "Pet Simulator 99", "watch": ["products"], "webhook": "https://discord.com/api/webhooks/another-channel" }
  ]
}
```

4. Run it:

```
python dev_product_scanner.py
```

It checks every game once a minute and posts whatever changed. What it has seen is saved in `state.json`, so you can stop and start it without getting old alerts.

## Options

| Setting | What it does |
|---|---|
| `webhook` | Where alerts go. A game can set its own `webhook` to post somewhere else. |
| `interval` | Seconds between checks (default 60, minimum 15). |
| `watch` | Any of `"products"`, `"passes"`, `"badges"` (default: all three). A game can set its own. |
| `place` / `universe` | The game. A place ID is the number in `roblox.com/games/<place id>/...`. |
| `name` | Shown at the bottom of each alert. Defaults to the game's name on Roblox. |

| Command | What it does |
|---|---|
| `python dev_product_scanner.py` | Watch forever. |
| `python dev_product_scanner.py --once` | Check every game once and exit (for cron or scheduled tasks). |
| `python dev_product_scanner.py --dry-run` | Print the alerts instead of posting them. |
| `--config path` / `--state path` | Use other files. The config can also come from the `DEV_PRODUCT_SCANNER_CONFIG` environment variable. |

## Run It Free On GitHub Actions

1. Fork this repo.
2. In your fork, go to **Settings › Secrets and variables › Actions** and add a secret called `DEV_PRODUCT_SCANNER_CONFIG` holding your whole `config.json`.
3. Copy [`examples/github-actions.yml`](examples/github-actions.yml) to `.github/workflows/watch.yml`.

It runs every 5 minutes (GitHub's shortest schedule) and keeps `state.json` in the Actions cache between runs.

## Tests

```
python -m unittest discover -s tests
```

The tests use a fake Roblox, so they run offline.

## License

MIT
