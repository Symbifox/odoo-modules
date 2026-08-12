/** @odoo-module **/
import { Component } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";

// ---------------------------------------------------------------------------
// 1. Namespace setup — triggered by "*" prefix in the command palette
// ---------------------------------------------------------------------------
registry.category("command_setup").add("*", {
    debounceDelay: 300,
    emptyMessage: _t("Aucun résultat trouvé"),
    name: _t("enregistrements"),
    placeholder: _t("Rechercher partout... (un numéro ouvre la tâche ou le ticket)"),
});

// ---------------------------------------------------------------------------
// 2. Categories — grouping within the "*" namespace
// ---------------------------------------------------------------------------
const catReg = registry.category("command_categories");
catReg.add("search_contacts", { namespace: "*", name: _t("Contacts") }, { sequence: 10 });
catReg.add("search_crm", { namespace: "*", name: _t("CRM") }, { sequence: 15 });
catReg.add("search_projects", { namespace: "*", name: _t("Projets") }, { sequence: 20 });
catReg.add("search_meetings", { namespace: "*", name: _t("Rencontres") }, { sequence: 25 });
catReg.add("search_comms", { namespace: "*", name: _t("Communications") }, { sequence: 30 });
catReg.add("search_hosting", { namespace: "*", name: _t("Hébergement") }, { sequence: 35 });
catReg.add("search_documents", { namespace: "*", name: _t("Documents") }, { sequence: 40 });
catReg.add("search_finance", { namespace: "*", name: _t("Finance") }, { sequence: 45 });
catReg.add("search_other", { namespace: "*", name: _t("Autres") }, { sequence: 50 });

// ---------------------------------------------------------------------------
// 3. Command item — model icon, context line, struck out when closed
// ---------------------------------------------------------------------------
export class UniversalSearchCommandItem extends Component {
    static template = "bf_universal_search.CommandItem";
    static props = {
        // Passed by the command palette itself.
        slots: { type: Object, optional: true },
        name: { type: String, optional: true },
        searchValue: { type: String, optional: true },
        executeCommand: { type: Function, optional: true },
        hotkey: { type: String, optional: true },
        hotkeyOptions: { type: String, optional: true },
        // Passed by this provider.
        detail: { type: String, optional: true },
        icon: { type: String, optional: true },
        closed: { type: Boolean, optional: true },
    };
}

// ---------------------------------------------------------------------------
// 4. Provider — async search via bf.universal.search.search_all()
// ---------------------------------------------------------------------------
registry.category("command_provider").add("bf_universal_search", {
    namespace: "*",
    async provide(env, options) {
        const query = options.searchValue;
        if (!query || query.length < 2) {
            return [];
        }

        let groups;
        try {
            groups = await env.services.orm.call(
                "bf.universal.search",
                "search_all",
                [query],
            );
        } catch (e) {
            console.error("bf_universal_search: RPC error", e);
            return [];
        }

        const commands = [];
        for (const group of groups) {
            for (const result of group.results) {
                commands.push({
                    name: result.name,
                    category: group.category,
                    // Ctrl+Enter opens the record in a new tab.
                    href: `/odoo/m-${group.model}/${result.id}`,
                    Component: UniversalSearchCommandItem,
                    props: {
                        detail: result.detail || "",
                        icon: group.icon || "fa fa-search",
                        closed: Boolean(result.closed),
                    },
                    action() {
                        env.services.action.doAction({
                            type: "ir.actions.act_window",
                            res_model: group.model,
                            res_id: result.id,
                            views: [[false, "form"]],
                        });
                    },
                });
            }
        }
        return commands;
    },
});
