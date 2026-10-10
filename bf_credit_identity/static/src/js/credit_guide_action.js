/** @odoo-module **/
// Le guide « Crédit et identité » : un gabarit QWeb rendu par le serveur dans la
// langue de la personne (bf.credit.guide.get_guide_html), affiché tel quel.

import { Component, markup, onWillStart } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

export class CreditGuide extends Component {
    static template = "bf_credit_identity.CreditGuide";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.html = "";
        onWillStart(async () => {
            const html = await this.orm.call("bf.credit.guide", "get_guide_html", []);
            this.html = markup(html);
        });
    }
}

registry.category("actions").add("bf_credit_identity.guide", CreditGuide);
