/** @odoo-module **/

import { Component } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";

export class BfWebmailDialog extends Component {
    static template = "bf_webmail.WebmailDialog";
    static components = { Dialog };
    static props = {
        url: String,
        close: { type: Function, optional: true },
    };
}
