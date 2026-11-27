"""Chuẩn hóa định danh tác giả cho thống kê tác giả & nơi đăng."""
import unittest

from fb_scraper.analysis.actors import author_key

FB = "https://www.facebook.com/"


class AuthorKeyTests(unittest.TestCase):
    def test_tracking_parameters_are_dropped(self):
        first = author_key(FB + "hainamleadinvest?__cft__[0]=AZj&__tn__=-]C%2CP-R")
        second = author_key(FB + "hainamleadinvest/?__cft__[0]=XYZ")
        self.assertEqual(first, FB + "hainamleadinvest")
        self.assertEqual(first, second)

    def test_profile_and_group_member_links_share_one_key(self):
        expected = FB + "profile.php?id=100077296412056"
        self.assertEqual(author_key(FB + "profile.php?id=100077296412056&__cft__[0]=A"), expected)
        self.assertEqual(author_key(FB + "groups/1110187625238585/user/100077296412056/"), expected)
        self.assertEqual(author_key(FB + "people/Ten-Nguoi/100077296412056/"), expected)

    def test_fallback_to_name_and_skip_anonymous(self):
        self.assertEqual(author_key("", "Tuyển Dụng Online"), "name:tuyển dụng online")
        self.assertIsNone(author_key(FB + "groups/151080472178386/", "Người tham gia ẩn danh"))
        self.assertIsNone(author_key(FB + "groups/151080472178386/", ""))


if __name__ == "__main__":
    unittest.main()
