# Wallet Ban Tracker

Checks a list of wallet addresses against the ban-check API once a day,
stores the history, and shows it on a small dashboard.

## How it works

- `data/addresses.json` — the list of addresses you want to monitor (grow this up to 5000)
- `scripts/fetch.js` — batches addresses into groups of 50, calls the API, writes results
- `.github/workflows/daily-check.yml` — runs the script once a day via GitHub Actions and commits the results
- `data/history/<date>.json` — one file per day, full results
- `data/latest.json` — most recent run
- `data/manifest.json` — list of dates that have history, used by the dashboard
- `index.html` — the dashboard (charts + table), served by GitHub Pages

## Setup (one-time)

1. **Create a new GitHub repo** (public or private both work — private is fine, Pages still works on private repos with GitHub Pro, but if you're on a free personal account Pages requires the repo to be public).

2. **Push this folder to it.** From this folder, in a terminal (PowerShell or Git Bash on Windows):
   ```
   git init
   git add .
   git commit -m "Initial commit"
   git branch -M main
   git remote add origin https://github.com/<your-username>/<your-repo>.git
   git push -u origin main
   ```

3. **Enable GitHub Pages**
   - Go to your repo → Settings → Pages
   - Source: "Deploy from a branch"
   - Branch: `main`, folder: `/ (root)`
   - Save. Your dashboard will be live at `https://<your-username>.github.io/<your-repo>/` within a minute or two.

4. **Enable Actions permissions to push commits**
   - Repo → Settings → Actions → General → scroll to "Workflow permissions"
   - Select **"Read and write permissions"**
   - Save. (Without this, the daily job can run the check but won't be able to commit the results back.)

5. **Add your real address list**
   - Edit `data/addresses.json`, replace with your full list (up to 5000 addresses)
   - Commit and push

6. **Test it manually before waiting for the schedule**
   - Repo → Actions tab → "Daily Wallet Ban Check" → "Run workflow" (this is the `workflow_dispatch` trigger)
   - Watch it run, then check that `data/latest.json` updated and the dashboard reflects it

## Adjusting the schedule

The cron in `.github/workflows/daily-check.yml` is set to `0 13 * * *` (13:00 UTC daily).
GitHub Actions cron times are always UTC — adjust the hour to whenever you want it to run.
Note: scheduled jobs on free GitHub accounts can be delayed by a few minutes during high load; that's normal.

## Building the top-5000 player/wallet list

`scripts/build-player-list.js` pulls the leaderboard and resolves each character to a
wallet address + attack power, then writes:

- `data/players.json` — full detail per player (rank, name, level, wallet, attack power)
- `data/addresses.json` — deduped wallet addresses, which is what `scripts/fetch.js` reads
  for the daily ban check

Run it manually from the **Actions** tab → "Build Player & Wallet List" → "Run workflow"
(you can override how many players to pull there, default 5000). It's also scheduled to
run weekly on Sundays — rankings don't shift enough to justify pulling them daily. Comment
out or remove the `schedule:` block in that workflow if you'd rather trigger it by hand only.

This does ~500 ranking requests plus one request per character (up to 5000) — around 5500
requests total. Calls run **one at a time with a 1-second delay between each** by default
(`CONCURRENCY=1`, `DELAY_MS=1000`), to stay polite to the API. That means a full run takes
roughly **90 minutes**. If you want it faster and are confident the API can handle it, raise
`CONCURRENCY` in the workflow file (note: with concurrency > 1, `DELAY_MS` only paces each
parallel lane, so calls will overlap rather than being strictly 1 second apart overall).

Note this **overwrites** `data/addresses.json` each time it runs — it always reflects the
current top-N leaderboard, not an accumulated list. If you want to track wallets you've
manually added on top of the leaderboard, keep those in a separate file rather than editing
`addresses.json` directly, since this script will replace it wholesale.

## Scaling to 5000 addresses

At 5000 addresses ÷ 50 per batch = 100 requests/day, spaced 10 seconds apart by default —
that's roughly 17 minutes of runtime, well within Actions' free minutes for a public repo
(2000 min/month on private repos too, which this barely touches).

If the API starts rate-limiting you, increase `DELAY_BETWEEN_BATCHES_MS` in `scripts/fetch.js`.

## Notes

- The API sometimes returns `banInfo: { banned: false }` with no other fields, and the script
  handles that fine — it just won't show a ban type/dates for those addresses.
- If a batch fails (network error, API downtime), the script retries up to 3 times with backoff,
  then records the failure in the `error` field for those addresses rather than crashing the whole run.
- History files are small JSON, so even years of daily data at 5000 addresses stays well within
  reasonable repo size.
