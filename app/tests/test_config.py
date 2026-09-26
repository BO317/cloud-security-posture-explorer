import unittest

from app.config import ConfigurationError, Settings


class ConfigurationTest(unittest.TestCase):
    def test_explicit_scope_and_deduplication(self):
        settings = Settings.from_environment({"AWS_REGION": "us-east-1", "ALLOWED_BUCKETS": " demo-bucket, demo-bucket ", "ALLOWED_SECURITY_GROUPS": "sg-12345678"})
        self.assertEqual(settings.region, "us-east-1")
        self.assertEqual(settings.buckets, ("demo-bucket",))
        self.assertEqual(settings.security_groups, ("sg-12345678",))

    def test_single_service_and_region_fallback(self):
        settings = Settings.from_environment({"AWS_DEFAULT_REGION": "us-west-2", "ALLOWED_SECURITY_GROUPS": "sg-0123456789abcdef0"})
        self.assertEqual(settings.buckets, ())
        self.assertEqual(settings.region, "us-west-2")

    def test_empty_scope_never_means_scan_all(self):
        for env in ({}, {"AWS_REGION": "us-east-1"}, {"AWS_REGION": "us-east-1", "ALLOWED_BUCKETS": " "}):
            with self.assertRaises(ConfigurationError):
                Settings.from_environment(env)

    def test_invalid_scope_and_region_fail_closed(self):
        for key, value in (("AWS_REGION", "https://untrusted.example"), ("ALLOWED_BUCKETS", "*"), ("ALLOWED_BUCKETS", "demo-bucket,"), ("ALLOWED_BUCKETS", "arn:aws:s3:::demo-bucket"), ("ALLOWED_SECURITY_GROUPS", "sg-invalid")):
            with self.subTest(key=key, value=value), self.assertRaises(ConfigurationError):
                Settings.from_environment({"AWS_REGION": "us-east-1", "ALLOWED_BUCKETS": "demo-bucket", key: value})

    def test_scope_limit(self):
        with self.assertRaises(ConfigurationError):
            Settings.from_environment({"AWS_REGION": "us-east-1", "ALLOWED_BUCKETS": ",".join(f"demo-{i}" for i in range(101))})
