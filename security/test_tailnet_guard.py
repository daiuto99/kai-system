"""Deny-path + allow-path tests for tailnet_guard (KAI-984 security core).

The whole value of the guard is that it FAILS CLOSED. Every DenyPaths test is a way the
target could fail to be a legitimate enrolled, online, single-tailnet-IP KAI node; all must
DENY. Hardened 2026-07-27 after Codex round-1 (strict Online, node-id format, dup-ID,
strict IP schema, ambiguity, enrollment_status gate).
"""
import json
import os
import tempfile
import unittest

import tailnet_guard as tg

ALLOW = {
    "kai-worker": "nzkpgsJk1M11CNTRL",
    "kai-mini": "nrZbQpqJCD11CNTRL",
    "mac-mini": "nwUpbTFAdP11CNTRL",
}


def status(self_entry=None, peers=None, backend="Running"):
    s = {}
    if backend is not None:
        s["BackendState"] = backend
    if self_entry is not None:
        s["Self"] = self_entry
    s["Peer"] = peers or {}
    return s


def node(node_id, ips, online=True, host="host"):
    e = {"ID": node_id, "TailscaleIPs": ips, "HostName": host}
    if online is not None:
        e["Online"] = online
    return e


class AllowPath(unittest.TestCase):
    def test_enrolled_online_peer_single_cgnat_allows(self):
        st = status(peers={"a": node("nrZbQpqJCD11CNTRL", ["100.85.243.2", "fd7a:115c::1"],
                                     online=True, host="Leo's Mac mini (2)")})
        d = tg.evaluate_target("kai-mini", ALLOW, st)
        self.assertTrue(d.allowed, d.reason)
        self.assertEqual(d.tailnet_ip, "100.85.243.2")

    def test_self_without_online_field_allows(self):
        # Self commonly omits Online; that is treated as online (it is the local caller).
        st = status(self_entry=node("nzkpgsJk1M11CNTRL", ["100.78.94.80"], online=None))
        self.assertTrue(tg.evaluate_target("kai-worker", ALLOW, st).allowed)


