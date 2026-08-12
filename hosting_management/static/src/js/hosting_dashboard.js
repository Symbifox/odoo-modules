/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component, useState, onWillStart } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";

export class HostingDashboard extends Component {
    static template = "hosting_management.Dashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");

        this.state = useState({
            data: null,
            loading: true,
            refreshing: false,
            expandedSoftware: {},
        });

        onWillStart(async () => {
            await this.loadDashboardData();
        });
    }

    async loadDashboardData() {
        this.state.loading = true;
        try {
            this.state.data = await this.orm.call(
                "hosting.dashboard",
                "get_dashboard_data",
                []
            );
            // Auto-expand groups flagged as worthy of mention.
            const expanded = {};
            for (const g of this.state.data.uptime_by_software || []) {
                if (g.has_issues) {
                    expanded[g.software_id] = true;
                }
            }
            this.state.expandedSoftware = expanded;
        } catch (error) {
            console.error("Error loading dashboard data:", error);
        }
        this.state.loading = false;
    }

    toggleSoftware(softwareId) {
        this.state.expandedSoftware[softwareId] = !this.state.expandedSoftware[softwareId];
    }

    isSoftwareExpanded(softwareId) {
        return !!this.state.expandedSoftware[softwareId];
    }

    async refresh() {
        await this.loadDashboardData();
    }

    // ---- Action buttons ----

    async refreshAll() {
        this.state.refreshing = true;
        try {
            const result = await this.orm.call(
                "hosting.dashboard",
                "action_refresh_all",
                []
            );
            const parts = [];
            parts.push(_t("Santé : %s vérifiés", result.health_check_count));
            parts.push(_t("Versions : %s vérifiées", result.version_check_count));
            if (result.docker_check_count > 0) {
                parts.push(_t("Docker : %s vérifiés", result.docker_check_count));
            }
            const msgType = result.errors.length > 0 ? "warning" : "success";
            if (result.errors.length > 0) {
                parts.push(_t("Erreurs : %s", result.errors.length));
            }
            this.notification.add(parts.join(" | "), {
                type: msgType,
                title: _t("Actualisation terminée"),
                sticky: true,
            });
            await this.loadDashboardData();
        } catch (error) {
            console.error("Error refreshing:", error);
            this.notification.add(
                _t("Erreur lors de l'actualisation. Vérifiez les journaux du serveur."),
                { type: "danger", title: _t("Erreur") }
            );
        }
        this.state.refreshing = false;
    }

    async runHealthChecks() {
        this.state.refreshing = true;
        try {
            const result = await this.orm.call(
                "hosting.dashboard",
                "action_run_health_checks",
                []
            );
            if (result.error) {
                this.notification.add(result.error, {
                    type: "danger",
                    title: _t("Erreur"),
                });
            } else {
                const msgType = result.down > 0 ? "warning" : "success";
                this.notification.add(
                    _t("%s services vérifiés : %s en ligne, %s en panne", result.checked, result.up, result.down),
                    { type: msgType, title: _t("Vérifications de santé terminées"), sticky: true }
                );
            }
            await this.loadDashboardData();
        } catch (error) {
            console.error("Error running health checks:", error);
            this.notification.add(
                _t("Erreur lors des vérifications de santé."),
                { type: "danger", title: _t("Erreur") }
            );
        }
        this.state.refreshing = false;
    }

    async checkVersions() {
        this.state.refreshing = true;
        try {
            const result = await this.orm.call(
                "hosting.dashboard",
                "action_check_versions",
                []
            );
            const msgType = result.errors.length > 0 ? "warning" : "success";
            const msg = result.errors.length > 0
                ? _t("%s logiciels vérifiés. Erreurs : %s", result.checked, result.errors.length)
                : _t("%s logiciels vérifiés. Tout est à jour.", result.checked);
            this.notification.add(msg, {
                type: msgType,
                title: _t("Vérification des versions terminée"),
                sticky: true,
            });
            await this.loadDashboardData();
        } catch (error) {
            console.error("Error checking versions:", error);
            this.notification.add(
                _t("Erreur lors de la vérification des versions."),
                { type: "danger", title: _t("Erreur") }
            );
        }
        this.state.refreshing = false;
    }

    // ---- Navigation helpers ----

    async _doAction(methodName) {
        const action = await this.orm.call(
            "hosting.dashboard",
            methodName,
            []
        );
        this.action.doAction(action);
    }

    // Expiring
    openExpiring30() { this._doAction("action_view_expiring_30"); }
    openExpiring60() { this._doAction("action_view_expiring_60"); }
    openExpiring90() { this._doAction("action_view_expiring_90"); }

    // Alerts
    openUpdates() { this._doAction("action_view_updates"); }
    openStorageAlerts() { this._doAction("action_view_storage_alerts"); }
    openHealthIssues() { this._doAction("action_view_health_issues"); }

    // Services
    openActiveServices() { this._doAction("action_view_active_services"); }
    openSuspendedServices() { this._doAction("action_view_suspended_services"); }
    openExpiredServices() { this._doAction("action_view_expired_services"); }

    // Maintenance
    openMaintenanceOverdue() { this._doAction("action_view_maintenance_overdue"); }
    openMaintenanceDueWeek() { this._doAction("action_view_maintenance_due_week"); }
    openMaintenanceDueMonth() { this._doAction("action_view_maintenance_due_month"); }

    // Backups
    openBackupRuns() { this._doAction("action_view_backup_runs"); }
    openFailedBackups() { this._doAction("action_view_failed_backups"); }
    openResticRepos() { this._doAction("action_view_restic_repositories"); }
    openResticSnapshots() { this._doAction("action_view_restic_snapshots"); }

    // Domains
    openDomains() { this._doAction("action_view_domains"); }
    openDomainsExpiring() { this._doAction("action_view_domains_expiring"); }
    openSslExpiring() { this._doAction("action_view_ssl_expiring"); }
    openDomainsNoAutoRenew() { this._doAction("action_view_domains_no_auto_renew"); }

    // Security / Audit
    openAuditLog24h() { this._doAction("action_view_audit_log_24h"); }
    openAuditLog7d() { this._doAction("action_view_audit_log_7d"); }
    openAuditCritical7d() { this._doAction("action_view_audit_critical_7d"); }
    openSecurityEventsOpen() { this._doAction("action_view_security_events_open"); }

    // Health checks list
    openHealthChecks() { this._doAction("action_view_health_checks"); }

    // Uptime table — open service form
    openService(serviceId) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "hosting.service",
            res_id: serviceId,
            views: [[false, "form"]],
        });
    }

    // Helpers for template
    getStatusClass(status) {
        const map = {
            up: "text-bg-success",
            degraded: "text-bg-warning",
            down: "text-bg-danger",
            timeout: "text-bg-danger",
            unknown: "text-bg-secondary",
        };
        return map[status] || "text-bg-secondary";
    }

    getStatusLabel(status) {
        const map = {
            up: _t("En ligne"),
            degraded: _t("Dégradé"),
            down: _t("En panne"),
            timeout: _t("Délai dépassé"),
            unknown: _t("Inconnu"),
        };
        return map[status] || status;
    }

    getUptimeClass(pct) {
        if (pct >= 99.5) return "text-success";
        if (pct >= 95) return "text-warning";
        return "text-danger";
    }

    formatResponseTime(ms) {
        if (!ms) return "-";
        if (ms < 1000) return ms + " ms";
        return (ms / 1000).toFixed(1) + " s";
    }

    formatStorage(gb) {
        if (!gb || gb <= 0) return "-";
        if (gb >= 1000) return (gb / 1000).toFixed(1) + " To";
        if (gb >= 1) return gb.toFixed(1) + " Go";
        return (gb * 1000).toFixed(0) + " Mo";
    }
}

registry.category("actions").add("hosting_dashboard", HostingDashboard);
