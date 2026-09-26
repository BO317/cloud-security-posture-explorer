import unittest

from app.checks import BPA_FLAGS, check


def observation(data, **overrides):
    return {"observed_at": "2026-09-25T12:00:00Z", "data": data, **overrides}


def rule(protocol="tcp", start=22, end=22, cidrs=None):
    return {"protocol": protocol, "from_port": start, "to_port": end, "cidrs": ["0.0.0.0/0"] if cidrs is None else cidrs}


class ChecksTest(unittest.TestCase):
    def test_all_bucket_flags_required(self):
        enabled = dict.fromkeys(BPA_FLAGS, True)
        self.assertEqual(check("s3", observation(enabled)).status, "PASS")
        for flag in BPA_FLAGS:
            with self.subTest(flag=flag):
                self.assertEqual(check("s3", observation({**enabled, flag: False})).status, "REVIEW")
                for invalid in (None, 1, "true"):
                    self.assertEqual(check("s3", observation({**enabled, flag: invalid})).status, "UNKNOWN")
                incomplete = enabled.copy()
                del incomplete[flag]
                self.assertEqual(check("s3", observation(incomplete)).status, "UNKNOWN")

    def test_api_errors_override_apparently_good_data(self):
        for kind, data in (("s3", dict.fromkeys(BPA_FLAGS, True)), ("security_group", [])):
            for error in ("AccessDenied", "Timeout", ""):
                result = check(kind, observation(data, error=error))
                self.assertEqual(result.status, "UNKNOWN")
                self.assertEqual(result.observed_at, "2026-09-25T12:00:00Z")
                self.assertTrue(result.reason)

    def test_missing_evidence_and_timestamp(self):
        for kind in ("s3", "security_group"):
            for value in (None, {}, [], observation(None)):
                self.assertEqual(check(kind, value).status, "UNKNOWN")
            for timestamp in (None, "invalid", "2026-09-25T12:00:00", 123):
                result = check(kind, observation([], observed_at=timestamp))
                self.assertEqual(result.status, "UNKNOWN")
                self.assertIsNone(result.observed_at)

    def test_worldwide_management_rules(self):
        for cidr in ("0.0.0.0/0", "::/0"):
            for protocol, start, end in (("tcp", 22, 22), ("tcp", 3300, 3400), ("udp", 3389, 3389), ("all", None, None)):
                with self.subTest(cidr=cidr, protocol=protocol):
                    self.assertEqual(check("security_group", observation([rule(protocol, start, end, [cidr])])).status, "REVIEW")

    def test_narrow_pass_scope(self):
        for rules in ([], [rule(cidrs=["192.0.2.0/24"])], [rule(start=443, end=443)], [rule(protocol="icmp")], [rule(cidrs=["2001:db8::/32"])]):
            self.assertEqual(check("security_group", observation(rules)).status, "PASS")

    def test_malformed_rule_never_passes(self):
        invalid_rules = [None, {}, rule(protocol="other"), rule(start=None), rule(start=True), rule(start=-1), rule(end=65536), rule(start=23, end=22), rule(cidrs=[]), rule(cidrs=["bad"]), rule(cidrs=[123]), rule(cidrs=["0.0.0.1/0"])]
        for invalid in invalid_rules:
            with self.subTest(rule=invalid):
                self.assertEqual(check("security_group", observation([invalid])).status, "UNKNOWN")
                self.assertEqual(check("security_group", observation([rule(), invalid])).status, "UNKNOWN")

    def test_unknown_kind(self):
        self.assertEqual(check("unsupported", observation({})).status, "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
