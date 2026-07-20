<?php
// Dependency-free unit tests for the KAI Publish Gate WordPress plugin.
define('ABSPATH', '/var/www/html/');
define('KAI_PUBLISH_GATE_SECRET', 'test-only-publish-gate-secret');

$filters = array();
$actions = array();
$meta_registration = array();
$routes = array();
$meta = array();
$posts = array(42 => (object) array('ID' => 42, 'post_type' => 'post'));

function add_filter($tag, $callback, $priority = 10, $accepted_args = 1) { global $filters; $filters[$tag] = $callback; }
function add_action($tag, $callback, $priority = 10, $accepted_args = 1) { global $actions; $actions[$tag] = $callback; }
function do_action() {}
function register_post_meta($type, $key, $args) { global $meta_registration; $meta_registration[$key] = $args; }
function register_rest_route($namespace, $route, $args) { global $routes; $routes[$namespace . $route] = $args; }
function get_post_meta($id, $key, $single = true) { global $meta; return isset($meta[$id][$key]) ? $meta[$id][$key] : ''; }
function update_post_meta($id, $key, $value) { global $meta; $meta[$id][$key] = $value; return true; }
function get_post($id) { global $posts; return isset($posts[$id]) ? $posts[$id] : null; }
function sanitize_text_field($value) { return trim($value); }
function current_time($type, $gmt = false) { return '2026-07-20 18:00:00'; }
function untrailingslashit($value) { return rtrim($value, '/'); }
function __return_false() { return false; }
class WP_Error { public $code; public $data; function __construct($code, $message, $data) { $this->code = $code; $this->data = $data; } }
class FakeRequest implements ArrayAccess {
    private $headers; private $params;
    function __construct($headers = array(), $params = array()) { $this->headers = $headers; $this->params = $params; }
    function get_header($name) { return isset($this->headers[$name]) ? $this->headers[$name] : ''; }
    function offsetExists($offset) { return isset($this->params[$offset]); }
    function offsetGet($offset) { return $this->params[$offset]; }
    function offsetSet($offset, $value) { $this->params[$offset] = $value; }
    function offsetUnset($offset) { unset($this->params[$offset]); }
}
function assert_true($condition, $message) { if (!$condition) { throw new Exception($message); } }

$plugin_file = getenv('KAI_PUBLISH_GATE_PLUGIN_FILE');
require $plugin_file ? $plugin_file : dirname(__DIR__) . '/kai-publish-gate.php';

// Register hooks exactly as WordPress does.
call_user_func($actions['init']);
call_user_func($actions['rest_api_init']);

// wp-admin, REST, and XML-RPC all converge on wp_insert_post_data.
foreach (array('wp-admin', 'REST', 'XML-RPC') as $channel) {
    $result = kai_publish_gate_force_draft(array('post_type' => 'post', 'post_status' => 'publish'), array('ID' => 42, 'post_type' => 'post'));
    assert_true($result['post_status'] === 'draft', "$channel publish without a gate must become draft");
}

// The normal meta registration explicitly denies writes.
assert_true($meta_registration[KAI_PUBLISH_GATE_META]['auth_callback'] === '__return_false', 'gate meta must be denied through normal editor/REST meta');
assert_true(call_user_func($meta_registration[KAI_PUBLISH_GATE_META]['auth_callback']) === false, 'normal meta writer must be denied');

// Missing and wrong approval secrets are rejected.
$missing = kai_publish_gate_permission(new FakeRequest());
$wrong = kai_publish_gate_permission(new FakeRequest(array('X-KAI-Publish-Gate' => 'wrong')));
assert_true($missing instanceof WP_Error && $missing->data['status'] === 403, 'missing secret must return 403');
assert_true($wrong instanceof WP_Error && $wrong->data['status'] === 403, 'wrong secret must return 403');

// Only the dedicated authenticated route can open the gate and writes its audit row.
$route = $routes['kai/v1/publish-gate/(?P<id>\d+)'];
$request = new FakeRequest(array(
    'X-KAI-Publish-Gate' => 'test-only-publish-gate-secret',
    'X-KAI-Resolver' => 'leo',
    'X-KAI-Gate-ID' => 'gate-42',
), array('id' => 42));
assert_true(call_user_func($route['permission_callback'], $request) === true, 'correct secret must authorize route');
$opened = call_user_func($route['callback'], $request);
assert_true($opened['gate_open'] === true && $meta[42][KAI_PUBLISH_GATE_META] === '1', 'authenticated route must open only this post gate');
assert_true($meta[42][KAI_PUBLISH_GATE_AUDIT_META]['gate_id'] === 'gate-42', 'gate opening must record audit');

$allowed = kai_publish_gate_force_draft(array('post_type' => 'post', 'post_status' => 'publish'), array('ID' => 42, 'post_type' => 'post'));
assert_true($allowed['post_status'] === 'publish', 'a post opened through the authenticated route must publish');

echo "PASS: KAI Publish Gate enforcement tests\n";
