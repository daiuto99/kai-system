<?php
/**
 * Plugin Name: KAI Blank Canvas
 * Description: Registers a full-width blank page template for KAI-designed pages. No theme header/footer — KAI owns the entire page. Supports per-slug static bundles (kai-pages/<slug>/{style.css,body.html}) so CSS, OFL fonts, and SVGs render at full fidelity without passing through kses.
 * Version: 1.2.0
 */

if (!defined('ABSPATH')) exit;

add_filter('theme_page_templates', function($templates) {
    $templates['kai-blank'] = 'KAI Blank Canvas';
    return $templates;
});

add_filter('template_include', function($template) {
    if (is_page()) {
        $page_template = get_post_meta(get_the_ID(), '_wp_page_template', true);
        if ($page_template === 'kai-blank') {
            the_post();
            $content = get_the_content();

            // Per-slug static bundle: CSS + OFL fonts + body HTML deployed alongside
            // this plugin at kai-pages/<slug>/. Bypasses kses (which strips <style> and
            // <svg> from post_content). Falls back to raw post content when absent.
            $slug = get_post_field('post_name', get_the_ID());
            $use_bundle = false;
            if ($slug && preg_match('/^[a-z0-9-]+$/', $slug)) {
                $body_file = WPMU_PLUGIN_DIR . '/kai-pages/' . $slug . '/body.html';
                $css_file  = WPMU_PLUGIN_DIR . '/kai-pages/' . $slug . '/style.css';
                $use_bundle = file_exists($body_file);
            }

            // Strip WP auto-formatting — output raw HTML as designed
            echo '<!DOCTYPE html><html lang="en"><head>';
            echo '<meta charset="UTF-8">';
            echo '<meta name="viewport" content="width=device-width, initial-scale=1.0">';
            echo '<title>' . esc_html(get_the_title()) . ' — ' . esc_html(get_bloginfo('name')) . '</title>';
            if ($use_bundle && file_exists($css_file)) {
                echo '<link rel="stylesheet" href="' . esc_url(WPMU_PLUGIN_URL . '/kai-pages/' . $slug . '/style.css') . '">';
            }
            // Inject WP head for SEO plugins + favicons
            wp_head();
            echo '</head><body>';
            if ($use_bundle) {
                echo file_get_contents($body_file);
            } else {
                echo $content;
            }
            wp_footer();
            echo '</body></html>';
            exit;
        }
    }
    return $template;
});

add_action('rest_api_init', function() {
    $allowed_options = ['kai_cs_active'];

    register_rest_route('kai/v1', '/option/(?P<name>[a-zA-Z0-9_]+)', [
        [
            'methods'             => 'GET',
            'callback'            => function($req) use ($allowed_options) {
                $n = $req->get_param('name');
                if (!in_array($n, $allowed_options)) {
                    return new WP_Error('forbidden', 'Option not in allowlist', ['status' => 403]);
                }
                return ['option' => $n, 'value' => get_option($n, null)];
            },
            'permission_callback' => 'is_user_logged_in',
        ],
        [
            'methods'             => 'POST',
            'callback'            => function($req) use ($allowed_options) {
                $n = $req->get_param('name');
                if (!in_array($n, $allowed_options)) {
                    return new WP_Error('forbidden', 'Option not in allowlist', ['status' => 403]);
                }
                $v = sanitize_text_field($req->get_param('value'));
                update_option($n, $v);
                return ['option' => $n, 'value' => get_option($n), 'updated' => true];
            },
            'permission_callback' => 'is_user_logged_in',
        ],
    ]);
});
