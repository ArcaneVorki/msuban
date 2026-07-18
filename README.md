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

## Scaling to 5000 addresses

At 5000 addresses ÷ 50 per batch = 100 requests/day, spaced 400ms apart by default —
that's roughly 40 seconds of runtime, well within Actions' free minutes for a public repo
(2000 min/month on private repos too, which this barely touches).

If the API starts rate-limiting you, increase `DELAY_BETWEEN_BATCHES_MS` in `scripts/fetch.js`.

## Notes

- The API sometimes returns `banInfo: { banned: false }` with no other fields, and the script
  handles that fine — it just won't show a ban type/dates for those addresses.
- If a batch fails (network error, API downtime), the script retries up to 3 times with backoff,
  then records the failure in the `error` field for those addresses rather than crashing the whole run.
- History files are small JSON, so even years of daily data at 5000 addresses stays well within
  reasonable repo size.
