<?php
/**
 * Plugin Name: KAI Blank Canvas
 * Description: Registers a full-width blank page template for KAI-designed pages. No theme header/footer — KAI owns the entire page.
 * Version: 1.1.0
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
            // Strip WP auto-formatting — output raw HTML as designed
            echo '<!DOCTYPE html><html lang="en"><head>';
            echo '<meta charset="UTF-8">';
            echo '<meta name="viewport" content="width=device-width, initial-scale=1.0">';
            echo '<title>' . esc_html(get_the_title()) . ' — ' . esc_html(get_bloginfo('name')) . '</title>';
            // Inject WP head for SEO plugins + favicons
            wp_head();
            echo '</head><body>';
            echo $content;
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
