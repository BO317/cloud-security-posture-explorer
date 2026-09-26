import unittest

from app.config import ConfigurationError, Settings


class ConfigurationTest(unittest.TestCase):
    def test_legacy_environment_cannot_silently_disable_ebs(self):
        with self.assertRaises(ConfigurationError):
            Settings.from_environment({"AWS_REGION": "us-east-1", "ALLOWED_BUCKETS": "demo-bucket",
                                       "ALLOWED_INSTANCES": "i-12345678"})

    def test_name_tag_validation_is_shared_and_literal(self):
        document = {"region": "us-east-1", "allowed_buckets": [], "allowed_security_groups": [],
                    "allowed_instance_name_tags": ["cloud-security-posture-explorer"]}
        self.assertEqual(Settings.from_document(document).instance_name_tags,
                         ("cloud-security-posture-explorer",))
        for name in ("", "*", "lab?", "lab\\*", "lab,other", "lab\nworker", "x" * 257, "lab[1]"):
            with self.subTest(name=name), self.assertRaises(ConfigurationError):
                Settings.from_document({**document, "allowed_instance_name_tags": [name]})
        for invalid in (None, "lab", [None], [False], [12]):
            with self.subTest(invalid=invalid), self.assertRaises(ConfigurationError):
                Settings.from_document({**document, "allowed_instance_name_tags": invalid})
        legacy = {**document, "allowed_instances": ["i-12345678"]}
        del legacy["allowed_instance_name_tags"]
        with self.assertRaises(ConfigurationError):
            Settings.from_document(legacy)

    def test_instance_only_scope_is_valid_and_deduplicated(self):
        settings = Settings.from_environment({"AWS_REGION": "us-east-1", "ALLOWED_INSTANCE_NAME_TAGS": " cloud-security-posture-explorer,cloud-security-posture-explorer,lab-worker "})
        self.assertEqual(settings.instance_name_tags, ("cloud-security-posture-explorer", "lab-worker"))
        self.assertEqual(settings.buckets, ())
        self.assertEqual(settings.security_groups, ())

    def test_invalid_instance_scope_rejected(self):
        for value in ("*", "lab?", "lab-worker,", "arn:aws:ec2:us-east-1:123456789012:instance/lab-worker",
                      ",".join(f"lab-{index}" for index in range(101))):
            with self.subTest(value=value), self.assertRaises(ConfigurationError):
                Settings.from_environment({"AWS_REGION": "us-east-1", "ALLOWED_INSTANCE_NAME_TAGS": value})

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
