import tempfile
import unittest
from pathlib import Path

from common.feeds import load_keep_hosts, parse_feed_list
from common.utils import rewrite_github_feed_url


class FeedListTest(unittest.TestCase):
    def test_parse_keep_prefix_and_comments(self):
        feeds = parse_feed_list(
            [
                "# https://example.com/disabled",
                "",
                "keep https://raw.githubusercontent.com/starreeze/ctest/main/assets/vultr-deploy.yaml",
                "KEEP: https://raw.githubusercontent.com/starreeze/ctest/main/assets/nodefiltrate.yaml",
                "https://raw.githubusercontent.com/anaer/Sub/main/clash.yaml",
            ]
        )
        self.assertEqual(
            feeds,
            [
                ("https://raw.githubusercontent.com/starreeze/ctest/main/assets/vultr-deploy.yaml", True),
                ("https://raw.githubusercontent.com/starreeze/ctest/main/assets/nodefiltrate.yaml", True),
                ("https://raw.githubusercontent.com/anaer/Sub/main/clash.yaml", False),
            ],
        )

    def test_keep_without_url_raises(self):
        with self.assertRaisesRegex(ValueError, "keep marker is missing"):
            parse_feed_list(["keep"])
        with self.assertRaisesRegex(ValueError, "keep marker is missing"):
            parse_feed_list(["keep:"])
        with self.assertRaisesRegex(ValueError, "keep marker is missing"):
            parse_feed_list(["keep # https://example.com"])

    def test_keep_hosts_load_from_local_github_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            assets = Path(tmp) / "assets"
            assets.mkdir()
            (assets / "pin.yaml").write_text(
                "proxies:\n  - {name: pin, server: 9.9.9.9, port: 443, type: ss}\n",
                encoding="utf-8",
            )
            urls = Path(tmp) / "urls.txt"
            urls.write_text(
                "keep https://raw.githubusercontent.com/starreeze/ctest/main/assets/pin.yaml\n"
                "https://raw.githubusercontent.com/anaer/Sub/main/clash.yaml\n",
                encoding="utf-8",
            )
            self.assertEqual(load_keep_hosts(str(urls)), {"9.9.9.9"})

    def test_repo_urls_txt_keep_hosts_and_github_rewrite(self):
        repo = Path(__file__).resolve().parents[1]
        urls_txt = repo / "urls.txt"
        hosts = load_keep_hosts(str(urls_txt))
        self.assertEqual(hosts, {"104.156.252.113"})
        with open(urls_txt, encoding="utf-8") as f:
            feeds = parse_feed_list(f)
        rewritten = [rewrite_github_feed_url(url) for url, _keep in feeds]
        self.assertTrue(any(url.endswith("assets/vultr-deploy.yaml") and keep for url, keep in feeds))
        for url, mirrored in zip((item[0] for item in feeds), rewritten):
            if "githubusercontent.com" in url or "github.com/" in url:
                self.assertTrue(mirrored.startswith("https://fastly.jsdelivr.net/gh/"), mirrored)
                self.assertNotIn("raw.githubusercontent.com", mirrored)


if __name__ == "__main__":
    unittest.main()
