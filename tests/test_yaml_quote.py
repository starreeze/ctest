import unittest

from common.utils import dump_yaml


class YamlQuoteTest(unittest.TestCase):
    def test_scientific_looking_short_id_is_quoted(self):
        dumped = dump_yaml({"proxies": [{"name": "n", "reality-opts": {"short-id": "8e45"}}]})
        self.assertIn("'short-id': '8e45'", dumped)


if __name__ == "__main__":
    unittest.main()
