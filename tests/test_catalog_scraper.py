import json
from pathlib import Path
from types import SimpleNamespace

from scraper.catalog_scraper import (
    Category,
    crawl_category,
    load_categories,
    parse_duration,
    parse_listing_html,
    path_only,
    select_categories,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "listing_page.html"


def test_extracts_video_card_and_drops_domains():
    html = FIXTURE.read_text(encoding="utf-8")
    category = Category(slug="sample", label="Sample", path="/tags/sample")
    cards, next_url = parse_listing_html(html, category)

    assert len(cards) == 1
    card = cards[0]
    assert card.page_path == "/video.example123/sample_test_video"
    assert card.title == "Sample test video"
    assert card.duration_text == "25 min"
    assert card.duration_seconds == 1500
    assert card.thumbnail_path == "/abc/0/xv_16_t.jpg"
    assert card.mosaic_path == "/abc/0/mozaique_listing.jpg"
    assert card.preview_path == "/abc/0/preview.mp4"
    assert card.external_id == "12345"
    assert card.external_eid == "example-eid"
    assert next_url == "https://www.xvideos.com/tags/sample/2"
    assert all("example.test" not in (v or "") for v in (
        card.page_path, card.thumbnail_path, card.mosaic_path, card.preview_path
    ))


def test_parses_duration_formats():
    assert parse_duration("25 min") == 1500
    assert parse_duration("1h 2min 3s") == 3723
    assert parse_duration("01:23") == 83
    assert parse_duration("01:02:03") == 3723
    assert parse_duration("unknown") is None


def test_path_only_removes_host_and_query_by_default():
    assert path_only("https://cdn.example.test/a/b.jpg?x=1") == "/a/b.jpg"
    assert path_only("/video.id/title?x=1", keep_query=True) == "/video.id/title?x=1"


def test_empty_listing_is_terminal():
    category = Category(slug="empty", label="Empty", path="/tags/empty")
    cards, next_url = parse_listing_html("<html><body><div>No videos found</div></body></html>", category)
    assert cards == []
    assert next_url is None


def test_loads_repo_tag_list_shape(tmp_path):
    tags_file = tmp_path / "tags.json"
    tags_file.write_text(json.dumps([
        {"tag": "Sample Tag", "href": "/tags/sample-tag"},
        {"tag": "Sample Tag", "href": "/tags/sample-tag"},
        {"tag": "Missing Path"},
    ]), encoding="utf-8")
    tags = load_categories(tags_file)
    assert tags == [Category(slug="sample-tag", label="Sample Tag", path="/tags/sample-tag")]


def test_crawler_stops_when_next_page_has_no_cards():
    category = Category(slug="sample", label="Sample", path="/tags/sample")
    fixture_html = FIXTURE.read_text(encoding="utf-8")
    calls = []

    def fake_fetcher(_session, url):
        calls.append(url)
        if url.endswith("/tags/sample"):
            return fixture_html
        return "<html><body><div class='pagination'><a class='next-page' href='/tags/sample/3'>Next</a></div></body></html>"

    records, stats = crawl_category(
        category,
        session=SimpleNamespace(),
        max_pages=40,
        delay_seconds=0,
        fetcher=fake_fetcher,
    )
    assert len(records) == 1
    assert len(calls) == 2
    assert stats["pages_visited"] == 2
    assert stats["end_reason"] == "no_cards"


def test_crawler_respects_max_page_limit():
    category = Category(slug="sample", label="Sample", path="/tags/sample")
    fixture_html = FIXTURE.read_text(encoding="utf-8")
    calls = []

    def fake_fetcher(_session, url):
        calls.append(url)
        return fixture_html

    records, stats = crawl_category(
        category,
        session=SimpleNamespace(),
        max_pages=1,
        delay_seconds=0,
        fetcher=fake_fetcher,
    )
    assert len(records) == 1
    assert len(calls) == 1
    assert stats["end_reason"] == "max_pages_reached"


def test_loads_query_style_category_paths_without_losing_filter():
    tags_file = Path(__file__).resolve().parent / "fixtures" / "query_tags.json"
    tags = load_categories(tags_file)
    assert len(tags) == 1
    assert tags[0].slug == "18yearsold"
    assert tags[0].path == "/?k=18yearsold&p="



def test_select_categories_supports_batches_and_final_partial_batch():
    tags = [
        Category(slug=f"tag-{i}", label=f"Tag {i}", path=f"/tags/tag-{i}")
        for i in range(5)
    ]
    assert [x.slug for x in select_categories(tags, max_tags=2, tag_offset=0)] == ["tag-0", "tag-1"]
    assert [x.slug for x in select_categories(tags, max_tags=2, tag_offset=2)] == ["tag-2", "tag-3"]
    assert [x.slug for x in select_categories(tags, max_tags=2, tag_offset=4)] == ["tag-4"]
    assert [x.slug for x in select_categories(tags, tag_offset=3, all_tags=True)] == ["tag-3", "tag-4"]



def test_schema_migration_is_idempotent_and_includes_required_tables():
    from scraper.apply_schema import load_migration_sql

    sql = load_migration_sql().lower()
    assert "create table if not exists catalog_tags" in sql
    assert "create table if not exists catalog_videos" in sql
    assert "create table if not exists catalog_video_tags" in sql
    assert "create table if not exists catalog_scrape_runs" in sql
    assert "create table if not exists catalog_scrape_pages" in sql
    assert "drop table" not in sql
    assert "truncate table" not in sql
