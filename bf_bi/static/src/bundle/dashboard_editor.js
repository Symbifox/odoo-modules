/** @odoo-module */
import { Component, onWillStart, onWillUnmount, useExternalListener, useState } from "@odoo/owl";
import { Model } from "@odoo/o-spreadsheet";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { user } from "@web/core/user";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";
import { SpreadsheetComponent } from "@spreadsheet/actions/spreadsheet_component";
import { OdooDataProvider } from "@spreadsheet/data_sources/odoo_data_provider";
import { createDefaultCurrency } from "@spreadsheet/currency/helpers";
import { dashboardActionRegistry } from "@spreadsheet_dashboard/bundle/dashboard_action/dashboard_action";
import { insertMeasures, insertSource, insertSpec } from "./insert";
import { InsertMeasureDialog, InsertSourceDialog } from "./source_dialog";

// Délai d'inactivité avant l'enregistrement automatique.
const SAVE_DELAY = 1500;

export class DashboardEditor extends Component {
    static template = "bf_bi.DashboardEditor";
    static components = { SpreadsheetComponent };
    static props = { ...standardActionServiceProps };

    setup() {
        // Le service orm non protégé : un appel lancé au démontage doit aller au bout.
        this.orm = this.env.services.orm;
        this.actionService = this.env.services.action;
        this.dialog = this.env.services.dialog;
        this.notification = this.env.services.notification;
        const params = this.props.action.params || this.props.action.context?.params || {};
        this.dashboardId = params.dashboard_id;
        this.state = useState({ name: "", status: "saved", loaded: false, connections: [], measures: [] });
        this.timer = null;
        this.saving = null;
        this.blocked = false;

        onWillStart(async () => {
            const res = await this.orm.call("spreadsheet.dashboard", "bf_get_edit_data", [this.dashboardId]);
            this.revision = res.revision;
            this.state.name = res.name;
            const data = res.data;
            data.settings = { ...(data.settings || {}), locale: res.user_locale };
            const odooDataProvider = new OdooDataProvider(this.env);
            this.model = new Model(
                data,
                {
                    custom: { env: this.env, orm: this.orm, odooDataProvider },
                    defaultCurrency: createDefaultCurrency(res.default_currency),
                },
                []
            );
            // Recalculer quand des données arrivent du serveur (mesures, sources, tableaux
            // croisés), comme le fait le lecteur d'Odoo ; sans ceci, une cellule reste sur
            // « Loading... » jusqu'à la prochaine modification.
            this.onDataUpdated = () => this.model.dispatch("EVALUATE_CELLS");
            odooDataProvider.addEventListener("data-source-updated", this.onDataUpdated);
            this.odooDataProvider = odooDataProvider;
            this.lastSaved = this.serialize();
            this.lastName = this.state.name;
            if (params.insert) {
                const spec = params.insert;
                // Une seule fois : un retour par le fil d'Ariane ne doit pas réinsérer.
                delete params.insert;
                await insertSpec(this.model, spec);
                await this.save();
            }
            this.model.on("update", this, () => this.scheduleSave());
            this.state.connections = await this.orm.call("bf.bi.connection", "bf_list_usable", []);
            this.state.measures = await this.orm.call("bf.bi.measure", "bf_list", []);
            this.state.loaded = true;
        });
        useExternalListener(window, "beforeunload", (ev) => {
            if (this.isDirty()) {
                ev.preventDefault();
                ev.returnValue = "";
            }
        });
        onWillUnmount(() => {
            this.destroyed = true;
            this.model?.off("update", this);
            this.odooDataProvider?.removeEventListener("data-source-updated", this.onDataUpdated);
            clearTimeout(this.timer);
            if (this.isDirty() && !this.blocked) {
                // Dernier enregistrement en quittant : une erreur ici n'a plus d'écran où
                // s'afficher ; elle ne doit pas lever de dialogue sur la page suivante.
                this.save().catch(() => {});
            }
        });
    }

    serialize() {
        return JSON.stringify(this.model.exportData());
    }

    isDirty() {
        return Boolean(this.model) && (this.serialize() !== this.lastSaved || this.state.name !== this.lastName);
    }

    get statusLabel() {
        return {
            saved: _t("Saved"),
            dirty: _t("Unsaved changes"),
            saving: _t("Saving…"),
            conflict: _t("Save blocked: newer version on the server"),
            error: _t("Save failed"),
        }[this.state.status];
    }

