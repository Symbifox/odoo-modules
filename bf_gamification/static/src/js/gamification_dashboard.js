/** @odoo-module **/

import { Component, useState, useEffect, onWillStart } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

export class GamificationDashboard extends Component {
    static template = "bf_gamification.Dashboard";
    static props = { "*": true };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        // Un défi accompli met l'écran à jour sans le faire clignoter.
        this.busState = useState(useService("bf_gamification_bus").state);
        this.state = useState({
            loading: true,
            data: null,
            activeTab: "overview",
            achievementFilter: "all",
        });

        onWillStart(async () => {
            await this.loadData();
        });

        // Un rechargement par défi accompli, jamais au montage (le tick d'une page précédente).
        this.seenTick = this.busState.challengeTick;
        useEffect(
            (tick) => {
                if (tick !== this.seenTick) {
                    this.seenTick = tick;
                    this.reloadQuietly();
                }
            },
            () => [this.busState.challengeTick]
        );
    }

    async reloadQuietly() {
        try {
            this.state.data = await this.orm.call(
                "bf.gamification.dashboard",
                "get_dashboard_data",
                []
            );
        } catch (e) {
            // L'écran déjà affiché reste tel quel.
        }
    }

    firstName() {
        const name = (this.state.data && this.state.data.profile && this.state.data.profile.user_name) || "";
        return name.split(" ")[0];
    }

    stampSlots() {
        const ch = this.state.data && this.state.data.challenges;
        if (!ch) return [];
        return Array.from({ length: ch.per_card }, (_, n) => ({ n, on: n < ch.stamps }));
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

    earnedBadges() {
        // Les badges obtenus : chacun peut devenir le titre porté.
        const data = this.state.data;
        if (!data || !data.achievements) return [];
        const earned = [];
        for (const category of data.achievements.categories) {
            for (const badge of category.badges) {
                if (badge.earned) earned.push({ id: badge.id, name: badge.name });
            }
        }
        // Le titre déjà porté reste dans la liste même si son badge est archivé depuis.
        const worn = data.profile && data.profile.worn_title;
        if (worn && !earned.some((badge) => badge.id === worn.badge_id)) {
            earned.push({ id: worn.badge_id, name: worn.name });
        }
        return earned.sort((a, b) => a.name.localeCompare(b.name));
    }

    async setWornTitle(ev) {
        const badgeId = ev.target.value ? parseInt(ev.target.value, 10) : false;
        try {
            await this.orm.call("bf.gamification.dashboard", "set_worn_title", [badgeId]);
            this.notification.add(
                badgeId ? "Titre mis \u00e0 jour." : "Titre retir\u00e9.",
                { type: "success" }
            );
        } catch (e) {
            this.notification.add(e.data?.message || "Erreur lors du changement de titre", {
                type: "danger",
            });
        }
        await this.reloadQuietly();
    }

    async setLeaveOptIn(flag) {
        try {
            await this.orm.call("bf.gamification.dashboard", "set_leave_opt_in", [flag]);
            this.notification.add(
                flag ? "Participation activ\u00e9e : vos jours ouvrables comptent \u00e0 partir d'aujourd'hui."
                     : "Participation retir\u00e9e.",
                { type: "success" }
            );
        } catch (e) {
            this.notification.add(e.data?.message || "Erreur lors de la mise \u00e0 jour de la participation", {
                type: "danger",
            });
        }
        await this.loadData();
    }

    openLeaderboard() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Classement",
            res_model: "bf.gamification.profile",
            views: [[false, "list"], [false, "form"]],
            domain: [
                ["user_id.share", "=", false],
                ["user_id.bf_gamification_excluded", "=", false],
                ["user_id", "!=", 1],
            ],
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
            on_time: "fa-clock-o",
            caught_up: "fa-check-circle-o",
            challenge: "fa-paw",
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
            on_time: "\u00c0 temps",
            caught_up: "\u00c0 jour",
            challenge: "D\u00e9fi du jour",
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
