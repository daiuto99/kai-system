import json
import pytest
from pathlib import Path


# S1-4: capability_map.json
def test_capability_map_no_wp_cli():
    map_path = Path("capabilities/capability_map.json")
    data = json.loads(map_path.read_text())
    # Walk every transport list and assert wp_cli never appears
    def _all_transports(obj):
        if isinstance(obj, list):
            yield from obj
        elif isinstance(obj, dict):
            for v in obj.values():
                yield from _all_transports(v)
    assert "wp_cli" not in list(_all_transports(data)), "wp_cli found in capability_map.json"


def test_capability_map_purge_transport():
    map_path = Path("capabilities/capability_map.json")
    data = json.loads(map_path.read_text())
    assert data["default_cloudways"]["purge_varnish"] == ["cloudways_ssh_purge"]


def test_capability_map_set_option_order():
    map_path = Path("capabilities/capability_map.json")
    data = json.loads(map_path.read_text())
    # REST route must be first — it's the primary; ssh_php_eval is fallback
    transports = data["default_cloudways"]["set_option"]
    assert transports[0] == "wp_rest_kai_route"
    assert "ssh_php_eval" in transports


# S1-5: get_transports resolution
def test_get_transports_default():
    from capabilities import get_transports
    t = get_transports("sette-uno.com", "set_option")
    assert t[0] == "wp_rest_kai_route"


def test_get_transports_purge():
    from capabilities import get_transports
    t = get_transports("sette-uno.com", "purge_varnish")
    assert t == ["cloudways_ssh_purge"]


# S1-6: ssh_php_eval has no run(raw_php) method and enforces allowlist
def test_ssh_php_eval_no_raw_run():
    from transports import ssh_php_eval
    assert not hasattr(ssh_php_eval, "run"), "ssh_php_eval must not have a run() method"


def test_ssh_php_eval_allowlist_rejects():
    from transports.ssh_php_eval import set_option
    r = set_option("sette-uno.com", "siteurl", "http://evil.com", {})
    assert r.ok is False
    assert "allowlist" in r.error


# S1-7: wordpress.set_option capability — allowlist enforcement
def test_set_option_capability_rejects_non_allowlisted():
    from capabilities.wordpress import set_option
    result = set_option("sette-uno.com", "blogname", "hacked", {})
    assert result.ok is False
    assert result.status == "failed_final"
    assert result.error["type"] == "option_not_allowed"


# S1-8: publish_homepage workflow — step names and count
def test_publish_homepage_steps():
    from workflows.wordpress_publish_homepage import PublishHomepageWorkflow
    step_names = [s.name for s in PublishHomepageWorkflow.steps]
    assert "disable_coming_soon" in step_names
    assert "verify_live" in step_names
    assert "complete" in step_names
    assert len(step_names) == 13


def test_publish_homepage_name():
    from workflows.wordpress_publish_homepage import PublishHomepageWorkflow
    assert PublishHomepageWorkflow.name == "wordpress.publish_homepage"
