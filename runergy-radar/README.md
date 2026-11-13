# Runergy Radar

Finds public conversations where runners are asking for what Runergy Lab offers, drafts a
reply in your voice, and puts both in an inbox. You read the draft, edit it if you like, and
post it yourself with one click.

- **Listens** on Reddit, Threads, running forums and the wider web for your trigger phrases.
- **Scores** every conversation, so real questions rise and ads, memes and off-topic matches sink.
- **Drafts** a helpful reply for each good match (Claude), following your description of the app.
- **Never posts for you.** "Copy reply & open thread" puts the text on your clipboard and opens
  the thread; you paste and post under your own account.

It lives in this folder and is separate from the flight searcher in the rest of the repository.

## Why it doesn't post automatically

This is deliberate, and it protects the brand:

- **Reddit** bans accounts that post automated promotional replies, and many running subreddits
  remove self-promotion. A banned founder account is hard to undo.
- **Facebook** offers no way for a tool to search public posts or groups, or to reply as a
  personal account. Facebook is therefore not covered. (Comments on a Page you own could be added.)
- A reply that reads like a person who knows running, with an honest "I'm part of the team",
  converts far better than a bot reply and can't be mistaken for spam.

## How conversations are found

| Source | Covers | Cost | Needs |
|---|---|---|---|
| **Google search** (through SerpApi) | Reddit, Threads, forums: any sites you list | 1 SerpApi credit per search | `SERPAPI_API_KEY` |
| **Google Alerts feeds** | The whole web, including Reddit and forums | Free | Feed links pasted into Settings |
| **Any RSS/Atom feed** | A forum or blog you care about | Free | Feed link pasted into Settings |
| **Threads search** (optional) | Threads posts, directly | Free | `THREADS_ACCESS_TOKEN` + Meta App Review |

Things worth knowing:

- **Reddit is reached through Google.** By the reports available in November 2026, Reddit has
  switched off its RSS feeds and stopped accepting new apps for its public API, so reading Reddit
  directly is no longer possible for a new tool. Google indexes Reddit quickly (usually within
  hours), which is what the search and the alerts use. Expect threads to appear a few hours after
  they are posted, not within seconds.
- **Searches are rationed.** You set a number of Google searches per day (default 30). They are
  spread evenly across the day and your trigger phrases take turns, so the budget can never be
  overspent. 30 a day is about 900 a month, so check which SerpApi plan covers that. If you share
  one SerpApi key with the flight searcher they share its quota; set `RADAR_SERPAPI_KEY` to give
  the radar its own.
- **Threads search is the least certain part.** It uses Meta's official keyword search. Until
  Meta approves the `threads_keyword_search` permission for your Meta app (App Review), Meta only
  returns your own posts. Without it, Threads posts are still found through Google search.
  This connector was written from Meta's published API and could not be tried against Meta's
  servers during development; if it misbehaves, the Sources page will say so.

## Set it up (about 20 minutes)

### 1. Put the dashboard online (Vercel)

1. In Vercel choose **Add New → Project** and import this repository again (a second project).
2. Set **Root Directory** to `runergy-radar`. Leave everything else as it is and deploy.
3. In the project open **Storage → Create Database → Neon (Postgres)** and connect it to the
   project. This adds `DATABASE_URL` for you.
4. In **Settings → Environment Variables** add:

   | Name | Value |
   |---|---|
   | `RADAR_PASSWORD` | A long password. You sign in to the dashboard with it. |
   | `CRON_SECRET` | Any long random text. Lets the scheduled scan in. |
   | `ANTHROPIC_API_KEY` | From console.anthropic.com. Enables scoring and draft replies. |
   | `SERPAPI_API_KEY` | From serpapi.com. Enables the Google search source. |
   | `THREADS_ACCESS_TOKEN` | Optional. A long-lived Threads API token. |

5. **Redeploy** so the variables take effect, open the site and sign in.

The dashboard refuses to open online until `RADAR_PASSWORD` is set, and warns you in red if no
database is connected.

### 2. Turn on automatic scans (GitHub)

In this repository open **Settings → Secrets and variables → Actions** and add two secrets:

| Secret | Value |
|---|---|
| `RADAR_URL` | Your dashboard address, e.g. `https://your-radar.vercel.app` |
| `CRON_SECRET` | The same value you gave Vercel |

The workflow `.github/workflows/engagement-radar.yml` then asks the dashboard to scan every
30 minutes. GitHub only runs schedules from the default branch, so this starts once this work is
merged into `main`. (GitHub pauses schedules in repositories with no activity for 60 days and
emails you first.) Vercel also triggers one scan a day as a backup.

### 3. Tell it about your app (Settings page)

- **Your app and your voice**: the AI states only what you write here. It ships with the
  Runergy Lab description (AI-powered training guidance from your wearable data, Garmin
  integration, 5K to marathon). Add the link to share, platforms and price when you want them
  mentioned, and keep it current.
- **Topics and trigger phrases**: starter lists are included; tune them to how runners talk.
- **Google Alerts**: follow the three steps shown on the Settings page to add free alert feeds.

Then press **Scan now**.

## Using it day to day

- **To reply**: new conversations, best match first. Edit the draft, press **Copy reply & open
  thread**, paste, post, then confirm **Yes, mark as replied**.
- **Rewrite with AI**: ask for a change ("shorter", "don't mention the app").
- **Save for later / Dismiss**: both are kept in their own tabs and can be moved back.
- **Low relevance**: what the AI judged a poor fit. Nothing is ever deleted.
- **Sources**: whether each source is working, today's search usage, and recent scans. If a key
  expires or a feed breaks, it shows here in red with the reason.

## What it costs

- **Vercel and Neon**: the free plans are enough.
- **SerpApi**: one credit per search; you choose the daily number.
- **Claude**: one request per new conversation, using `claude-opus-5-5` by default. Expect a
  few cents per conversation. "AI drafts per scan" caps it; set `RADAR_MODEL` to use another model.
- **Google Alerts**: free.

## Run it on your own computer

```bash
cd runergy-radar
pip install -r requirements.txt
python -m radar demo      # example conversations, no keys needed
python -m radar serve     # http://localhost:8010
python -m radar scan      # one real scan (uses whatever keys are set)
```

Locally it stores everything in `data/radar.db` and needs no password.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest -q tests
# also run the storage tests against Postgres:
RADAR_TEST_POSTGRES=postgresql://user:pass@localhost:5432/db python -m pytest -q tests
```

## All settings read from the environment

| Variable | Purpose |
|---|---|
| `RADAR_PASSWORD` | Dashboard password (required online). |
| `CRON_SECRET` | Lets the scheduled workflow and Vercel's cron start a scan. |
| `DATABASE_URL` | Postgres connection (also read: `POSTGRES_URL`, `RADAR_DATABASE_URL`). |
| `ANTHROPIC_API_KEY` | Claude key for scoring and drafts. |
| `RADAR_MODEL` | Claude model, default `claude-opus-5-5`. |
| `SERPAPI_API_KEY` | SerpApi key (`RADAR_SERPAPI_KEY` takes priority if set). |
| `THREADS_ACCESS_TOKEN` | Optional Threads API token; renewed automatically each week. |
| `RADAR_DB` | Path of the local SQLite file. |
