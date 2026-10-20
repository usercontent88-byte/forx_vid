from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup, Tag as Bs4Tag
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE_URL = "https://www.xvideos.com"
DEFAULT_TAG_FILE = Path("tag_list.json")
LOG = logging.getLogger("catalog_scraper")


@dataclass(frozen=True)
class Category:
    slug: str
    label: str
    path: str


@dataclass
class VideoCard:
    source: str
    external_id: str | None
    external_eid: str | None
    page_path: str
    title: str
    duration_text: str | None
    duration_seconds: int | None
    thumbnail_path: str | None
    mosaic_path: str | None
    preview_path: str | None
    tag_slug: str
    raw_source_data: dict[str, Any] = field(default_factory=dict)


def path_only(value: str | None, base_url: str = BASE_URL, *, keep_query: bool = False) -> str | None:
    """Normalize an absolute, protocol-relative, or relative URL to a path."""
    if not value:
        return None
    value = value.strip()
    if not value or value.startswith("data:") or value.startswith("javascript:"):
        return None
    absolute = urljoin(base_url.rstrip("/") + "/", value)
    parsed = urlsplit(absolute)
    path = parsed.path or "/"
    if keep_query and parsed.query:
        return f"{path}?{parsed.query}"
    return path


def normalized_request_url(value: str, base_url: str = BASE_URL) -> str:
    """Return an absolute same-site URL suitable for fetching and cycle detection."""
    absolute = urljoin(base_url.rstrip("/") + "/", value)
    parsed = urlsplit(absolute)
    base_host = (urlsplit(base_url).hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or (parsed.hostname or "").lower() != base_host:
        raise ValueError(f"Refusing to follow off-site pagination link: {value!r}")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))


def parse_duration(text: str | None) -> int | None:
    """Convert labels such as '25 min' or '01:23:45' to seconds."""
    if not text:
        return None
    text = " ".join(text.strip().lower().split())
    clock = re.fullmatch(r"(\d{1,3}):(\d{2})(?::(\d{2}))?", text)
    if clock:
        values = [int(x) for x in clock.groups() if x is not None]
        if len(values) == 2:
            return values[0] * 60 + values[1]
        return values[0] * 3600 + values[1] * 60 + values[2]

    seconds = 0
    for value, unit in re.findall(
        r"(\d+)\s*(hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)\b", text
    ):
        amount = int(value)
        if unit in {"h", "hr", "hrs", "hour", "hours"}:
            seconds += amount * 3600
        elif unit in {"m", "min", "mins", "minute", "minutes"}:
            seconds += amount * 60
        elif unit in {"s", "sec", "secs", "second", "seconds"}:
            seconds += amount
    return seconds or None


def _first_text(el: Bs4Tag | None) -> str | None:
    if el is None:
        return None
    text = " ".join(el.get_text(" ", strip=True).split())
    return text or None


def _find_video_anchor(card: Bs4Tag) -> Bs4Tag | None:
    return card.select_one("p.title a[href]") or card.select_one('a[href*="/video."]')


def _parse_card(card: Bs4Tag, category: Category, base_url: str) -> VideoCard | None:
    anchor = _find_video_anchor(card)
    if not anchor or not anchor.get("href"):
        return None

    page_path = path_only(anchor.get("href"), base_url)
    if not page_path or not page_path.startswith("/"):
        return None

    duration_node = card.select_one(".duration")
    duration_text = _first_text(duration_node)
    title = (anchor.get("title") or "").strip()
    if not title:
        # Work on a copy so extracting the duration cannot mutate source markup.
        anchor_copy = BeautifulSoup(str(anchor), "html.parser").find("a")
        duration_copy = anchor_copy.select_one(".duration") if anchor_copy else None
        if duration_copy:
            duration_copy.extract()
        title = _first_text(anchor_copy) or ""
    if not title:
        return None

    image = card.select_one(".thumb img") or card.select_one("img")
    thumbnail_url = None
    mosaic_url = None
    if image:
        thumbnail_url = image.get("src") or image.get("data-src") or image.get("data-sfwthumb")
        mosaic_url = image.get("data-mzl") or image.get("data-mosaic")
    thumbnail_path = path_only(thumbnail_url, base_url)
    mosaic_path = path_only(mosaic_url, base_url)

    # Derived path only; this does not issue a request for the preview media.
    preview_path = None
    if thumbnail_path and "/" in thumbnail_path:
        preview_path = thumbnail_path.rsplit("/", 1)[0] + "/preview.mp4"

    return VideoCard(
        source="xvideos",
        external_id=str(card.get("data-id")) if card.get("data-id") else None,
        external_eid=str(card.get("data-eid")) if card.get("data-eid") else None,
        page_path=page_path,
        title=title,
        duration_text=duration_text,
        duration_seconds=parse_duration(duration_text),
        thumbnail_path=thumbnail_path,
        mosaic_path=mosaic_path,
        preview_path=preview_path,
        tag_slug=category.slug,
        raw_source_data={
            "card_class": card.get("class", []),
            "data_id": card.get("data-id"),
            "data_eid": card.get("data-eid"),
            "thumbnail_data_idcdn": image.get("data-idcdn") if image else None,
        },
    )


