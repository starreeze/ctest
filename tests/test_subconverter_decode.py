import unittest

from common.utils import decode_subconverter_body


class SubconverterDecodeTest(unittest.TestCase):
    def test_valid_utf8_unchanged(self):
        text, replaced = decode_subconverter_body("proxies:\n- name: 节点\n".encode())
        self.assertEqual(text, "proxies:\n- name: 节点\n")
        self.assertEqual(replaced, 0)

    def test_invalid_utf8_replaced(self):
        text, replaced = decode_subconverter_body(b"obfs-param: d300c%%\xdc, rest")
        self.assertGreater(replaced, 0)
        self.assertIn("\ufffd", text)
        self.assertTrue(text.startswith("obfs-param:"))


if __name__ == "__main__":
    unittest.main()
