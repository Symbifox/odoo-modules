/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { KnowledgeDashboard } from "@project_knowledge_matrix/js/knowledge_dashboard";

/**
 * Les cinq navigations du bloc de gouvernance.
 *
 * Elles vivaient dans le composant du socle jusqu'à la 18.0.11.5.0. Le
 * gabarit qui les appelle est ajouté par le même module qu'elles : si le
 * correctif ne se charge pas, le bloc n'existe pas non plus, donc aucun
 * t-on-click ne pointe dans le vide.
 */
patch(KnowledgeDashboard.prototype, {
    openResolutions() {
        this.action.doAction("bf_corporate_governance.corporate_resolution_action");
    },

    openDirectors() {
        this.action.doAction("bf_corporate_governance.corporate_director_action");
    },

    openOfficers() {
        this.action.doAction("bf_corporate_governance.corporate_officer_action");
    },

    openComplianceCalendar() {
        this.action.doAction("bf_corporate_governance.corporate_compliance_action");
    },

    openOverdueCompliance() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Conformité en retard",
            res_model: "corporate.compliance.event",
            views: [[false, "list"], [false, "form"]],
            domain: [["status", "=", "overdue"]],
            context: {},
        });
    },
});