    scheduleSave() {
        if (this.blocked) {
            return;
        }
        clearTimeout(this.timer);
        if (this.isDirty()) {
            this.state.status = "dirty";
        }
        // L'échec d'un enregistrement automatique s'affiche dans le statut, pas en dialogue.
        this.timer = setTimeout(() => this.save().catch(() => {}), SAVE_DELAY);
    }

    onNameChange() {
        this.scheduleSave();
    }

    async save() {
        if (this.blocked) {
            return;
        }
        if (this.saving) {
            // Un enregistrement en cours : on repasse derrière lui.
            await this.saving;
            return this.save();
        }
        const serial = this.serialize();
        const name = this.state.name;
        if (serial === this.lastSaved && name === this.lastName) {
            this.state.status = "saved";
            return;
        }
        this.state.status = "saving";
        this.saving = this.orm
            .call("spreadsheet.dashboard", "bf_save", [this.dashboardId, JSON.parse(serial), this.revision], {
                name,
            })
            .then((res) => {
                if (this.destroyed) {
                    return;
                }
                if (res.status === "saved") {
                    this.revision = res.revision;
                    this.lastSaved = serial;
                    this.lastName = name;
                    this.state.status = this.isDirty() ? "dirty" : "saved";
                } else {
                    this.state.status = "conflict";
                    this.onConflict(res);
                }
            })
            .catch((error) => {
                if (!this.destroyed) {
                    this.state.status = "error";
                }
                throw error;
            })
            .finally(() => {
                this.saving = null;
            });
        return this.saving;
    }

    openSourceDialog() {
        this.dialog.add(InsertSourceDialog, {
            connections: this.state.connections,
            onPick: async (source) => {
                await insertSource(this.model, source);
                await this.save();
            },
        });
    }

    openMeasureDialog() {
        this.dialog.add(InsertMeasureDialog, {
            measures: this.state.measures,
            onPick: async (measures, as) => {
                await insertMeasures(this.model, measures, as);
                await this.save();
            },
        });
    }

    onConflict(res) {
        this.blocked = true;
        this.conflict = res;
        this.dialog.add(ConfirmationDialog, {
            title: _t("This dashboard has changed"),
            body: _t("%s saved a newer version (revision %s) while you were working. Reload it (your changes since the last save will be lost) or replace it with yours (theirs will stay in the version history).",
                res.author,
                res.revision
            ),
            confirmLabel: _t("Reload their version"),
            confirm: () => this.reload(),
            cancelLabel: _t("Replace with mine"),
            cancel: () => {
                this.blocked = false;
                this.conflict = null;
                this.revision = res.revision;
                this.save();
            },
            // Fermer par le X ne tranche rien : sans ceci, Odoo exécuterait `cancel`,
            // c'est-à-dire « Remplacer par la mienne ». L'éditeur reste bloqué.
            dismiss: () => {},
        });
    }

    reload() {
        this.blocked = true;
        return this.actionService.doAction(
            {
                type: "ir.actions.client",
                tag: "bf_bi.dashboard_editor",
                name: this.state.name,
                params: { dashboard_id: this.dashboardId },
            },
            { stackPosition: "replaceCurrentAction" }
        );
    }

    async close() {
        clearTimeout(this.timer);
        if (this.conflict) {
            // Un conflit laissé sans réponse : on repose la question plutôt que de fermer.
            return this.onConflict(this.conflict);
        }
        await this.save();
        if (this.state.status !== "saved") {
            this.notification.add(_t("Dashboard not saved: %s", this.statusLabel), { type: "danger" });
            return;
        }
        this.blocked = true;
        await this.actionService.doAction("spreadsheet_dashboard.ir_actions_dashboard_action", {
            clearBreadcrumbs: true,
            additionalContext: { params: { dashboard_id: this.dashboardId } },
        });
    }
}

registry.category("actions").add("bf_bi.dashboard_editor", DashboardEditor, { force: true });

/** Crayon « Modifier » à côté de chaque tableau de bord du lecteur d'Odoo, pour les concepteurs. */
export class EditDashboardButton extends Component {
    static template = "bf_bi.EditDashboardButton";
    static props = { dashboardId: Number, onClick: Function };

    setup() {
        this.state = useState({ canDesign: false });
        onWillStart(async () => {
            this.state.canDesign = await user.hasGroup("bf_bi.group_bi_designer");
        });
    }
}

dashboardActionRegistry.add("bf_bi_edit", EditDashboardButton);
