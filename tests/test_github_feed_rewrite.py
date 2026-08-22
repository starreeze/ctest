import unittest

from common.utils import rewrite_github_feed_url


class GithubFeedRewriteTest(unittest.TestCase):
    def test_raw_github_and_refs_heads(self):
        self.assertEqual(
            rewrite_github_feed_url(
                "https://raw.githubusercontent.com/Barabama/FreeNodes/main/nodes/clashmeta.yaml"
            ),
            "https://fastly.jsdelivr.net/gh/Barabama/FreeNodes@main/nodes/clashmeta.yaml",
        )
        self.assertEqual(
            rewrite_github_feed_url(
                "https://raw.githubusercontent.com/starreeze/ctest/refs/heads/main/assets/nodefiltrate.yaml"
            ),
            "https://fastly.jsdelivr.net/gh/starreeze/ctest@main/assets/nodefiltrate.yaml",
        )

    def test_jsdelivr_hosts_normalize_to_fastly(self):
        path = "gh/anaer/Sub@main/clash.yaml"
        self.assertEqual(
            rewrite_github_feed_url(f"https://cdn.jsdelivr.net/{path}"),
            f"https://fastly.jsdelivr.net/{path}",
        )
        self.assertEqual(
            rewrite_github_feed_url(f"https://gcore.jsdelivr.net/{path}"),
            f"https://fastly.jsdelivr.net/{path}",
        )
        self.assertEqual(
            rewrite_github_feed_url(f"https://fastly.jsdelivr.net/{path}"),
            f"https://fastly.jsdelivr.net/{path}",
        )

    def test_non_github_urls_unchanged(self):
        self.assertEqual(rewrite_github_feed_url("https://tt.vg/freeclash"), "https://tt.vg/freeclash")
        self.assertEqual(
            rewrite_github_feed_url("https://mm.mibei77.com/2026/08.22Clasholr.yaml"),
            "https://mm.mibei77.com/2026/08.22Clasholr.yaml",
        )


if __name__ == "__main__":
    unittest.main()