def parse_listing_html(html: str, category: Category, base_url: str = BASE_URL) -> tuple[list[VideoCard], str | None]:
    """Parse listing cards and the next-page link from one listing page."""
    soup = BeautifulSoup(html, "html.parser")
    cards: list[VideoCard] = []
    seen_paths: set[str] = set()
    for node in soup.select(".frame-block.thumb-block, .thumb-block"):
        card = _parse_card(node, category, base_url)
        if card and card.page_path not in seen_paths:
            cards.append(card)
            seen_paths.add(card.page_path)

    next_anchor = soup.select_one(".pagination a.next-page[href], a[rel='next'][href]")
    next_url = None
    if next_anchor:
        href = (next_anchor.get("href") or "").strip()
        if href:
            next_url = normalized_request_url(href, base_url)
    return cards, next_url


def load_categories(file_path: Path) -> list[Category]:
    """Load categories from the repository's tag_list.json."""
    with file_path.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, list):
        raise ValueError("The tag list must be a JSON array")

    result: list[Category] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        label = str(item.get("tag") or "").strip()
        href = str(item.get("href") or "").strip()
        path = path_only(href, keep_query=True)
        parsed_path = urlsplit(path or "").path
        segments = [segment for segment in parsed_path.rstrip("/").split("/") if segment]
        slug = segments[-1] if segments and segments[-2:-1] == ["tags"] else ""
        if not slug:
            slug = parse_qs(urlsplit(path or "").query).get("k", [""])[0].strip()
        if not label or not slug or slug in seen:
            continue
        result.append(Category(slug=slug, label=label, path=path or f"/tags/{slug}"))
        seen.add(slug)
    return result


def build_session(user_agent: str) -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        connect=3,
        read=2,
        status=3,
        backoff_factor=1.2,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=2, pool_maxsize=2)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"User-Agent": user_agent, "Accept": "text/html,application/xhtml+xml"})
    return session


def fetch_html(session: requests.Session, url: str, timeout: float = 25) -> str:
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    return response.text