class DenyPaths(unittest.TestCase):
    def test_name_not_on_allowlist(self):
        st = status(peers={"a": node("nSomeValidID123", ["100.1.2.3"])})
        self.assertFalse(tg.evaluate_target("attacker-laptop", ALLOW, st).allowed)

    def test_enrolled_id_absent(self):
        st = status(self_entry=node("nzkpgsJk1M11CNTRL", ["100.78.94.80"]))
        self.assertFalse(tg.evaluate_target("kai-mini", ALLOW, st).allowed)

    def test_hostname_spoof_wrong_id_denied(self):
        st = status(peers={"evil": node("nEVILimposter00", ["100.85.243.2"], online=True,
                                        host="Leo's Mac mini (2)")})
        d = tg.evaluate_target("kai-mini", ALLOW, st)
        self.assertFalse(d.allowed)
        self.assertIn("not present", d.reason)

    def test_online_string_false_denied(self):
        st = status(peers={"a": node("nrZbQpqJCD11CNTRL", ["100.85.243.2"], online="false")})
        self.assertFalse(tg.evaluate_target("kai-mini", ALLOW, st).allowed)

    def test_online_int_one_denied(self):
        st = status(peers={"a": node("nrZbQpqJCD11CNTRL", ["100.85.243.2"], online=1)})
        self.assertFalse(tg.evaluate_target("kai-mini", ALLOW, st).allowed)

    def test_online_false_denied(self):
        st = status(peers={"a": node("nrZbQpqJCD11CNTRL", ["100.85.243.2"], online=False)})
        self.assertFalse(tg.evaluate_target("kai-mini", ALLOW, st).allowed)

    def test_self_explicit_offline_denied(self):
        st = status(self_entry=node("nzkpgsJk1M11CNTRL", ["100.78.94.80"], online=False))
        self.assertFalse(tg.evaluate_target("kai-worker", ALLOW, st).allowed)

    def test_self_explicit_null_online_denied(self):
        # Online PRESENT-but-null is an explicit non-True and must deny, even for Self
        # (distinct from a genuinely absent Online key, which Self is allowed to omit).
        st = status(self_entry={"ID": "nzkpgsJk1M11CNTRL", "Online": None,
                                "TailscaleIPs": ["100.78.94.80"]})
        self.assertFalse(tg.evaluate_target("kai-worker", ALLOW, st).allowed)

    def test_peer_absent_online_key_denied(self):
        # An absent Online key never helps a PEER (only Self).
        st = status(peers={"a": {"ID": "nrZbQpqJCD11CNTRL", "TailscaleIPs": ["100.85.243.2"]}})
        self.assertFalse(tg.evaluate_target("kai-mini", ALLOW, st).allowed)

    def test_duplicate_id_in_status_denied(self):
        st = status(peers={"a": node("nrZbQpqJCD11CNTRL", ["100.85.243.2"], online=True),
                           "b": node("nrZbQpqJCD11CNTRL", ["100.106.160.42"], online=True)})
        d = tg.evaluate_target("kai-mini", ALLOW, st)
        self.assertFalse(d.allowed)
        self.assertIn("ambiguous", d.reason)

    def test_no_cgnat_ip_denied(self):
        st = status(peers={"a": node("nrZbQpqJCD11CNTRL", ["8.8.8.8", "fd7a:115c::9"])})
        self.assertFalse(tg.evaluate_target("kai-mini", ALLOW, st).allowed)

    def test_ip_outside_cgnat_denied(self):
        for ip in ["100.32.0.1", "100.63.255.255", "100.128.0.1", "101.0.0.1", "10.0.0.5"]:
            st = status(peers={"a": node("nrZbQpqJCD11CNTRL", [ip])})
            self.assertFalse(tg.evaluate_target("kai-mini", ALLOW, st).allowed, ip)

    def test_cgnat_boundaries_allow(self):
        for ip in ["100.64.0.0", "100.127.255.255"]:
            st = status(peers={"a": node("nrZbQpqJCD11CNTRL", [ip], online=True)})
            self.assertTrue(tg.evaluate_target("kai-mini", ALLOW, st).allowed, ip)

    def test_multiple_cgnat_ambiguous_denied(self):
        st = status(peers={"a": node("nrZbQpqJCD11CNTRL", ["100.64.0.1", "100.64.0.2"], online=True)})
        d = tg.evaluate_target("kai-mini", ALLOW, st)
        self.assertFalse(d.allowed)
        self.assertIn("ambiguous", d.reason)

    def test_tailscaleips_dict_denied(self):
        st = status(peers={"a": {"ID": "nrZbQpqJCD11CNTRL", "Online": True,
                                 "TailscaleIPs": {"0": "100.85.243.2"}}})
        self.assertFalse(tg.evaluate_target("kai-mini", ALLOW, st).allowed)

    def test_tailscaleips_int_denied(self):
        st = status(peers={"a": {"ID": "nrZbQpqJCD11CNTRL", "Online": True, "TailscaleIPs": 5}})
        self.assertFalse(tg.evaluate_target("kai-mini", ALLOW, st).allowed)

    def test_nonstring_ip_element_denied(self):
        st = status(peers={"a": {"ID": "nrZbQpqJCD11CNTRL", "Online": True,
                                 "TailscaleIPs": [123, "100.85.243.2"]}})
        self.assertFalse(tg.evaluate_target("kai-mini", ALLOW, st).allowed)

    def test_malformed_ip_element_denies_whole_decision(self):
        st = status(peers={"a": {"ID": "nrZbQpqJCD11CNTRL", "Online": True,
                                 "TailscaleIPs": ["not-an-ip", "100.85.243.2"]}})
        self.assertFalse(tg.evaluate_target("kai-mini", ALLOW, st).allowed)

    def test_invalid_node_id_format_in_allowlist_denied(self):
        bad_allow = {"kai-mini": "not a node id!"}
        st = status(peers={"a": node("not a node id!", ["100.85.243.2"], online=True)})
        self.assertFalse(tg.evaluate_target("kai-mini", bad_allow, st).allowed)

    def test_terminal_newline_node_id_denied(self):
        # `$` in a regex matches before a terminal \n; validation must reject "nABCDEF\n".
        self.assertFalse(tg._valid_node_id("nABCDEF\n"))
        bad_allow = {"attacker": "nABCDEF\n"}
        st = status(peers={"x": node("nABCDEF\n", ["100.64.0.1"], online=True)})
        self.assertFalse(tg.evaluate_target("attacker", bad_allow, st).allowed)

    def test_matched_entry_missing_id_denied(self):
        st = status(peers={"a": {"Online": True, "TailscaleIPs": ["100.85.243.2"]}})  # no ID
        self.assertFalse(tg.evaluate_target("kai-mini", ALLOW, st).allowed)

    def test_missing_tailscaleips_field_denied(self):
        st = status(peers={"a": {"ID": "nrZbQpqJCD11CNTRL", "Online": True}})  # no TailscaleIPs
        self.assertFalse(tg.evaluate_target("kai-mini", ALLOW, st).allowed)

    def test_self_and_peer_duplicate_id_ambiguous_denied(self):
        st = status(self_entry=node("nrZbQpqJCD11CNTRL", ["100.85.243.2"], online=None),
                    peers={"a": node("nrZbQpqJCD11CNTRL", ["100.106.160.42"], online=True)})
        d = tg.evaluate_target("kai-mini", ALLOW, st)
        self.assertFalse(d.allowed)
        self.assertIn("ambiguous", d.reason)

    def test_malformed_status_denies_no_crash(self):
        for bad in [{}, {"Self": None, "Peer": None}, {"Peer": "nope"}, {"Self": 5}]:
            self.assertFalse(tg.evaluate_target("kai-mini", ALLOW, bad).allowed)

    def test_empty_allowlist_denies(self):
        st = status(peers={"a": node("nrZbQpqJCD11CNTRL", ["100.85.243.2"])})
        self.assertFalse(tg.evaluate_target("kai-mini", {}, st).allowed)

    def test_bad_name_and_status_types_deny(self):
        st = status(peers={"a": node("nrZbQpqJCD11CNTRL", ["100.85.243.2"])})
        for bad in [None, "", 123]:
            self.assertFalse(tg.evaluate_target(bad, ALLOW, st).allowed)
        for bad in [None, [], "status", 42]:
            self.assertFalse(tg.evaluate_target("kai-mini", ALLOW, bad).allowed)

    def test_hostile_name_str_raises_still_denies(self):
        class Evil:
            def __str__(self):
                raise RuntimeError("boom")
        self.assertFalse(tg.evaluate_target(Evil(), ALLOW, status()).allowed)

    def test_backend_not_running_denied(self):
        for backend in ["Stopped", "NeedsLogin", None, "running"]:
            st = status(peers={"a": node("nrZbQpqJCD11CNTRL", ["100.85.243.2"], online=True)},
                        backend=backend)
            d = tg.evaluate_target("kai-mini", ALLOW, st)
            self.assertFalse(d.allowed, backend)

    def test_self_omitted_online_with_running_backend_allows(self):
        st = status(self_entry=node("nzkpgsJk1M11CNTRL", ["100.78.94.80"], online=None))
        self.assertTrue(tg.evaluate_target("kai-worker", ALLOW, st).allowed)

    def test_non_dict_self_denied(self):
        st = status(peers={"a": node("nrZbQpqJCD11CNTRL", ["100.85.243.2"], online=True)})
        st["Self"] = "not-an-object"
        self.assertFalse(tg.evaluate_target("kai-mini", ALLOW, st).allowed)

    def test_non_dict_peer_container_denied(self):
        st = {"BackendState": "Running", "Peer": "not-an-object"}
        self.assertFalse(tg.evaluate_target("kai-mini", ALLOW, st).allowed)

    def test_non_dict_peer_value_denied(self):
        st = {"BackendState": "Running",
              "Peer": {"good": node("nrZbQpqJCD11CNTRL", ["100.85.243.2"], online=True),
                       "garbage": "not-an-object"}}
        self.assertFalse(tg.evaluate_target("kai-mini", ALLOW, st).allowed)

    def test_dirty_allowlist_duplicate_ids_denied_inline(self):
        dirty = {"alias1": "nrZbQpqJCD11CNTRL", "alias2": "nrZbQpqJCD11CNTRL"}
        st = status(peers={"a": node("nrZbQpqJCD11CNTRL", ["100.85.243.2"], online=True)})
        self.assertFalse(tg.evaluate_target("alias1", dirty, st).allowed)

    def test_dirty_allowlist_invalid_sibling_id_denies_valid_name(self):
        dirty = {"kai-mini": "nrZbQpqJCD11CNTRL", "poison": "42"}
        st = status(peers={"a": node("nrZbQpqJCD11CNTRL", ["100.85.243.2"], online=True)})
        self.assertFalse(tg.evaluate_target("kai-mini", dirty, st).allowed)


