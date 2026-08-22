import base64
import unittest

from common.utils import mihomo_accepts_vless_encryption


class VlessEncryptionTest(unittest.TestCase):
    def test_none_and_empty_are_accepted(self):
        self.assertTrue(mihomo_accepts_vless_encryption(""))
        self.assertTrue(mihomo_accepts_vless_encryption("none"))

    def test_x25519_password_length_is_accepted(self):
        key = base64.urlsafe_b64encode(b"k" * 32).decode().rstrip("=")
        self.assertTrue(mihomo_accepts_vless_encryption(f"mlkem768x25519plus.native.0rtt.{key}"))

    def test_wrong_decoded_length_is_rejected(self):
        self.assertFalse(
            mihomo_accepts_vless_encryption(
                "mlkem768x25519plus.native.0rtt."
                "NZBih2jIVFR24bMMX7i-Q9XE3oESSooOHSeDvnjP3fc0EaqCVMWndYUo6mUakYN2ZaYHVZofOZsanhV2ZNoFVOcvfKo7RybJVza0Y6ZT1ogxe1G72XUauZxaUwQ3m3YrapuNA8VPQ"
            )
        )
        self.assertFalse(mihomo_accepts_vless_encryption("auto"))


if __name__ == "__main__":
    unittest.main()
