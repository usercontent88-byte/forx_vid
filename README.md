# forx_vid catalog scraper

This repository contains a testable metadata scraper and a chained GitHub Actions workflow. It reads video listing cards only; it does not download video or preview media.

Stored URLs are paths rather than full URLs, for example `/video.example/title` or `/folder/xv_16_t.jpg`.

## Extracted fields

- Video page path, title, and source card identifiers
- Display duration and normalized duration in seconds
- Thumbnail and mosaic paths
- A derived preview path (never fetched)
- Tag/category relationships

## Workflow: start once, continue automatically

Open **Actions → Catalog scrape → Run workflow** and start the first batch. With `auto_continue=true`, the workflow queues the next batch automatically until every tag index has been processed. It does not create output artifacts; per-page logs and batch summaries are available in the Actions run logs.

The repository's current tag list contains 2,000 tags. Recommended starting defaults:

| Setting | Default | Allowed |
|---|---:|---:|
| Starting tag index | 0 | 0 or greater |
| Tags per workflow run | 20 | 1–20 |
| Pages per tag | 140 | 1–140 |
| Concurrent tag workers | 4 | 1–8 |
| Delay between requests per tag | 1 second | At least 1 second |
| Write to Neon | Off | On, when configured |
| Auto-continue batches | On | On/Off |

Four workers fetch separate categories concurrently. Every worker has its own HTTP session. Database writes use a shared transaction lock and `ON CONFLICT` upserts, so the database connection is never used by two page-write transactions at once. The crawler reports each page, including the tag, page number, cards extracted, and path, in the Actions log.

The workflow uses GitHub's built-in token to dispatch the next `workflow_dispatch` run. The next run waits on the `catalog-scrape` concurrency group so runs do not overlap. Automatic continuation occurs only if the current workflow completes successfully. Ordinary page-level request errors are logged and stored as partial results so the next batch can continue; database/configuration errors fail the run and stop the chain.

## Setup

Python 3.10+ is recommended.

```bash
python -m venv .venv
# Windows PowerShell: .venv\\Scripts\\Activate.ps1
# macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
```

Run offline tests:

```bash
python -m pytest -q
```

## Neon database writes

To write to Neon, enable `write_db` when starting a workflow and add a repository Actions secret named `DATABASE_URL` for the intended database. The workflow applies `migrations/001_video_catalog.sql` before scraping. Its `CREATE TABLE/INDEX IF NOT EXISTS` statements are idempotent and do not drop or truncate data.

The five tables are `catalog_tags`, `catalog_videos`, `catalog_video_tags`, `catalog_scrape_runs`, and `catalog_scrape_pages`. They keep tags normalized, de-duplicate videos by source + page path, preserve tag associations, and record per-page counts/errors for future diagnosis.

## Local command line

Small local run, with no database writes:

```bash
python -m scraper.catalog_scraper --max-tags 1 --max-pages 1 --delay 2
```

Local scrape with Neon enabled (after setting `DATABASE_URL`):

```bash
python -m scraper.apply_schema
python -m scraper.catalog_scraper --max-tags 1 --max-pages 140 --workers 4 --delay 1 --write-db
```

JSONL export remains optional for local use through `--output-jsonl`; GitHub Actions does not generate or upload artifacts.