class LoadAllowlist(unittest.TestCase):
    def _write(self, obj):
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        open(path, "w").write(json.dumps(obj) if not isinstance(obj, str) else obj)
        self.addCleanup(os.unlink, path)
        return path

    def test_bad_path_returns_empty(self):
        self.assertEqual(tg.load_allowlist("/nonexistent/allowlist.json"), {})

    def test_unconfirmed_enrollment_returns_empty(self):
        p = self._write({"enrollment_status": "seeded_pending_leo_confirmation",
                         "nodes": {"kai-mini": "nrZbQpqJCD11CNTRL"}})
        self.assertEqual(tg.load_allowlist(p), {})

    def test_confirmed_enrollment_loads(self):
        p = self._write({"enrollment_status": "confirmed",
                         "nodes": {"kai-mini": "nrZbQpqJCD11CNTRL"}})
        self.assertEqual(tg.load_allowlist(p), {"kai-mini": "nrZbQpqJCD11CNTRL"})

    def test_duplicate_ids_return_empty(self):
        p = self._write({"enrollment_status": "confirmed",
                         "nodes": {"a": "nrZbQpqJCD11CNTRL", "b": "nrZbQpqJCD11CNTRL"}})
        self.assertEqual(tg.load_allowlist(p), {})

    def test_invalid_id_returns_empty(self):
        p = self._write({"enrollment_status": "confirmed", "nodes": {"a": "bad id"}})
        self.assertEqual(tg.load_allowlist(p), {})

    def test_non_json_returns_empty(self):
        p = self._write("{ not json")
        self.assertEqual(tg.load_allowlist(p), {})

    def test_duplicate_json_keys_return_empty(self):
        # A duplicate name key could silently swap an enrolled node ID (trust-root injection).
        p = self._write('{"enrollment_status":"confirmed","nodes":'
                        '{"kai-mini":"nrZbQpqJCD11CNTRL","kai-mini":"nEVILimposter00"}}')
        self.assertEqual(tg.load_allowlist(p), {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
