# Yad2 Rental Tracker

A lightweight, **serverless** tool that watches [Yad2](https://www.yad2.co.il) for
rental properties matching a fixed set of criteria, detects **new listings** and
**price drops**, tracks **relisted** and **removed** properties, and pushes results
to **Telegram** and a **live-updating dashboard**.

No always-on server: a GitHub Actions cron job does the polling, Supabase stores
state (with Realtime), and a Next.js app on Vercel renders a live table.

```
scraper (GitHub Actions, every 30m)  ──writes──▶  Supabase (Postgres + Realtime)
        │                                                │
        └──▶ Telegram (new / drop / relist)              └──reads──▶ Next.js dashboard (Vercel)
```

## Repo layout

```
yad2-rental-tracker/
├── scraper/
│   ├── config.json          # search criteria (edit this, not the code)
│   ├── yad2_mappings.json    # feed URL + property-type codes (VERIFY against live site)
│   ├── yad2_client.py        # fetch + parse (JSON primary, Playwright fallback)
│   ├── sync.py               # delta engine: new / price_drop / relisted / removed
│   ├── notifier.py           # Telegram
│   ├── db.py                 # Supabase client wrapper
│   ├── run.py                # entrypoint called by the cron job
│   ├── test_smoke.py         # offline tests for normalize + delta engine
│   └── requirements.txt
├── dashboard/                # Next.js app (Vercel) + Supabase Realtime
├── supabase/
│   └── schema.sql            # table + indexes + RLS + realtime publication
├── .github/workflows/
│   └── poll.yml              # cron workflow (see "Scheduling" below)
├── .env.example
└── README.md
```

## How it works (delta engine — `sync.py`)

On each run the scraper fetches every target, dedupes, then per listing:

- **New** (id unseen) → insert `status='active'`, `first_seen=now()`. → notify.
- **Price drop** (fetched price < stored) → **does not overwrite**: sets
  `previous_price`, new `price`, `price_diff = old - new`, `price_changed_at`. → notify.
- **Price increase** → updates `price` silently (no alert).
- **Relisted** (id existed with `status='removed'`) → back to `active`, `relisted=true`. → notify + highlighted in the dashboard.
- **Still present** → refresh `last_seen`.
- **Removed** (active in DB, absent this run for its target) → `status='removed'` (retained so a future relist is detectable).

**False-removed guard:** if a target query errors or returns zero results, the
removal step is **skipped for that target** this run.

---

## ⚠️ Resolving Yad2 IDs (read before first run)

Yad2 has **no public API**. The site loads listings from internal JSON endpoints
(`gw.yad2.co.il/realestate-feed/...`) behind anti-bot protection. The endpoint
shape, query-parameter names, and the **internal numeric IDs** for cities,
neighborhoods, and property types **must be confirmed against the live site** —
they change and must not be guessed.

This project does NOT hardcode guessed IDs:

- **City / neighborhood IDs** are resolved **at runtime** from Yad2's
  `address-autocomplete/realestate/v2` endpoint by the Hebrew names in
  `config.json` (`Yad2Client.resolve_location`).
- **Property-type codes** and the **feed URL** live in
  `scraper/yad2_mappings.json` (`"verified": false`) so they can be corrected
  without touching code.

**To verify the mappings:**

1. Open a Yad2 rental search in a browser and open DevTools → Network.
2. Apply each filter (city, neighborhood, price, rooms, property type, parking,
   size). Watch the request to the `realestate-feed` endpoint.
3. Confirm the **feed URL** and the exact **query-parameter names** the site uses
   (this project sends `city`, `area`, `topArea`, `neighborhood`, `price`,
   `rooms`, `squaremeter`, `parking`, `property`, `page` — adjust if they differ).
4. Tick a single property type and read the `property` param's numeric value;
   put it in `yad2_mappings.json → property_type_codes`. Then set `"verified": true`.

> This sandbox cannot reach `gw.yad2.co.il`, so the mappings shipped here are
> **commonly-observed defaults, not verified**. Confirm them before trusting a run.

Reference scrapers worth studying (verify they still work): `DavOstx7/yad2-scraper`,
`NivEz/yad2-scraper`.

If the JSON path is consistently blocked, the Playwright fallback renders the
search page headlessly and extracts the embedded `__NEXT_DATA__` JSON. As a last
resort, a managed actor (e.g. Apify) can replace `yad2_client.py` behind the same
interface.

---

## Provisioned infrastructure

A live Supabase project has been provisioned for this tracker:

- **Project ref:** `gjlhmgfshumsskrnsxfi` (org "Ron's Becher Projects", region `eu-central-1`)
- **API URL:** `https://gjlhmgfshumsskrnsxfi.supabase.co`
- The `listings` table, indexes, anon-read RLS policy, and the Realtime
  publication are already applied (`supabase/schema.sql`).
- The public anon key is baked into `dashboard/.env.production` (browser-safe;
  RLS restricts access). For the **scraper** you still need the **service-role**
  key — copy it from the Supabase dashboard into `SUPABASE_SERVICE_KEY`.

**Vercel:** deployment could not be automated from the build environment (no
Vercel token + egress to vercel.com is blocked). To deploy: import this repo in
Vercel, set the **Root Directory** to `yad2-rental-tracker/dashboard`, and push —
the `NEXT_PUBLIC_*` env vars are already in `.env.production`, so no extra config
is needed. (Or run `vercel deploy` from `dashboard/` with the CLI authenticated.)

## Setup

### 1. Supabase
1. Create a Supabase project.
2. Run `supabase/schema.sql` in the SQL editor (creates the `listings` table,
   indexes, RLS read policy, and the Realtime publication).
3. Note the **project URL**, the **service-role key** (scraper writes), and the
   **anon key** (dashboard reads).

### 2. Scraper (local dry run)
```bash
cd yad2-rental-tracker
pip install -r scraper/requirements.txt
python -m playwright install chromium      # only needed for the fallback path

# print results without touching DB/Telegram:
python scraper/yad2_client.py

# run the full pipeline (needs env below):
cp .env.example .env   # fill in values
python scraper/run.py

# offline logic tests:
python scraper/test_smoke.py
```

Required env (see `.env.example`):
`SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.

### 3. Telegram
1. Create a bot via [@BotFather](https://t.me/BotFather) → `TELEGRAM_BOT_TOKEN`.
2. Get your channel/chat id → `TELEGRAM_CHAT_ID`.

### 4. Scheduling (GitHub Actions)
GitHub only runs workflows at the **repo root** `.github/workflows/`. Since this
project lives in a subdirectory, copy `yad2-rental-tracker/.github/workflows/poll.yml`
to `<repo-root>/.github/workflows/poll.yml` (the paths inside already account for
the subdirectory). Then add the four secrets under **Settings → Secrets and
variables → Actions** and test with **Run workflow** (`workflow_dispatch`).

### 5. Dashboard (Vercel)
```bash
cd dashboard
npm install
cp .env.local.example .env.local   # NEXT_PUBLIC_SUPABASE_URL / _ANON_KEY
npm run dev
```
Deploy to Vercel and set `NEXT_PUBLIC_SUPABASE_URL` and
`NEXT_PUBLIC_SUPABASE_ANON_KEY` in the project env. The table subscribes to
Supabase Realtime and updates with no manual refresh; rows with a price drop or
`relisted=true` are highlighted.

---

## Editing the search

Everything is data in `scraper/config.json` — price/rooms/size/property-types,
and the `targets` list (city + optional neighborhoods). Herzliya is scoped to
*only* Herzliya Pituach + Nof Yam; Kfar Shmaryahu is the whole city; Ramat
HaSharon is *only* the Golan neighborhood. The client runs **one query per
target** and merges + dedupes by listing id.

## Politeness & ToS

Yad2's ToS prohibits automated scraping and they actively defend against it. This
is a **personal, low-frequency** tool: keep `poll_minutes` ≥ 30, randomized small
delays between queries, retries with backoff, and a hard per-run request cap (all
enforced in `yad2_client.py`).
