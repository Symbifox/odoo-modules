/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { KnowledgeDashboard } from "@project_knowledge_matrix/js/knowledge_dashboard";

/**
 * Les quatre navigations de la carte « Identifiants ».
 *
 * Elles vivaient dans le composant du socle jusqu'à la 18.0.12.0.0. Le gabarit
 * qui les appelle est ajouté par le même module qu'elles : si le correctif ne
 * se charge pas, la carte n'existe pas non plus, donc aucun t-on-click ne
 * pointe dans le vide.
 *
 * `_getProjectDomain` est surchargé ici aussi : le socle ne connaît plus
 * `project.credential` et rendrait un domaine vide, donc la liste ouverte
 * depuis un tableau de bord filtré par projet montrerait TOUT le parc.
 */
patch(KnowledgeDashboard.prototype, {
    _getProjectDomain(model) {
        if (this.state.projectId && model === "project.credential") {
            return [["project_id", "=", this.state.projectId]];
        }
        return super._getProjectDomain(model);
    },

    openCredentials() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Identifiants",
            res_model: "project.credential",
            views: [[false, "list"], [false, "kanban"], [false, "form"]],
            domain: this._getProjectDomain("project.credential"),
            context: { search_default_filter_active: 1 },
        });
    },

    openExpiringCredentials() {
        // Le domaine doit refléter get_credential_metrics à l'identique. Il a
        // porté le même défaut que le compteur : il cherchait des identifiants
        // « actifs » alors que la tâche quotidienne les fait passer à
        // « expiring ». Compteur et liste annonçaient zéro de concert.
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Identifiants expirant sous 30 jours",
            res_model: "project.credential",
            views: [[false, "list"], [false, "form"]],
            domain: [...this._getProjectDomain("project.credential"), ["state", "=", "expiring"]],
            context: {},
        });
    },

    openExpiredCredentials() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Identifiants expirés",
            res_model: "project.credential",
            views: [[false, "list"], [false, "form"]],
            domain: [...this._getProjectDomain("project.credential"), ["state", "=", "expired"]],
            context: {},
        });
    },

    // Les deux chiffres du deuxième facteur. Leur domaine doit refléter
    // `get_credential_metrics` à l'identique, RÉVOQUÉS EXCLUS : un identifiant
    // révoqué n'a plus de facteur à documenter, et l'inclure ferait grossir une
    // liste de travail que personne ne peut vider.
    openMfaToDocument() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Deuxième facteur à documenter",
            res_model: "project.credential",
            views: [[false, "list"], [false, "form"]],
            domain: [
                ...this._getProjectDomain("project.credential"),
                ["state", "!=", "revoked"],
                ["mfa_state", "=", "unknown"],
            ],
            context: {},
        });
    },

    openMfaAtRisk() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Deuxième facteur sans relève",
            res_model: "project.credential",
            views: [[false, "list"], [false, "form"]],
            domain: [
                ...this._getProjectDomain("project.credential"),
                ["state", "!=", "revoked"],
                ["mfa_state", "=", "at_risk"],
            ],
            context: {},
        });
    },

    openRevokedCredentials() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Identifiants révoqués",
            res_model: "project.credential",
            views: [[false, "list"], [false, "form"]],
            domain: [...this._getProjectDomain("project.credential"), ["state", "=", "revoked"]],
            context: {},
        });
    },
});
