-- Initial normalized catalog schema. Safe to apply repeatedly.
-- Store source paths, not hostnames. No media files are downloaded by the scraper.

CREATE TABLE IF NOT EXISTS catalog_tags (
    id BIGSERIAL PRIMARY KEY,
    source TEXT NOT NULL DEFAULT 'xvideos',
    slug TEXT NOT NULL,
    label TEXT NOT NULL,
    tag_path TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT catalog_tags_source_slug_key UNIQUE (source, slug)
);

CREATE TABLE IF NOT EXISTS catalog_videos (
    id BIGSERIAL PRIMARY KEY,
    source TEXT NOT NULL DEFAULT 'xvideos',
    external_id TEXT,
    external_eid TEXT,
    page_path TEXT NOT NULL,
    title TEXT NOT NULL,
    duration_text TEXT,
    duration_seconds INTEGER CHECK (duration_seconds IS NULL OR duration_seconds >= 0),
    thumbnail_path TEXT,
    mosaic_path TEXT,
    preview_path TEXT,
    description TEXT,
    likes_count BIGINT CHECK (likes_count IS NULL OR likes_count >= 0),
    views_count BIGINT CHECK (views_count IS NULL OR views_count >= 0),
    raw_source_data JSONB NOT NULL DEFAULT '{}'::jsonb,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT catalog_videos_source_page_key UNIQUE (source, page_path)
);

CREATE INDEX IF NOT EXISTS catalog_videos_last_seen_idx ON catalog_videos (last_seen_at DESC);
CREATE INDEX IF NOT EXISTS catalog_videos_created_idx ON catalog_videos (created_at DESC);
CREATE INDEX IF NOT EXISTS catalog_videos_title_search_idx ON catalog_videos USING GIN (to_tsvector('simple', title));

CREATE TABLE IF NOT EXISTS catalog_video_tags (
    video_id BIGINT NOT NULL REFERENCES catalog_videos(id) ON DELETE CASCADE,
    tag_id BIGINT NOT NULL REFERENCES catalog_tags(id) ON DELETE CASCADE,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (video_id, tag_id)
);
CREATE INDEX IF NOT EXISTS catalog_video_tags_tag_video_idx ON catalog_video_tags (tag_id, video_id);

CREATE TABLE IF NOT EXISTS catalog_scrape_runs (
    id BIGSERIAL PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'running'
      CHECK (status IN ('running', 'completed', 'partial', 'failed')),
    expected_tags INTEGER NOT NULL DEFAULT 0 CHECK (expected_tags >= 0),
    tags_processed INTEGER NOT NULL DEFAULT 0 CHECK (tags_processed >= 0),
    pages_visited INTEGER NOT NULL DEFAULT 0 CHECK (pages_visited >= 0),
    cards_seen INTEGER NOT NULL DEFAULT 0 CHECK (cards_seen >= 0),
    config JSONB NOT NULL DEFAULT '{}'::jsonb,
    summary JSONB NOT NULL DEFAULT '{}'::jsonb,
    errors JSONB NOT NULL DEFAULT '[]'::jsonb,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS catalog_scrape_runs_started_idx ON catalog_scrape_runs (started_at DESC);

CREATE TABLE IF NOT EXISTS catalog_scrape_pages (
    id BIGSERIAL PRIMARY KEY,
    run_id BIGINT NOT NULL REFERENCES catalog_scrape_runs(id) ON DELETE CASCADE,
    tag_id BIGINT NOT NULL REFERENCES catalog_tags(id) ON DELETE CASCADE,
    page_number INTEGER NOT NULL CHECK (page_number >= 1),
    request_path TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ok' CHECK (status IN ('ok', 'error', 'empty')),
    card_count INTEGER NOT NULL DEFAULT 0 CHECK (card_count >= 0),
    error_text TEXT,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT catalog_scrape_pages_run_tag_page_key UNIQUE (run_id, tag_id, page_number)
);
CREATE INDEX IF NOT EXISTS catalog_scrape_pages_tag_fetched_idx ON catalog_scrape_pages (tag_id, fetched_at DESC);
