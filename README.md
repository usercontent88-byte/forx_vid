# forx_vid catalog scraper

A test-first scraper for listing-card metadata. It does not download video or preview files. Stored URLs are paths rather than full URLs, for example \`/video.example/title\` or \`/folder/xv_16_t.jpg\`.

## Extracted fields

- Video page path and title
- Displayed duration and normalized duration in seconds
- Thumbnail and mosaic image paths
- An optional preview path derived from the thumbnail directory (never fetched)
- Source card IDs and category/tag associations

The database schema reserves nullable fields for future descriptions, likes and views, and stores selected source fields in JSONB so later detail-page parsing can be extended.

## Setup

Python 3.10+ is recommended.

\`\`\`bash
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
\`\`\`

\`tag_list.json\` should be present in the repository root. Never commit a real \`DATABASE_URL\`; keep it in an environment variable or a local ignored file.

## 1. Run offline tests

\`\`\`bash
pytest -q
\`\`\`

The tests use a local HTML fixture and do not make network requests or write to Neon.

## 2. Run a tiny live smoke test

Start with just one category and one page, exporting JSONL without database writes:

\`\`\`bash
python -m scraper.catalog_scraper --max-tags 1 --max-pages 1 --delay 2 --output-jsonl tmp/one-tag.jsonl
\`\`\`

Inspect the summary and sample rows before expanding. Only collect and reuse metadata where you have permission, and check the source site's terms and crawling rules first.

## 3. Use Neon

Apply \`migrations/001_video_catalog.sql\` to the intended database/branch. Then set \`DATABASE_URL\` in your environment and run one controlled DB test.

PowerShell:

\`\`\`powershell
$env:DATABASE_URL = "<your Neon connection string>"
python -m scraper.catalog_scraper --max-tags 1 --max-pages 1 --delay 2 --write-db
\`\`\`

bash/zsh:

\`\`\`bash
export DATABASE_URL="<your Neon connection string>"
python -m scraper.catalog_scraper --max-tags 1 --max-pages 1 --delay 2 --write-db
\`\`\`

Database writes are opt-in. Without \`--write-db\`, the script does not connect to or modify Neon.

## 4. Expand carefully

- More tags: \`--max-tags 10\`
- Up to 40 pages per selected tag: \`--max-tags 10 --max-pages 40\`
- Full tag list: \`--all-tags --max-pages 40\`

The current \`tag_list.json\` contains 2,000 tags, so a full run could request up to 80,000 pages. Start with small runs, keep a delay, watch failures, and stop if the source blocks or disallows collection. The crawler follows the page's real next link, stops at no cards/no next link, and detects pagination loops.

## Schema overview

- \`catalog_tags\`: normalized categories
- \`catalog_videos\`: one row per source + video path
- \`catalog_video_tags\`: many-to-many video/tag relationships
- \`catalog_scrape_runs\`: run settings, totals and errors
- \`catalog_scrape_pages\`: per-page outcomes for diagnosis and retry planning

The initial migration uses \`CREATE TABLE/INDEX IF NOT EXISTS\` so it can be applied repeatedly.
