"""Tests for provision_policy — the (secret-name x node) authorization layer (KAI-984 inc2).

Bounds BOTH dimensions fail-closed: only allowlisted secret NAMES, only tailnet-guard-approved
NODES. Composed on top of the Codex-verified tailnet_guard.
"""
import unittest

import provision_policy as pp

ALLOW = {"kai-mini": "nrZbQpqJCD11CNTRL"}


def good_status():
    return {"BackendState": "Running",
            "Peer": {"a": {"ID": "nrZbQpqJCD11CNTRL", "Online": True,
                           "TailscaleIPs": ["100.85.243.2"]}}}


class AllowPath(unittest.TestCase):
    def test_provisionable_secret_to_valid_node_allows(self):
        d = pp.authorize_provision("kai-mini", "todoist_api_key", ALLOW, good_status())
        self.assertTrue(d.allowed, d.reason)
        self.assertEqual(d.tailnet_ip, "100.85.243.2")
        self.assertEqual(d.secret_name, "todoist_api_key")

    def test_brief_secrets_provisionable(self):
        # slack_bot_token removed from the provisionable set — Slack retired (AR-5 / KAI-1127).
        for name in ["todoist_api_key", "anthropic_api_key"]:
            self.assertTrue(pp.authorize_provision("kai-mini", name, ALLOW, good_status()).allowed, name)


class SecretNameDenies(unittest.TestCase):
    def test_non_allowlisted_secret_denied(self):
        for name in ["kai_worker_auth", "legacy_bot_token_roads", "anthropic_api_key_sky",
                     "todoist", "random_secret"]:
            d = pp.authorize_provision("kai-mini", name, ALLOW, good_status())
            self.assertFalse(d.allowed, name)
            self.assertIn("provisionable", d.reason)

    def test_path_traversal_secret_name_denied(self):
        for name in ["../etc/passwd", "a/b", "todoist_api_key/../x", "todoist_api_key.txt",
                     "todoist api key", "todoist\nkey", ".."]:
            d = pp.authorize_provision("kai-mini", name, ALLOW, good_status())
            self.assertFalse(d.allowed, name)

    def test_empty_and_nonstring_secret_name_denied(self):
        for name in ["", None, 123, ["todoist_api_key"]]:
            self.assertFalse(pp.authorize_provision("kai-mini", name, ALLOW, good_status()).allowed)

    def test_overlong_secret_name_denied(self):
        self.assertFalse(pp.authorize_provision("kai-mini", "a" * 65, ALLOW, good_status()).allowed)


class NodeDenies(unittest.TestCase):
    def test_guard_denied_node_denied(self):
        # valid secret, but node not on the tailnet allowlist
        d = pp.authorize_provision("attacker-laptop", "todoist_api_key", ALLOW, good_status())
        self.assertFalse(d.allowed)
        self.assertIn("target denied", d.reason)

    def test_offline_node_denied(self):
        st = {"BackendState": "Running",
              "Peer": {"a": {"ID": "nrZbQpqJCD11CNTRL", "Online": False,
                             "TailscaleIPs": ["100.85.243.2"]}}}
        self.assertFalse(pp.authorize_provision("kai-mini", "todoist_api_key", ALLOW, st).allowed)

    def test_backend_down_denied(self):
        st = {"BackendState": "Stopped", "Peer": {}}
        self.assertFalse(pp.authorize_provision("kai-mini", "todoist_api_key", ALLOW, st).allowed)


class SubclassAndPropagation(unittest.TestCase):
    def test_hostile_str_subclass_secret_name_denied(self):
        # A str subclass could spoof equality/iteration to smuggle a bad value past validation.
        class EvilStr(str):
            def __eq__(self, other):
                return True   # claim to equal any allowlisted name
            def __hash__(self):
                return hash("todoist_api_key")
        evil = EvilStr("../etc/passwd")
        self.assertFalse(pp.authorize_provision("kai-mini", evil, ALLOW, good_status()).allowed)

    def test_str_subclass_of_allowlisted_value_denied(self):
        class S(str):
            pass
        self.assertFalse(pp.authorize_provision("kai-mini", S("todoist_api_key"),
                                                ALLOW, good_status()).allowed)

    def test_caller_cannot_widen_allowlist(self):
        # There is no allowlist parameter; only the module secrets are ever provisionable.
        self.assertEqual(pp.PROVISIONABLE_SECRETS,
                         frozenset({"todoist_api_key", "anthropic_api_key"}))
        self.assertFalse(pp.authorize_provision("kai-mini", "random_secret", ALLOW, good_status()).allowed)

    def test_allow_propagates_ip_and_node_id(self):
        d = pp.authorize_provision("kai-mini", "anthropic_api_key", ALLOW, good_status())
        self.assertTrue(d.allowed)
        self.assertEqual(d.tailnet_ip, "100.85.243.2")
        self.assertEqual(d.node_id, "nrZbQpqJCD11CNTRL")

    def test_deny_carries_no_ip(self):
        d = pp.authorize_provision("attacker-laptop", "todoist_api_key", ALLOW, good_status())
        self.assertFalse(d.allowed)
        self.assertIsNone(d.tailnet_ip)


class FailClosed(unittest.TestCase):
    def test_garbage_inputs_deny_no_crash(self):
        for node, name, al, st in [(None, None, None, None), ({}, {}, {}, {}),
                                   ("kai-mini", "todoist_api_key", None, None)]:
            self.assertFalse(pp.authorize_provision(node, name, al, st).allowed)

    def test_hostile_stringify_still_denies(self):
        class Evil:
            def __str__(self):
                raise RuntimeError("boom")
        self.assertFalse(pp.authorize_provision(Evil(), Evil(), ALLOW, good_status()).allowed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
