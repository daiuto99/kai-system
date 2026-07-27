"""Deny-path + allow-path tests for tailnet_guard (KAI-984 security core).

The whole value of the guard is that it FAILS CLOSED. Every test below is a way the
target could fail to be a legitimate enrolled KAI tailnet node; all must DENY. The one
allow test is the single narrow path that should succeed.
"""
import unittest

import tailnet_guard as tg

# Enrolled allowlist (name -> stable Tailscale node ID). Matches the design's KAI nodes.
ALLOW = {
    "kai-worker": "nzkpgsJk1M11CNTRL",
    "71-kai-mini": "ntzBBuNMsE11CNTRL",
    "mac-mini": "nwUpbTFAdP11CNTRL",
}


def status(self_entry=None, peers=None):
    return {"Self": self_entry or {}, "Peer": peers or {}}


def node(node_id, ips, online=True, host="host"):
    return {"ID": node_id, "TailscaleIPs": ips, "Online": online, "HostName": host}


class AllowPath(unittest.TestCase):
    def test_enrolled_online_peer_with_cgnat_ip_allows(self):
        st = status(
            self_entry=node("nzkpgsJk1M11CNTRL", ["100.78.94.80"], host="kai-worker"),
            peers={"a": node("ntzBBuNMsE11CNTRL", ["100.106.160.41", "fd7a:115c::1"],
                             online=True, host="Leo's Mac mini (2)")},
        )
        d = tg.evaluate_target("71-kai-mini", ALLOW, st)
        self.assertTrue(d.allowed, d.reason)
        self.assertEqual(d.tailnet_ip, "100.106.160.41")
        self.assertEqual(d.node_id, "ntzBBuNMsE11CNTRL")

    def test_self_node_allows_even_though_no_online_flag(self):
        st = status(self_entry=node("nzkpgsJk1M11CNTRL", ["100.78.94.80"], online=False,
                                    host="kai-worker"))
        d = tg.evaluate_target("kai-worker", ALLOW, st)
        self.assertTrue(d.allowed, d.reason)  # Self is implicitly online (it's the caller)


class DenyPaths(unittest.TestCase):
    def test_name_not_on_allowlist(self):
        st = status(peers={"a": node("someID", ["100.1.2.3"])})
        d = tg.evaluate_target("attacker-laptop", ALLOW, st)
        self.assertFalse(d.allowed)
        self.assertIn("allowlist", d.reason)

    def test_enrolled_id_absent_from_tailnet(self):
        st = status(self_entry=node("nzkpgsJk1M11CNTRL", ["100.78.94.80"]))  # no mini peer
        d = tg.evaluate_target("71-kai-mini", ALLOW, st)
        self.assertFalse(d.allowed)
        self.assertIn("not present", d.reason)

    def test_hostname_spoof_wrong_id_denied(self):
        # A peer that CLAIMS to be the mini by hostname but has a DIFFERENT id must be denied.
        st = status(peers={"evil": node("nEVILimposter00000", ["100.106.160.41"],
                                        online=True, host="Leo's Mac mini (2)")})
        d = tg.evaluate_target("71-kai-mini", ALLOW, st)
        self.assertFalse(d.allowed)
        self.assertIn("not present", d.reason)  # matched by ID, hostname ignored

    def test_offline_node_denied(self):
        st = status(peers={"a": node("ntzBBuNMsE11CNTRL", ["100.106.160.41"], online=False)})
        d = tg.evaluate_target("71-kai-mini", ALLOW, st)
        self.assertFalse(d.allowed)
        self.assertIn("offline", d.reason)

    def test_no_cgnat_ip_denied(self):
        # Only a public IPv4 + an IPv6 — no 100.64/10 address.
        st = status(peers={"a": node("ntzBBuNMsE11CNTRL", ["8.8.8.8", "fd7a:115c::9"])})
        d = tg.evaluate_target("71-kai-mini", ALLOW, st)
        self.assertFalse(d.allowed)
        self.assertIn("no tailnet", d.reason)

    def test_ip_outside_cgnat_denied(self):
        st = status(peers={"a": node("ntzBBuNMsE11CNTRL", ["10.0.0.5", "192.168.1.2"])})
        d = tg.evaluate_target("71-kai-mini", ALLOW, st)
        self.assertFalse(d.allowed)
        self.assertIn("no tailnet", d.reason)

    def test_malformed_status_denies_no_crash(self):
        for bad in [{}, {"Self": None, "Peer": None}, {"Peer": "nope"}, {"Self": 5}]:
            d = tg.evaluate_target("71-kai-mini", ALLOW, bad)
            self.assertFalse(d.allowed)

    def test_empty_allowlist_denies(self):
        st = status(peers={"a": node("ntzBBuNMsE11CNTRL", ["100.106.160.41"])})
        self.assertFalse(tg.evaluate_target("71-kai-mini", {}, st).allowed)

    def test_invalid_name_denies(self):
        st = status(peers={"a": node("ntzBBuNMsE11CNTRL", ["100.106.160.41"])})
        for bad in [None, "", 123]:
            self.assertFalse(tg.evaluate_target(bad, ALLOW, st).allowed)

    def test_garbage_status_type_denies(self):
        for bad in [None, [], "status", 42]:
            self.assertFalse(tg.evaluate_target("71-kai-mini", ALLOW, bad).allowed)

    def test_allowlist_entry_maps_to_missing_ip_field(self):
        st = status(peers={"a": {"ID": "ntzBBuNMsE11CNTRL", "Online": True}})  # no TailscaleIPs
        d = tg.evaluate_target("71-kai-mini", ALLOW, st)
        self.assertFalse(d.allowed)
        self.assertIn("no tailnet", d.reason)


class LoadAllowlist(unittest.TestCase):
    def test_bad_path_returns_empty(self):
        self.assertEqual(tg.load_allowlist("/nonexistent/allowlist.json"), {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
