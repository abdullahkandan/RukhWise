# RukhWise

**Pakistan's job market, measured, not guessed.**

Live: **[rukhwise.vercel.app](https://rukhwise.vercel.app)**

RukhWise is a continuously running intelligence system on the Pakistani job market. It collects postings daily without human intervention, extracts skill demand against a curated taxonomy, and is being built toward a self-grading forecast loop: predictions logged before outcomes are known, then graded automatically when reality arrives.

No live, longitudinal, publicly self-auditing system like this exists for Pakistan. Existing resources are static scrapes, synthetic datasets, or one-off reports. The moat here is not the dashboard. It is the dataset that grows every day and the honesty architecture around it.

*Rukh* (رخ) means direction in Urdu. The name is a bilingual pun.

---

## What it does

- **Collects daily.** A GitHub Actions pipeline scrapes Pakistani job boards every morning at 08:00 PKT, deduplicates against history, and upserts into Postgres. The system runs whether or not anyone is watching.
- **Tracks skills honestly.** Postings are matched against a 96-skill, 11-category taxonomy built from two discovery rounds on real data. Counts are reported by **distinct postings and distinct companies**, because one bulk poster flooding a feed with templated listings should not look like market demand.
- **Answers the question that matters.** The Analyzer flips the usual dashboard framing from "what is happening" to "what should I do": pick your skills, see the absolute number of postings you strongly match, then see which single skill unlocks the most additional matches.
- **Grades itself (in development).** The forecasting engine will log weekly predictions immutably before outcomes exist, then auto-grade them against a naive no-change baseline when the week completes. Accuracy will be published even when it is embarrassing. A forecast only counts if it beats doing nothing.

## Architecture

```
Python scrapers ──> Supabase (Postgres) ──> FastAPI (Render) ──> Next.js ISR (Vercel)
       ▲                                                                │
       └────────── GitHub Actions, daily 03:00 UTC ◄────────────────────┘
```

- **Collection:** Python scrapers per source, unified upsert with `UNIQUE(source, detail_url)`. Re-seen postings only touch `last_seen_at`, which is what makes longitudinal analysis possible.
- **Extraction:** Unicode-aware whole-word and phrase matching against the taxonomy, with per-skill edge handling (`c++`/`c#` supported; bare `r` dropped after producing only false positives on real data).
- **API:** Read-only FastAPI service using an anonymous key against row-level-security tables. Ten-minute response cache.
- **Frontend:** Next.js App Router with 10-minute ISR, so pages are static-fast but never more than minutes stale relative to the API.

### Methodology decisions worth noting

- **Medians and IQR for salaries, never means.** One implausible posting should not move the market.
- **Bulk-poster exclusion.** A single company accounts for roughly 45% of the corpus with templated postings. Every skill metric can be viewed with bulk posters excluded, and concentration is disclosed on the site rather than hidden.
- **Partial-week guards.** Trend and mover calculations refuse to compare incomplete weekly buckets. Early versions produced confident garbage from partial data; the guards exist because of that failure, and it is documented on the methodology page.
- **Distinct-company counting as anti-template defense.** A skill appearing in 287 postings from 17 companies is a different market signal than one in 279 postings from 64 companies. Both numbers are always shown.

## Data sources

| Source | Method | Notes |
|---|---|---|
| Mustakbil | Automated, daily | Primary source. Public API, listing + detail enrichment |
| Rozee.pk | Semi-manual, weekly | Cloudflare-protected; collected via a human-attended browser session |
| BrightSpyre | Supplementary | |
| LinkedIn | **Deliberately excluded** | Scraping violates ToS. A system built on honest self-grading cannot stand on dishonest collection |

## Known limitations

- The corpus skews toward sources that permit collection, which skews sectoral coverage. This is disclosed, not corrected silently.
- Salary data is sparse and platform conventions are noisy; salary views are labeled accordingly.
- Skill extraction is taxonomy-based (v1), so skills outside the taxonomy are invisible until a taxonomy revision. Extraction method is versioned per mention so revisions remain auditable.
- History is young. Forecast-dependent features (momentum, emerging skills, seasonality) are intentionally gated until the dataset has earned them.

## Stack

Python, FastAPI, Supabase (Postgres + RLS), GitHub Actions, Next.js (App Router, ISR), Tailwind CSS, GSAP. Deployed on Render (API) and Vercel (frontend).

## Running locally

```bash
# API
cd api
pip install -r requirements.txt
# .env: SUPABASE_URL, SUPABASE_ANON_KEY
uvicorn main:app --port 8000

# Frontend
cd frontend
npm install
# .env.local: NEXT_PUBLIC_API_URL=http://localhost:8000
npm run dev
```

Collection and extraction entry points: `collect.py --source mustakbil --pages 10`, `extract.py --all`. Scraper credentials and write access are not required to run the read-only API and frontend against the public data.

---

Built by **Abdullah Kandan**. BS Data Science, Institute of Space Technology, Islamabad.
