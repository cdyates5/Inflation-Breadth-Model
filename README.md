# US CPI breadth dashboard

Share of US CPI components rising more than 0.5% QoQ (3-month average) against headline CPI YoY — a rebuild of Variant Perception's chart from BLS item-level data, plus a lead/lag stress-test. A scheduled GitHub Action rebuilds the page every weekday and deploys it to GitHub Pages. CSV downloads work on the live page.

## Files

| Path | Purpose |
|---|---|
| `build.py` | Downloads BLS data, computes breadth, writes `site/index.html` (self-contained, fonts embedded) |
| `requirements.txt` | pandas, numpy, requests |
| `ref/cu.item` | Committed copy of the BLS item hierarchy, used only if the live download fails |
| `.github/workflows/refresh.yml` | Scheduled build + Pages deploy + monthly keepalive |
| `.nojekyll`, `.gitignore` | Housekeeping (`site/` and `.cache/` are never committed) |

## Setup (about 5 minutes)

1. **Create the repo.** On GitHub, create a new repo (e.g. `cpi-breadth`). Upload every file in this folder, keeping the folder structure — `.github/workflows/refresh.yml` must sit at exactly that path. Dotfiles: in the web uploader, drag the whole folder in; or use git:
   ```bash
   git init && git add . && git commit -m "init" && git branch -M main
   git remote add origin https://github.com/<you>/cpi-breadth.git && git push -u origin main
   ```
2. **Turn on Pages via Actions.** Settings → Pages → *Build and deployment* → Source: **GitHub Actions**. (Not "Deploy from a branch".)
3. **Add secrets.** Settings → Secrets and variables → Actions → New repository secret:
   - `BLS_CONTACT_EMAIL` — your email. BLS rejects anonymous scripted downloads; it's sent in the User-Agent. Recommended.
   - `BLS_API_KEY` — optional fallback. Free key from https://data.bls.gov/registrationEngine/ (arrives by email; click the activation link). Used only if the flat-file download is blocked.
4. **Allow the keepalive commit.** Settings → Actions → General → *Workflow permissions* → **Read and write permissions** → Save.
5. **First run.** Actions tab → *Refresh CPI breadth dashboard* → **Run workflow**. Takes 2–3 minutes. The page appears at `https://<you>.github.io/cpi-breadth/`.

## Schedule

`15 14 * * 1-5` — 14:15 UTC weekdays (00:15 or 01:15 Melbourne next day). CPI prints at 08:30 ET, so release day is captured under both EDT and EST. Non-release days redeploy unchanged data with a fresh build stamp. Edit the cron line to change it; GitHub may run scheduled jobs up to ~30 minutes late.

## Safeguards

- **Two data routes.** BLS flat files first; if blocked, the BLS API v2 (needs `BLS_API_KEY`, ~18 requests per run against a 500/day limit).
- **Sanity gate.** The build exits with an error — and nothing is deployed, so the last good page stays live — if the latest month is more than 100 days old, fewer than 200 components report, values are out of range, or history is short.
- **Output check.** The workflow also confirms `index.html` exceeds 100 KB and contains data from 1988.
- **Keepalive.** An empty commit on the 1st of each month stops GitHub disabling the schedule after 60 days without repo activity.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `flat files failed: HTTP 403` then exits | BLS blocking the runner (missing/blocked User-Agent) | Set `BLS_CONTACT_EMAIL`; add `BLS_API_KEY` so the API fallback takes over |
| `BLS API failed: ... daily threshold` | Key missing, unactivated, or over quota | Click the activation link in the BLS email; check the secret has no stray spaces |
| `sanity check: latest observation ... days old` | Delayed CPI release (e.g. government shutdown) | Nothing to fix; the last good page stays up. Raise the `age > 100` limit in `build.py` if you want to deploy anyway |
| Deploy step: `Get Pages site failed` / 404 | Pages source not set to GitHub Actions | Step 2 above |
| Keepalive step: `permission denied` | Workflow token is read-only | Step 4 above |
| Scheduled runs stopped | Repo inactive 60+ days before keepalive ran, or Actions disabled | Actions tab → enable workflow → Run workflow |
| Page updated in Actions but browser shows old version | Pages CDN / browser cache | Hard refresh; wait ~10 minutes |
| Private repo, no Pages option | Pages on private repos needs GitHub Pro/Team | Make the repo public or upgrade |

## Method notes

Universe: every seasonally adjusted US city-average CPI-U index in the main expenditure hierarchy (all levels, excluding All items and special aggregates) — ~284 series historically, ~277 reporting now. A component counts if its SA index is >0.5% above its level three months earlier (not annualised); the share is smoothed with a 3-month average. October 2025 CPI was never published (shutdown); each index is bridged with the log-linear midpoint of September and November. VP's exact basket is proprietary; this universe reproduces its published contours closely. Recession bands are hard-coded NBER dates — add new ones to `NBER` in `build.py` when announced.

Local run: `pip install -r requirements.txt && BLS_CONTACT_EMAIL=you@x.com python build.py` → `site/index.html`.
