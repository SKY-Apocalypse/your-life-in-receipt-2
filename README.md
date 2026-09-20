# Threads — Your Life, In Receipts

An interactive reading of one person's digital life, rebuilt from listening
history and a household spending ledger. Built for the *Your Life, In
Receipts* hackathon brief.

**This copy ships with the dataset trimmed to ~50% of its full size** so the
repo is lighter to upload and clone. Coverage across the full 2013–2024 span
is preserved — every month that had activity in the original still has
receipts here, just fewer per month. See **Full vs. trimmed dataset** below
if you want the complete version instead.

---

## Upload this straight to GitHub — no build step

`index.html` at the root of this repo **is** the site. Everything — CSS,
JS, and the derived data — is inlined into that one file. There is nothing
to install and nothing to build.

1. Create a new repo on GitHub.
2. Upload every file in this folder (or `git push` it — see below).
3. **Settings → Pages** → **Source: Deploy from a branch** → **Branch:
   `main`, folder: `/ (root)`** → Save.
4. Your site is live at `https://<you>.github.io/<repo>/` in a minute or two.

That's it — `index.html` being at the root is what makes this work with the
plainest possible GitHub Pages setup.

```bash
git init
git add .
git commit -m "Threads"
git branch -M main
git remote add origin https://github.com/<you>/<repo>.git
git push -u origin main
```

### Alternative: GitHub Actions (only needed if you edit the source)

This repo also includes `.github/workflows/deploy.yml`, which rebuilds
`dist/index.html` from `src/` + `data/data.json` and deploys it whenever you
push. Use this instead of the root `index.html` if you plan to edit
`src/app.js` or `src/styles.css` — set **Settings → Pages → Source** to
**GitHub Actions** rather than "Deploy from a branch" if you go this route.
You don't need both; pick one.

---

## What this is

Four views over one derived "life graph":

- **Field** — a zoomable canvas, five lanes (listening, places, spending,
  screens, money in), receipts as dots, threads drawn between ones that
  belong together. Drag to move through time, scroll to zoom, click anything.
- **Chapters** — seven stretches of the eleven years, found by looking for
  the month behaviour actually changed (listening hours, night-time share,
  skip rate, taste breadth), described from those numbers.
- **Moments** — scenes reconstructed by grouping receipts of different kinds
  that happened within a few hours of each other, narrated in plain language.
- **Patterns** — artists who burned bright and disappeared, songs played on
  loop inside a single sitting, a yearly pulse chart, a 24-hour listening
  clock.
- **Play the story** — a guided walk through all seven chapters, each
  landing on a real reconstructed moment.

## The data

Two real personal exports, layered with a synthetic one to extend the final
two years:

| Source | What it is | Span |
|---|---|---|
| `spotify_history.csv` | ~150,000 track plays | Jul 2013 – Dec 2024 |
| `Daily Household Transactions.csv` | ~2,460 household ledger entries (Mumbai) | Jan 2015 – Sep 2018 |
| `Augmented_IndiaTransactMultiFacet2024.csv` | Synthetic card-transaction data | 2022 – 2024 |

The first two overlap 2015–2018 and plausibly describe one person, which is
what makes cross-domain connection meaningful. The third does not belong to
the same person — it's a synthetic fraud-detection dataset — and is sampled
lightly to give 2022–24 a spending signal where the real household ledger
had already ended. Disclosed rather than hidden.

## Full vs. trimmed dataset

`data/data.json` in this folder is the **trimmed (~50%)** version — every
month of activity is represented, but roughly half the receipts within each
month were dropped (biased to keep the more significant ones: longer
listening sessions, bigger spends, anything played on repeat). This halves
the payload without hollowing out any part of the timeline.

To regenerate the **full** dataset instead:

1. Download the three source CSVs (search their exact filenames on Kaggle)
   into `data/raw/`.
2. `cd data && python3 build.py` — writes the full `data/data.json`.
3. `cd .. && python3 build_site.py` — writes the full `dist/index.html`.
4. Copy `dist/index.html` over the root `index.html` if you want to deploy
   the full version instead.

## Project structure

```
├── index.html          # ← the deployed site. Self-contained, upload as-is.
├── data/
│   ├── build.py          # derivation pipeline (session → moments → chapters)
│   ├── data.json           # trimmed derived data (this is what index.html contains)
│   └── raw/                 # put source CSVs here to regenerate the full dataset
├── src/
│   ├── index.html            # shell with placeholder comments for the build step
│   ├── styles.css             # all styling, light + dark theme via CSS variables
│   └── app.js                  # the whole app: field renderer, views, story mode
├── build_site.py                # inlines src/ + data/data.json → dist/index.html
└── .github/workflows/deploy.yml  # optional: auto-rebuild on push (see above)
```

## Stack

Vanilla HTML/CSS/JS — no framework, no bundler. The derivation pipeline is
Python (pandas + numpy), run once, offline; the browser only reads and
renders the resulting JSON. Chosen deliberately for a 6-hour build: zero
install friction, zero build-step debugging, one file that deploys anywhere.

## Known limitations

- The 2022–24 spending signal is synthetic (see **The data** above).
- Household ledger place names are pre-anonymised in the source dataset
  (`Place 0`, `Place 2`, etc.) and shown as-is.
- This copy's dataset is trimmed to ~50%; the field view already applies
  its own level-of-detail thinning on top of that when zoomed out across
  the full span (it says so in the on-screen hint) — zoom into any stretch
  to see everything that survived the trim.
