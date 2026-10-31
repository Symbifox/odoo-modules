/** @odoo-module **/

import { browser } from "@web/core/browser/browser";
import { ConnectionLostError, rpc } from "@web/core/network/rpc";
import { registry } from "@web/core/registry";
import { reactive } from "@odoo/owl";

/**
 * Une fête ne se montre qu'une fois.
 *
 * Le bus porte chaque message à tous les onglets ouverts de la personne. Une fête
 * attend donc que son onglet soit regardé (visible et au premier plan), puis
 * réclame son jeton au serveur : seul le premier onglet à le réclamer la montre.
 * Un onglet en arrière-plan ne la garde plus en réserve pour le retour de la
 * personne. Message sans jeton (serveur d'avant 18.0.3.2.0) : montré tout de suite.
 * Réclamation coupée par le réseau : une fête toute fraîche se montre (mieux deux fois
 * que jamais) ; une plus vieille réessaie au prochain retour sur l'onglet, pour ne pas
 * ressortir un renard déjà vu ailleurs. Toute autre erreur (session expirée…) : rien.
 */
const FRESH_MS = 2 * 60 * 1000;

export function showOnce(claim, show) {
    if (!claim) {
        show();
        return;
    }
    const receivedAt = Date.now();
    let busy = false;
    const listen = (on) => {
        const method = on ? "addEventListener" : "removeEventListener";
        browser[method]("focus", attempt);
        document[method]("visibilitychange", attempt);
    };
    async function attempt() {
        if (busy || document.visibilityState !== "visible" || !document.hasFocus()) {
            return;
        }
        busy = true;
        listen(false);
        let mine = false;
        try {
            mine = await rpc("/bf_gamification/celebration/claim", { token: claim }, { silent: true });
        } catch (error) {
            if (!(error instanceof ConnectionLostError)) {
                return;
            }
            if (Date.now() - receivedAt > FRESH_MS) {
                busy = false;
                listen(true);
                return;
            }
            mine = true;
        }
        if (mine) {
            show();
        }
    }
    listen(true);
    attempt();
}

export const gamificationBusService = {
    dependencies: ["bus_service", "notification", "effect"],

    start(env, { bus_service, notification, effect }) {
        const state = reactive({
            pendingBadge: null,
            pendingLevelUp: null,
            challengeTick: 0,
        });

        bus_service.subscribe("bf_gamification/badge_earned", (payload) => {
            showOnce(payload.claim, () => {
                state.pendingBadge = payload;
                notification.add(
                    `Badge obtenu : ${payload.badge_name} (+${payload.xp_reward} XP)`,
                    { type: "success", sticky: false }
                );
            });
        });

        // La fenêtre de niveau ne se ferme qu'à la main : sans `showOnce`, il fallait
        // la fermer dans chaque onglet.
        bus_service.subscribe("bf_gamification/level_up", (payload) => {
            showOnce(payload.claim, () => {
                state.pendingLevelUp = payload;
                notification.add(
                    `Niveau supérieur ! ${payload.new_level.title}`,
                    { type: "success", sticky: false }
                );
            });
        });

        // Une liste de travail vient de se vider. L'effet « rainbow man »
        // du cœur, celui de la fin d'un rapprochement bancaire, avec un renard.
        // Si l'usager a coupé les effets d'Odoo, le cœur en fait une notification.
        // L'effet ne se retire qu'à la fin d'une animation CSS, qui ne tourne pas dans un
        // onglet caché : sans `showOnce`, le renard y attendait le retour de la personne.
        bus_service.subscribe("bf_gamification/caught_up", (payload) => {
            showOnce(payload.claim, () =>
                effect.add({
                    type: "rainbow_man",
                    message: payload.message,
                    img_url: payload.img_url,
                    fadeout: payload.big ? "slow" : "medium",
                })
            );
        });

        // Un défi du jour accompli : un petit mot du renard ; la carte complète, elle, a sa fête.
        bus_service.subscribe("bf_gamification/challenge_done", (payload) => {
            state.challengeTick += 1;
            if (payload.card_complete) {
                showOnce(payload.claim, () =>
                    effect.add({
                        type: "rainbow_man",
                        message: payload.message,
                        img_url: payload.img_url,
                        fadeout: "slow",
                    })
                );
            } else {
                notification.add(
                    `${payload.message} +${payload.xp} XP \u00b7 tampons ${payload.stamps}/${payload.per_card}`,
                    { type: "success", sticky: false }
                );
            }
        });

        bus_service.subscribe("bf_gamification/xp_gained", (payload) => {
            notification.add(
                `+${payload.amount} XP : ${payload.description}`,
                { type: "info", sticky: false }
            );
        });

        return {
            get state() { return state; },
            consumeBadge() {
                const badge = state.pendingBadge;
                state.pendingBadge = null;
                return badge;
            },
            consumeLevelUp() {
                const levelUp = state.pendingLevelUp;
                state.pendingLevelUp = null;
                return levelUp;
            },
        };
    },
};

registry.category("services").add("bf_gamification_bus", gamificationBusService);
