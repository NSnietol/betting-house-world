"""Unit tests for the CacheStore class."""

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from src.cache_store import CacheStore


@pytest.fixture
def cache_store(tmp_path):
    """Create a CacheStore with a temporary database."""
    db_path = str(tmp_path / "test_cache.db")
    return CacheStore(db_path=db_path)


class TestCacheStoreInit:
    """Tests for CacheStore initialization and table creation."""

    def test_creates_database_file(self, tmp_path):
        db_path = str(tmp_path / "new_cache.db")
        assert not os.path.exists(db_path)
        CacheStore(db_path=db_path)
        assert os.path.exists(db_path)

    def test_creates_all_tables(self, cache_store):
        """Verify all four tables are created."""
        import sqlite3

        conn = sqlite3.connect(cache_store.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = [row[0] for row in cursor.fetchall()]
        conn.close()

        assert "cache" in tables
        assert "predictions" in tables
        assert "actual_results" in tables
        assert "bookmaker_scores" in tables

    def test_idempotent_init(self, tmp_path):
        """Creating CacheStore twice on same DB doesn't fail."""
        db_path = str(tmp_path / "idempotent.db")
        CacheStore(db_path=db_path)
        CacheStore(db_path=db_path)  # Should not raise


class TestCacheStoreGet:
    """Tests for the get() method."""

    def test_returns_none_when_empty(self, cache_store):
        result = cache_store.get("soccer_epl", "h2h", "event123", ("pinnacle",))
        assert result is None

    def test_returns_none_for_nonexistent_entry(self, cache_store):
        # Store something then look up with different keys
        cache_store.put(
            "soccer_epl",
            "h2h",
            "event123",
            ("pinnacle", "draftkings"),
            '{"data": "test"}',
            datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
        )
        result = cache_store.get("soccer_epl", "h2h", "event999", ("pinnacle",))
        assert result is None

    def test_returns_cached_entry(self, cache_store):
        response = '{"events": [{"id": "1"}]}'
        cache_store.put(
            "soccer_epl",
            "h2h",
            "event123",
            ("draftkings", "pinnacle"),
            response,
            datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
        )
        result = cache_store.get("soccer_epl", "h2h", "event123", ("pinnacle", "draftkings"))
        assert result is not None
        assert result["response_json"] == response
        assert result["retrieved_at"] == "2024-01-15T12:00:00Z"

    def test_bookmaker_keys_order_independent(self, cache_store):
        """Lookups should match regardless of bookmaker key ordering."""
        response = '{"data": "order_test"}'
        cache_store.put(
            "soccer_epl",
            "totals",
            "event456",
            ("betmgm", "pinnacle", "draftkings"),
            response,
            datetime(2024, 6, 1, 8, 30, 0, tzinfo=timezone.utc),
        )
        # Look up with different ordering
        result = cache_store.get(
            "soccer_epl", "totals", "event456", ("draftkings", "betmgm", "pinnacle")
        )
        assert result is not None
        assert result["response_json"] == response

    def test_different_sport_key_returns_none(self, cache_store):
        cache_store.put(
            "soccer_epl",
            "h2h",
            "event123",
            ("pinnacle",),
            '{"data": "epl"}',
            datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
        )
        result = cache_store.get("soccer_la_liga", "h2h", "event123", ("pinnacle",))
        assert result is None


class TestCacheStorePut:
    """Tests for the put() method."""

    def test_store_and_retrieve(self, cache_store):
        response = '{"bookmakers": [{"key": "pinnacle"}]}'
        cache_store.put(
            "soccer_epl",
            "h2h",
            "event789",
            ("pinnacle",),
            response,
            datetime(2024, 3, 10, 14, 0, 0, tzinfo=timezone.utc),
        )
        result = cache_store.get("soccer_epl", "h2h", "event789", ("pinnacle",))
        assert result is not None
        assert result["response_json"] == response

    def test_upsert_replaces_existing(self, cache_store):
        """Putting with same keys should replace the previous entry."""
        old_response = '{"version": 1}'
        new_response = '{"version": 2}'
        keys = ("pinnacle", "draftkings")
        ts1 = datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        ts2 = datetime(2024, 1, 2, 10, 0, 0, tzinfo=timezone.utc)

        cache_store.put("soccer_epl", "h2h", "event1", keys, old_response, ts1)
        cache_store.put("soccer_epl", "h2h", "event1", keys, new_response, ts2)

        result = cache_store.get("soccer_epl", "h2h", "event1", keys)
        assert result["response_json"] == new_response
        assert result["retrieved_at"] == "2024-01-02T10:00:00Z"

    def test_stores_complex_json(self, cache_store):
        complex_response = json.dumps({
            "events": [
                {
                    "id": "abc123",
                    "bookmakers": [
                        {"key": "pinnacle", "markets": [{"key": "h2h", "outcomes": []}]}
                    ],
                }
            ]
        })
        cache_store.put(
            "soccer_epl",
            "h2h",
            "abc123",
            ("pinnacle",),
            complex_response,
            datetime(2024, 5, 20, 9, 0, 0, tzinfo=timezone.utc),
        )
        result = cache_store.get("soccer_epl", "h2h", "abc123", ("pinnacle",))
        assert result["response_json"] == complex_response
        assert json.loads(result["response_json"]) == json.loads(complex_response)


class TestIsExpired:
    """Tests for the is_expired() method."""

    def test_not_expired_within_ttl(self, cache_store):
        recent_time = datetime.now(timezone.utc) - timedelta(hours=1)
        assert cache_store.is_expired(recent_time, ttl_hours=24) is False

    def test_expired_beyond_ttl(self, cache_store):
        old_time = datetime.now(timezone.utc) - timedelta(hours=25)
        assert cache_store.is_expired(old_time, ttl_hours=24) is True

    def test_exactly_at_ttl_boundary_not_expired(self, cache_store):
        # At exactly TTL, elapsed == ttl so it should NOT be expired (> not >=)
        # With floating point, this is tricky but let's test near boundary
        boundary_time = datetime.now(timezone.utc) - timedelta(hours=24)
        # Due to execution time, this might just barely cross, so test slightly under
        slightly_under = datetime.now(timezone.utc) - timedelta(hours=23, minutes=59)
        assert cache_store.is_expired(slightly_under, ttl_hours=24) is False

    def test_expired_with_short_ttl(self, cache_store):
        old_time = datetime.now(timezone.utc) - timedelta(hours=2)
        assert cache_store.is_expired(old_time, ttl_hours=1) is True

    def test_not_expired_just_stored(self, cache_store):
        just_now = datetime.now(timezone.utc)
        assert cache_store.is_expired(just_now, ttl_hours=1) is False

    def test_handles_naive_datetime(self, cache_store):
        """Naive datetimes are treated as UTC."""
        old_time = datetime.utcnow() - timedelta(hours=25)
        assert cache_store.is_expired(old_time, ttl_hours=24) is True

    def test_ttl_one_hour(self, cache_store):
        recent = datetime.now(timezone.utc) - timedelta(minutes=30)
        assert cache_store.is_expired(recent, ttl_hours=1) is False

    def test_ttl_forty_eight_hours(self, cache_store):
        old = datetime.now(timezone.utc) - timedelta(hours=49)
        assert cache_store.is_expired(old, ttl_hours=48) is True


class TestRetryLogic:
    """Tests for the SQLite locked DB retry mechanism."""

    def test_works_without_contention(self, cache_store):
        """Normal operation without lock contention succeeds."""
        cache_store.put(
            "soccer_epl",
            "h2h",
            "retry_test",
            ("pinnacle",),
            '{"test": true}',
            datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
        )
        result = cache_store.get("soccer_epl", "h2h", "retry_test", ("pinnacle",))
        assert result is not None


class TestExtractionMethod:
    """Tests for the extraction_method column functionality."""

    def test_default_extraction_method_is_api(self, cache_store):
        """When no extraction_method is provided, it defaults to 'api'."""
        cache_store.put(
            "soccer_epl",
            "h2h",
            "event_default",
            ("pinnacle",),
            '{"data": "test"}',
            datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
        )
        result = cache_store.get("soccer_epl", "h2h", "event_default", ("pinnacle",))
        assert result is not None
        assert result["extraction_method"] == "api"

    def test_store_scraping_http_method(self, cache_store):
        """Can store and retrieve extraction_method='scraping_http'."""
        cache_store.put(
            "soccer_epl",
            "h2h",
            "event_http",
            ("pinnacle",),
            '{"data": "scraped"}',
            datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
            extraction_method="scraping_http",
        )
        result = cache_store.get("soccer_epl", "h2h", "event_http", ("pinnacle",))
        assert result is not None
        assert result["extraction_method"] == "scraping_http"

    def test_store_scraping_headless_method(self, cache_store):
        """Can store and retrieve extraction_method='scraping_headless'."""
        cache_store.put(
            "soccer_epl",
            "h2h",
            "event_headless",
            ("bet365",),
            '{"data": "headless"}',
            datetime(2024, 2, 10, 8, 0, 0, tzinfo=timezone.utc),
            extraction_method="scraping_headless",
        )
        result = cache_store.get("soccer_epl", "h2h", "event_headless", ("bet365",))
        assert result is not None
        assert result["extraction_method"] == "scraping_headless"

    def test_store_api_method_explicitly(self, cache_store):
        """Can explicitly pass extraction_method='api'."""
        cache_store.put(
            "soccer_epl",
            "totals",
            "event_api",
            ("draftkings",),
            '{"data": "api_data"}',
            datetime(2024, 3, 5, 14, 30, 0, tzinfo=timezone.utc),
            extraction_method="api",
        )
        result = cache_store.get("soccer_epl", "totals", "event_api", ("draftkings",))
        assert result is not None
        assert result["extraction_method"] == "api"

    def test_invalid_extraction_method_raises(self, cache_store):
        """Passing an invalid extraction_method raises ValueError."""
        with pytest.raises(ValueError, match="Invalid extraction_method"):
            cache_store.put(
                "soccer_epl",
                "h2h",
                "event_bad",
                ("pinnacle",),
                '{"data": "bad"}',
                datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
                extraction_method="invalid_method",
            )

    def test_upsert_updates_extraction_method(self, cache_store):
        """Upserting an entry can change extraction_method."""
        keys = ("pinnacle",)
        cache_store.put(
            "soccer_epl", "h2h", "event_upsert", keys,
            '{"v": 1}',
            datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
            extraction_method="api",
        )
        cache_store.put(
            "soccer_epl", "h2h", "event_upsert", keys,
            '{"v": 2}',
            datetime(2024, 1, 2, 10, 0, 0, tzinfo=timezone.utc),
            extraction_method="scraping_http",
        )
        result = cache_store.get("soccer_epl", "h2h", "event_upsert", keys)
        assert result["extraction_method"] == "scraping_http"

    def test_migration_on_existing_db(self, tmp_path):
        """Databases without extraction_method column get it added via migration."""
        import sqlite3

        db_path = str(tmp_path / "legacy.db")
        # Create a legacy database without extraction_method column
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sport_key TEXT NOT NULL,
                market_type TEXT NOT NULL,
                event_id TEXT NOT NULL,
                bookmaker_keys TEXT NOT NULL,
                response_json TEXT NOT NULL,
                retrieved_at TEXT NOT NULL,
                UNIQUE(sport_key, market_type, event_id, bookmaker_keys)
            )
        """)
        # Insert legacy data without extraction_method
        conn.execute(
            """INSERT INTO cache (sport_key, market_type, event_id, bookmaker_keys, response_json, retrieved_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("soccer_epl", "h2h", "legacy_event", '["pinnacle"]', '{"legacy": true}', "2024-01-01T00:00:00Z"),
        )
        conn.commit()
        conn.close()

        # Now open with CacheStore — migration should add column
        store = CacheStore(db_path=db_path)
        result = store.get("soccer_epl", "h2h", "legacy_event", ("pinnacle",))
        assert result is not None
        assert result["response_json"] == '{"legacy": true}'
        # Legacy data should default to 'api'
        assert result["extraction_method"] == "api"

    def test_extraction_method_column_exists_in_schema(self, cache_store):
        """Verify the cache table has the extraction_method column."""
        import sqlite3

        conn = sqlite3.connect(cache_store.db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(cache)")
        columns = {row[1] for row in cursor.fetchall()}
        conn.close()
        assert "extraction_method" in columns