class DatabaseSink:
    """PostgreSQL sink; psycopg is imported only when database output is requested."""

    def __init__(self, database_url: str):
        try:
            import psycopg  # type: ignore
        except ImportError as exc:
            raise RuntimeError("Install dependencies with: pip install -r requirements.txt") from exc
        self.conn = psycopg.connect(database_url)
        self.conn.autocommit = False

    def close(self) -> None:
        self.conn.close()

    def start_run(self, config: dict[str, Any], expected_tags: int) -> int:
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO catalog_scrape_runs (status, expected_tags, config)
                   VALUES ('running', %s, %s::jsonb) RETURNING id""",
                (expected_tags, json.dumps(config)),
            )
            run_id = cur.fetchone()[0]
        self.conn.commit()
        return run_id

    def upsert_category(self, category: Category) -> int:
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO catalog_tags (source, slug, label, tag_path)
                   VALUES ('xvideos', %s, %s, %s)
                   ON CONFLICT (source, slug) DO UPDATE SET
                     label = EXCLUDED.label, tag_path = EXCLUDED.tag_path,
                     updated_at = now()
                   RETURNING id""",
                (category.slug, category.label, category.path),
            )
            tag_id = cur.fetchone()[0]
        self.conn.commit()
        return tag_id

    def write_page(
        self,
        run_id: int,
        tag_id: int,
        page_number: int,
        request_url: str,
        cards: list[VideoCard],
        status: str = "ok",
        error_text: str | None = None,
    ) -> None:
        request_path = path_only(request_url, keep_query=True) or "/"
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO catalog_scrape_pages
                     (run_id, tag_id, page_number, request_path, status, card_count, error_text)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (run_id, tag_id, page_number) DO UPDATE SET
                     request_path = EXCLUDED.request_path, status = EXCLUDED.status,
                     card_count = EXCLUDED.card_count, error_text = EXCLUDED.error_text,
                     fetched_at = now()""",
                (run_id, tag_id, page_number, request_path, status, len(cards), error_text),
            )
            for card in cards:
                cur.execute(
                    """INSERT INTO catalog_videos
                         (source, external_id, external_eid, page_path, title,
                          duration_text, duration_seconds, thumbnail_path, mosaic_path,
                          preview_path, raw_source_data)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                       ON CONFLICT (source, page_path) DO UPDATE SET
                         external_id = COALESCE(EXCLUDED.external_id, catalog_videos.external_id),
                         external_eid = COALESCE(EXCLUDED.external_eid, catalog_videos.external_eid),
                         title = EXCLUDED.title,
                         duration_text = COALESCE(EXCLUDED.duration_text, catalog_videos.duration_text),
                         duration_seconds = COALESCE(EXCLUDED.duration_seconds, catalog_videos.duration_seconds),
                         thumbnail_path = COALESCE(EXCLUDED.thumbnail_path, catalog_videos.thumbnail_path),
                         mosaic_path = COALESCE(EXCLUDED.mosaic_path, catalog_videos.mosaic_path),
                         preview_path = COALESCE(EXCLUDED.preview_path, catalog_videos.preview_path),
                         raw_source_data = catalog_videos.raw_source_data || EXCLUDED.raw_source_data,
                         last_seen_at = now(), updated_at = now()
                       RETURNING id""",
                    (
                        card.source, card.external_id, card.external_eid, card.page_path,
                        card.title, card.duration_text, card.duration_seconds,
                        card.thumbnail_path, card.mosaic_path, card.preview_path,
                        json.dumps(card.raw_source_data),
                    ),
                )
                video_id = cur.fetchone()[0]
                cur.execute(
                    """INSERT INTO catalog_video_tags (video_id, tag_id)
                       VALUES (%s, %s) ON CONFLICT DO NOTHING""",
                    (video_id, tag_id),
                )
        self.conn.commit()

    def finish_run(self, run_id: int, status: str, summary: dict[str, Any]) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """UPDATE catalog_scrape_runs SET status = %s, finished_at = now(),
                     tags_processed = %s, pages_visited = %s, cards_seen = %s,
                     errors = %s::jsonb, summary = %s::jsonb
                   WHERE id = %s""",
                (
                    status, summary.get("tags_processed", 0), summary.get("pages_visited", 0),
                    summary.get("cards_seen", 0), json.dumps(summary.get("errors", [])),
                    json.dumps(summary), run_id,
                ),
            )
        self.conn.commit()


def crawl_category(
    category: Category,
    *,
    session: requests.Session,
    max_pages: int = 40,
    delay_seconds: float = 1.5,
    base_url: str = BASE_URL,
    fetcher: Callable[[requests.Session, str], str] = fetch_html,
    sink: DatabaseSink | None = None,
    run_id: int | None = None,
    tag_id: int | None = None,
) -> tuple[list[VideoCard], dict[str, Any]]:
    if max_pages < 1:
        raise ValueError("max_pages must be at least 1")
    current_url = normalized_request_url(category.path, base_url)
    visited_urls: set[str] = set()
    output: list[VideoCard] = []
    end_reason = "max_pages_reached"
    page_count = 0
    errors: list[str] = []

    for page_number in range(1, max_pages + 1):
        if current_url in visited_urls:
            end_reason = "pagination_cycle_detected"
            break
        visited_urls.add(current_url)
        page_count += 1
        try:
            html = fetcher(session, current_url)
            cards, next_url = parse_listing_html(html, category, base_url)
            if sink and run_id is not None and tag_id is not None:
                sink.write_page(run_id, tag_id, page_number, current_url, cards)
        except Exception as exc:
            message = f"page {page_number} ({path_only(current_url, keep_query=True)}): {type(exc).__name__}: {exc}"
            errors.append(message)
            LOG.warning("%s: %s", category.slug, message)
            if sink and run_id is not None and tag_id is not None:
                sink.write_page(run_id, tag_id, page_number, current_url, [], status="error", error_text=message)
            end_reason = "request_or_parse_error"
            break

        if not cards:
            end_reason = "no_cards"
            break
        output.extend(cards)
        if not next_url:
            end_reason = "no_next_page_link"
            break
        if next_url in visited_urls:
            end_reason = "pagination_cycle_detected"
            break
        current_url = next_url
        if page_number < max_pages and delay_seconds > 0:
            time.sleep(delay_seconds)

    return output, {
        "tag": category.slug,
        "pages_visited": page_count,
        "cards_seen": len(output),
        "end_reason": end_reason,
        "errors": errors,
    }


def dump_jsonl(path: Path, records: Iterable[VideoCard]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
            count += 1
    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Extract listing-card metadata only; video and preview files are not downloaded."
    )
    parser.add_argument("--tags-file", type=Path, default=DEFAULT_TAG_FILE)
    parser.add_argument("--max-tags", type=int, default=1, help="Limit the run to this many tags (safe default: 1).")
    parser.add_argument("--all-tags", action="store_true", help="Process the entire tag list after a test run.")
    parser.add_argument("--max-pages", type=int, default=40, help="Maximum pages per tag (default: 40).")
    parser.add_argument("--delay", type=float, default=1.5, help="Seconds between page requests (default: 1.5).")
    parser.add_argument("--output-jsonl", type=Path, help="Optional JSONL export path.")
    parser.add_argument("--write-db", action="store_true", help="Persist to PostgreSQL using DATABASE_URL.")
    parser.add_argument("--base-url", default=BASE_URL, help=argparse.SUPPRESS)
    parser.add_argument("--user-agent", default="forx_vid-catalog-scraper/1.0")
    parser.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    args = parser.parse_args(argv)

    logging.basicConfig(level=getattr(logging, args.log_level), format="%(levelname)s: %(message)s")
    if args.max_pages < 1 or args.delay < 0 or args.max_tags < 1:
        parser.error("--max-pages and --max-tags must be >= 1; --delay must be >= 0")
    if args.write_db and not os.environ.get("DATABASE_URL"):
        parser.error("--write-db requires DATABASE_URL in the environment")

    categories = load_categories(args.tags_file)
    if not categories:
        parser.error(f"No categories found in {args.tags_file}")
    selected = categories if args.all_tags else categories[:args.max_tags]
    if not args.all_tags:
        LOG.info("Limited mode: %d tag(s), up to %d page(s) each.", len(selected), args.max_pages)
    else:
        LOG.warning("Full catalog mode: %d tags × up to %d pages each.", len(selected), args.max_pages)

    session = build_session(args.user_agent)
    sink: DatabaseSink | None = None
    run_id: int | None = None
    all_records: list[VideoCard] = []
    category_stats: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    try:
        if args.write_db:
            sink = DatabaseSink(os.environ["DATABASE_URL"])
            run_id = sink.start_run(
                {
                    "max_pages": args.max_pages, "delay_seconds": args.delay,
                    "all_tags": args.all_tags, "max_tags": args.max_tags,
                    "tags_file": str(args.tags_file),
                },
                len(selected),
            )

        for index, category in enumerate(selected, 1):
            LOG.info("[%d/%d] Tag %s (%s)", index, len(selected), category.label, category.path)
            tag_id = sink.upsert_category(category) if sink else None
            records, stats = crawl_category(
                category, session=session, max_pages=args.max_pages,
                delay_seconds=args.delay, base_url=args.base_url,
                sink=sink, run_id=run_id, tag_id=tag_id,
            )
            all_records.extend(records)
            category_stats.append(stats)
            errors.extend({"tag": category.slug, "message": msg} for msg in stats["errors"])
            LOG.info(
                "  %d page(s), %d card(s), stopped: %s",
                stats["pages_visited"], stats["cards_seen"], stats["end_reason"],
            )

        if args.output_jsonl:
            count = dump_jsonl(args.output_jsonl, all_records)
            LOG.info("Wrote %d row(s) to %s", count, args.output_jsonl)

        summary = {
            "tags_requested": len(selected),
            "tags_processed": len(category_stats),
            "pages_visited": sum(s["pages_visited"] for s in category_stats),
            "cards_seen": sum(s["cards_seen"] for s in category_stats),
            "unique_video_paths": len({record.page_path for record in all_records}),
            "errors": errors,
            "categories": category_stats,
        }
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        if sink and run_id is not None:
            sink.finish_run(run_id, "completed" if not errors else "partial", summary)
        return 1 if errors else 0
    finally:
        session.close()
        if sink:
            sink.close()


if __name__ == "__main__":
    sys.exit(main())
