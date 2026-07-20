<?php
/**
 * Plugin Name: KAI Publish Gate
 * Description: Enforces JARVIS §9: posts and pages remain drafts until an exact-post publish gate is explicitly opened.
 * Version: 1.0.0
 */

if (!defined('ABSPATH')) {
    exit;
}

define('KAI_PUBLISH_GATE_META', '_kai_publish_gate');
define('KAI_PUBLISH_GATE_AUDIT_META', '_kai_publish_gate_audit');

/** Return the dedicated approval secret, never the KAI WordPress app password. */
function kai_publish_gate_secret() {
    if (defined('KAI_PUBLISH_GATE_SECRET') && KAI_PUBLISH_GATE_SECRET !== '') {
        return KAI_PUBLISH_GATE_SECRET;
    }

    // Cloudways app roots are /home/<app-user>/public_html. The default file
    // therefore lives one level above ABSPATH, outside the web root.
    $secret_file = defined('KAI_PUBLISH_GATE_SECRET_FILE') && KAI_PUBLISH_GATE_SECRET_FILE !== ''
        ? KAI_PUBLISH_GATE_SECRET_FILE
        : dirname(untrailingslashit(ABSPATH)) . '/kai_publish_gate_secret';
    if (!is_readable($secret_file)) {
        return '';
    }

    return trim((string) file_get_contents($secret_file));
}

/** True only for the exact post whose dedicated gate flag has been set. */
function kai_publish_gate_is_open($post_id) {
    return (string) get_post_meta((int) $post_id, KAI_PUBLISH_GATE_META, true) === '1';
}

/**
 * Write-time enforcement. This runs before the post row is saved, covering
 * wp-admin, REST, XML-RPC, and any other route that calls wp_insert_post().
 */
function kai_publish_gate_force_draft($data, $postarr) {
    $post_type = isset($data['post_type']) ? $data['post_type'] : (isset($postarr['post_type']) ? $postarr['post_type'] : 'post');
    if (!in_array($post_type, array('post', 'page'), true)) {
        return $data;
    }

    $requested_status = isset($data['post_status']) ? $data['post_status'] : '';
    if (!in_array($requested_status, array('publish', 'future'), true)) {
        return $data;
    }

    $post_id = isset($postarr['ID']) ? (int) $postarr['ID'] : 0;
    if ($post_id > 0 && kai_publish_gate_is_open($post_id)) {
        return $data;
    }

    $data['post_status'] = 'draft';
    do_action('kai_publish_gate_blocked', $post_id, $post_type, $requested_status);
    return $data;
}
add_filter('wp_insert_post_data', 'kai_publish_gate_force_draft', 999, 2);

/** Logging only: it is intentionally not the enforcement point. */
function kai_publish_gate_log_unapproved_transition($new_status, $old_status, $post) {
    if (!in_array($new_status, array('publish', 'future'), true) || !in_array($post->post_type, array('post', 'page'), true)) {
        return;
    }
    if (!kai_publish_gate_is_open($post->ID)) {
        error_log(sprintf('KAI Publish Gate drift: post %d transitioned %s -> %s without a gate', $post->ID, $old_status, $new_status));
    }
}
add_action('transition_post_status', 'kai_publish_gate_log_unapproved_transition', 10, 3);

/** The normal editor and REST meta endpoint can never set this capability. */
function kai_publish_gate_register_meta() {
    register_post_meta('', KAI_PUBLISH_GATE_META, array(
        'type'          => 'string',
        'single'        => true,
        'show_in_rest'  => true,
        'auth_callback' => '__return_false',
    ));
    register_post_meta('', KAI_PUBLISH_GATE_AUDIT_META, array(
        'type'          => 'array',
        'single'        => true,
        'show_in_rest'  => false,
        'auth_callback' => '__return_false',
    ));
}
add_action('init', 'kai_publish_gate_register_meta');

function kai_publish_gate_permission($request) {
    $expected = kai_publish_gate_secret();
    $provided = (string) $request->get_header('X-KAI-Publish-Gate');
    if ($expected === '' || $provided === '' || !hash_equals($expected, $provided)) {
        return new WP_Error('kai_publish_gate_forbidden', 'A valid publish-gate approval secret is required.', array('status' => 403));
    }
    return true;
}

function kai_publish_gate_open($request) {
    $post_id = (int) $request['id'];
    $post = get_post($post_id);
    if (!$post || !in_array($post->post_type, array('post', 'page'), true)) {
        return new WP_Error('kai_publish_gate_invalid_post', 'Post or page not found.', array('status' => 404));
    }

    $resolver = sanitize_text_field((string) $request->get_header('X-KAI-Resolver'));
    $gate_id = sanitize_text_field((string) $request->get_header('X-KAI-Gate-ID'));
    $audit = array(
        'post_id' => $post_id,
        'resolver' => $resolver,
        'timestamp' => current_time('mysql', true),
        'gate_id' => $gate_id,
    );

    update_post_meta($post_id, KAI_PUBLISH_GATE_META, '1');
    update_post_meta($post_id, KAI_PUBLISH_GATE_AUDIT_META, $audit);

    return array('post_id' => $post_id, 'gate_open' => true, 'audit' => $audit);
}

function kai_publish_gate_register_route() {
    register_rest_route('kai/v1', '/publish-gate/(?P<id>\d+)', array(
        'methods'             => 'POST',
        'callback'            => 'kai_publish_gate_open',
        'permission_callback' => 'kai_publish_gate_permission',
    ));
}
add_action('rest_api_init', 'kai_publish_gate_register_route');
