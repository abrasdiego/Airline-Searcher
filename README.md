# Airline-Searcher

Finds the cheapest flights between two cities within a travel window (for example
a 2-week window) and emails you the best prices **every day**.

For each route you configure, it:

1. Works out every departure/return date combination inside the window
   (e.g. every 7-night round trip that fits in 10–23 Jan).
2. Searches each combination through a flight-data API.
3. Ranks the results and sends an email with:
   - the **best price today**, and how it compares with yesterday,
   - the **top N cheapest** options (airline, stops, duration, link),
   - a **price-per-date calendar** with ▲/▼ changes since the last run.

It runs for free on a schedule in **GitHub Actions**, so nothing has to stay running on your computer.
The same results are also published to a phone-friendly web page, **Fare Watch** (see below).

## Quick start (local)

```bash
pip install -r requirements.txt

# See which dates would be searched (makes no API calls)
python -m airline_searcher --plan

# Try it with fake prices; no API key or email needed
python -m airline_searcher --provider mock --no-email --save-html report.html
```

## 1. Configure your routes: `config.yaml`

```yaml
searches:
  - name: Lisbon trip
    origin: JFK
    destination: LIS
    window_start: 2027-01-10
    window_days: 14          # 2-week window (or set window_end)
    trip_type: round_trip    # or one_way
    nights: 7                # or {min: 5, max: 9}
    max_stops: 1
    currency: USD
    top_n: 5
```

Use `window_starts_in_days: 60` instead of `window_start` for a rolling window that
always looks 60 days ahead. You can list as many searches as you like.
`config.yaml` documents every option.

## 2. Get a flight-data API key

| Provider | `settings.provider` | Secret | Notes |
|---|---|---|---|
| [SerpApi – Google Flights](https://serpapi.com/google-flights-api) | `serpapi` (default) | `SERPAPI_API_KEY` | Google Flights prices from all airlines, with a link to the results. Has a free monthly quota. |
| [Duffel](https://duffel.com) | `duffel` | `DUFFEL_ACCESS_TOKEN` | Live airline offers (NDC/GDS). Prices are in your Duffel account's currency. |

**Watch your quota.** Every date combination is one API call. Some examples per day:

| Window | Trip | Calls/day | Calls/month |
|---|---|---|---|
| 14 days | one-way | 14 | ~420 |
| 14 days | round trip, exactly 7 nights | 7 | ~210 |
| 14 days | round trip, 5–9 nights | 35 | ~1,050 |

Run `--plan` to see the exact number. `settings.max_requests_per_search` caps the calls per run.

## 3. Set up email

Any SMTP server works. For **Gmail**, turn on 2-Step Verification and create an
[App Password](https://myaccount.google.com/apppasswords). Then set:

| Variable | Example |
|---|---|
| `SMTP_HOST` | `smtp.gmail.com` (default) |
| `SMTP_PORT` | `587` (default; `465` for SSL) |
| `SMTP_USERNAME` | `you@gmail.com` |
| `SMTP_PASSWORD` | your app password |
| `EMAIL_FROM` | optional, defaults to `SMTP_USERNAME` |
| `EMAIL_TO` | `you@example.com` (comma-separate for several people) |

## 4. Run it every day with GitHub Actions

1. In the repo, open **Settings → Secrets and variables → Actions** and add
   `SERPAPI_API_KEY` (or `DUFFEL_ACCESS_TOKEN`), `SMTP_USERNAME`, `SMTP_PASSWORD` and `EMAIL_TO`
   (plus `SMTP_HOST`/`SMTP_PORT` if you're not using Gmail).
2. Merge to the default branch. The workflow in `.github/workflows/daily-flight-search.yml`
   runs every day at 11:17 UTC. Change the `cron` line to pick another time.
3. To test it right away, open **Actions → Daily flight search → Run workflow**.

Price history is kept in the Actions cache, so the email can show what changed since the
previous day. GitHub pauses scheduled workflows in repos with no activity for 60 days. If the
emails stop, re-enable the workflow from the Actions tab.

## 5. The web page (Fare Watch)

`web/index.html` is a mobile-friendly page that shows the same results as the email:
best fare, best price by day, a price calendar with changes since the last check, and the
cheapest 5, with a tab for each route. It also has a planner that previews any route with sample
prices and generates the `config.yaml` entry for it.

The daily workflow publishes it to GitHub Pages with that day's results in `data/latest.json`.
To turn it on, open **Settings → Pages** and set **Source** to **GitHub Actions**. After the next run
the page is at `https://<your-user>.github.io/Airline-Searcher/`. Add it to your phone's home
screen to open it like an app.

- The page updates once a day, when the email is sent. To refresh it sooner, run the workflow by hand.
- The API key never reaches the page. Prices are fetched by the GitHub Action, and the page only
  reads the results file.
- GitHub Pages sites are public, so anyone with the link can see your routes and prices.
- Opened without the results file (locally, or before the first run) the page shows sample prices.

## Running it yourself instead (cron)

```bash
export SERPAPI_API_KEY=... SMTP_USERNAME=... SMTP_PASSWORD=... EMAIL_TO=...
python -m airline_searcher -c config.yaml
```

```
# crontab -e: every day at 08:00
0 8 * * * cd /path/to/Airline-Searcher && /usr/bin/python3 -m airline_searcher >> search.log 2>&1
```

## CLI options

```
-c, --config FILE    config file (default config.yaml)
--provider NAME      override the provider: serpapi | duffel | mock
--plan               list the date combinations without searching
--no-email           print the report instead of emailing it
--save-html FILE     also save the HTML email
--save-json FILE     also save the results as JSON (what the web page reads)
--no-history         don't read or update price history
-v, --verbose        log each API call
```

## Development

```bash
pip install -r requirements-dev.txt
python -m pytest
```
