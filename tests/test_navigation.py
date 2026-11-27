"""Mở trang Facebook chậm: thử lại, không phụ thuộc DOMContentLoaded, một nguồn lỗi không dừng cả lượt."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from fb_scraper.common.page_actions import open_page
from fb_scraper.web import worker
from tests.persistence_fixtures import PersistenceFixture


class OpenPageTests(unittest.IsolatedAsyncioTestCase):
    async def test_slow_first_navigation_is_retried_and_waits_for_commit_only(self):
        response = SimpleNamespace(status=200)
        page = SimpleNamespace(goto=AsyncMock(side_effect=[PlaywrightTimeoutError("slow"), response]),
                               wait_for_selector=AsyncMock())
        self.assertIs(await open_page(page, "https://www.facebook.com/x"), response)
        self.assertEqual(page.goto.await_count, 2)
        self.assertEqual(page.goto.await_args.kwargs["wait_until"], "commit")
        page.wait_for_selector.assert_awaited_once()

    async def test_missing_main_area_does_not_raise(self):
        page = SimpleNamespace(goto=AsyncMock(return_value=None),
                               wait_for_selector=AsyncMock(side_effect=PlaywrightTimeoutError("no main")))
        self.assertIsNone(await open_page(page, "https://www.facebook.com/x"))

    async def test_repeated_timeout_is_reported(self):
        page = SimpleNamespace(goto=AsyncMock(side_effect=PlaywrightTimeoutError("down")), wait_for_selector=AsyncMock())
        with self.assertRaises(PlaywrightTimeoutError):
            await open_page(page, "https://www.facebook.com/x")
        self.assertEqual(page.goto.await_count, 2)


class WorkerResilienceTests(PersistenceFixture, unittest.IsolatedAsyncioTestCase):
    async def test_failing_source_does_not_stop_the_remaining_sources(self):
        from fb_scraper.browser import connection
        from fb_scraper.entities import collector
        manager = AsyncMock()
        manager.__aenter__.return_value = object()
        plan = [{"kind": "profile", "value": "https://www.facebook.com/slow"},
                {"kind": "profile", "value": "https://www.facebook.com/ok"}]
        collect = AsyncMock(side_effect=[PlaywrightTimeoutError("Page.goto: Timeout 30000ms exceeded."), None])
        with patch.object(connection, "browser_context", return_value=manager), \
             patch.object(collector, "collect_entities", new=collect), \
             patch.object(worker, "watch_stop", return_value=None):
            result = await worker.collect({"plan": plan, "options": {"max_posts": 1}})
        self.assertEqual(result, 0)
        self.assertEqual(collect.await_count, 2)


if __name__ == "__main__":
    unittest.main()
