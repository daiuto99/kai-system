<?php
/**
 * Plugin Name: KAI Blank Canvas
 * Description: Registers a full-width blank page template for KAI-designed pages. No theme header/footer — KAI owns the entire page.
 * Version: 1.0.0
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
