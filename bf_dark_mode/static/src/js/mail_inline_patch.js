/** @odoo-module **/
import { patch } from "@web/core/utils/patch";
import { HtmlMailField } from "@mail/views/web/fields/html_mail_field/html_mail_field";

/**
 * Keep the dark theme out of outgoing email bodies.
 *
 * Odoo inlines every applicable CSS declaration before an email leaves the
 * composer, because a mail client has no stylesheet to consult. To resolve
 * those declarations it inserts a copy of the body into the LIVE document
 * (`editor.editable.after(el)`), collects `document.styleSheets`, and applies
 * whatever matches. CSS selectors resolve against the whole document, not
 * against the queried subtree, so a copy sitting under `<body class="…">`
 * matches every `body.bf_dark_mode p { color: … }` rule this module ships —
 * and the dark grays end up baked into the message the recipient receives.
 *
 * Dropping the body class for the duration of the pass closes both routes at
 * once: selector matching (`classToStyle`) and the direct `getComputedStyle`
 * reads `toInline` performs for Outlook table cells and icon-to-image
 * conversion, which resolve inheritance from `<body>`. Filtering the rule list
 * alone would only close the first.
 *
 * Cost: the backend renders light for the duration of the pass. That is a
 * blink on send, longer when the body holds images awaiting conversion.
 */
patch(HtmlMailField, {
    async getInlinedEditorContent(cssRulesByElement, editor, el) {
        const body = editor.document.body;
        const wasDark = body.classList.contains("bf_dark_mode");
        if (wasDark) {
            body.classList.remove("bf_dark_mode");
        }
        try {
            return await super.getInlinedEditorContent(cssRulesByElement, editor, el);
        } finally {
            if (wasDark) {
                body.classList.add("bf_dark_mode");
            }
        }
    },
});
