/** @odoo-module **/

import { Component, useState, onWillStart } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

export class GamificationDashboard extends Component {
    static template = "bf_gamification.Dashboard";
    static props = { "*": true };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.state = useState({
            loading: true,
            data: null,
            activeTab: "overview",
            achievementFilter: "all",
        });

        onWillStart(async () => {
            await this.loadData();
        });
    }

    async loadData() {
        this.state.loading = true;
        try {
            this.state.data = await this.orm.call(
                "bf.gamification.dashboard",
                "get_dashboard_data",
                []
            );
        } catch (e) {
            this.notification.add("Erreur lors du chargement du tableau de bord", {
                type: "danger",
            });
        }
        this.state.loading = false;
    }

    async refresh() {
        await this.loadData();
    }

    showOverview() {
        this.state.activeTab = "overview";
    }

    showAchievements() {
        this.state.activeTab = "achievements";
    }

    filterAll() {
        this.state.achievementFilter = "all";
    }

    filterEarned() {
        this.state.achievementFilter = "earned";
    }

    filterLocked() {
        this.state.achievementFilter = "locked";
    }

    getFilteredCategories() {
        if (!this.state.data || !this.state.data.achievements) return [];
        const filter = this.state.achievementFilter;
        return this.state.data.achievements.categories.map((cat) => {
            let badges = cat.badges;
            if (filter === "earned") {
                badges = badges.filter((b) => b.earned);
            } else if (filter === "locked") {
                badges = badges.filter((b) => !b.earned);
            }
            return { ...cat, badges };
        }).filter((cat) => cat.badges.length > 0);
    }

    async toggleShowcase(userBadgeId) {
        try {
            const result = await this.orm.call(
                "bf.gamification.dashboard",
                "toggle_showcase_badge",
                [userBadgeId]
            );
            if (result.error) {
                this.notification.add(result.error, { type: "warning" });
                return;
            }
            await this.loadData();
        } catch (e) {
            this.notification.add("Erreur lors de la mise \u00e0 jour de la vitrine", {
                type: "danger",
            });
        }
    }

    openLeaderboard() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Classement",
            res_model: "bf.gamification.profile",
            views: [[false, "list"], [false, "form"]],
            domain: [],
        });
    }

    openMyBadges() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Mes badges",
            res_model: "bf.gamification.user.badge",
            views: [[false, "list"]],
            domain: [],
            context: { search_default_my_badges: 1 },
        });
    }

    openXpJournal() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Journal XP",
            res_model: "bf.gamification.xp.transaction",
            views: [[false, "list"]],
            domain: [],
            context: { search_default_my_xp: 1 },
        });
    }

    openRewardCatalog() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Catalogue",
            res_model: "bf.gamification.reward",
            views: [[false, "kanban"], [false, "list"], [false, "form"]],
            domain: [["available", "=", true]],
        });
    }

    getSourceIcon(source) {
        const icons = {
            timesheet: "fa-clock-o",
            task: "fa-check-square-o",
            document: "fa-file-text-o",
            badge: "fa-trophy",
            streak: "fa-fire",
            manual: "fa-hand-paper-o",
            hosting: "fa-server",
            reward: "fa-gift",
        };
        return icons[source] || "fa-star";
    }

    getSourceLabel(source) {
        const labels = {
            timesheet: "Feuille de temps",
            task: "T\u00e2che",
            document: "Document",
            badge: "Badge",
            streak: "Streak",
            manual: "Manuel",
            hosting: "H\u00e9bergement",
            reward: "R\u00e9compense",
        };
        return labels[source] || source;
    }

    getRarityLabel(rarity) {
        const labels = {
            common: "Commun",
            uncommon: "Peu commun",
            rare: "Rare",
            epic: "\u00c9pique",
            legendary: "L\u00e9gendaire",
        };
        return labels[rarity] || rarity;
    }

    getRarityClass(rarity) {
        const classes = {
            common: "bf-rarity-common",
            uncommon: "bf-rarity-uncommon",
            rare: "bf-rarity-rare",
            epic: "bf-rarity-epic",
            legendary: "bf-rarity-legendary",
        };
        return classes[rarity] || "bf-rarity-common";
    }

    formatDate(dateStr) {
        if (!dateStr) return "";
        const d = new Date(dateStr);
        return d.toLocaleDateString("fr-CA", {
            day: "numeric",
            month: "short",
        });
    }
}

registry.category("actions").add("bf_gamification_dashboard", GamificationDashboard);
