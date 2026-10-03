/** @odoo-module **/
// Checks the documents before the public admission form is posted: past the route's size
// limit, Odoo answers « 413 Request Entity Too Large » before the controller, and the family
// loses everything typed. The messages are rendered (and translated) by the page.
import publicWidget from "@web/legacy/js/public/public_widget";

publicWidget.registry.SchoolAdmissionDocuments = publicWidget.Widget.extend({
    selector: ".o_school_admission_form",
    events: {
        "change input[name=documents]": "_onDocuments",
        submit: "_onSubmit",
    },

    _check() {
        const input = this.el.querySelector("input[name=documents]");
        const files = Array.from(input.files || []);
        const max = parseInt(this.el.dataset.maxFiles, 10);
        const size = parseInt(this.el.dataset.maxSize, 10);
        let message = "";
        if (files.length > max) {
            message = this.el.querySelector(".o_school_too_many").textContent.trim();
        } else if (files.some((file) => file.size > size)) {
            message = this.el.querySelector(".o_school_too_big").textContent.trim();
        }
        input.setCustomValidity(message);
        return message ? input : null;
    },

    _onDocuments() {
        this._check()?.reportValidity();
    },

    _onSubmit(ev) {
        const input = this._check();
        if (input) {
            ev.preventDefault();
            input.reportValidity();
        }
    },
});
