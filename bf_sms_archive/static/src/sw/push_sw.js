/*
 * Service worker Web Push — messagerie SMS Blue Fox.
 *
 * Servi à /bf_sms_archive/push-sw.js par un contrôleur Odoo. Reçoit les push du
 * service du navigateur (FCM/Mozilla) même onglet fermé, affiche la notification
 * OS, et ouvre/refocalise la messagerie au clic. La vraie URL vient du payload
 * (résolue dynamiquement côté serveur) ; ce repli n'est utilisé qu'à défaut.
 * Volontairement minimal : pas de cache/offline (le SW natif d'Odoo s'en charge).
 */
"use strict";

const FALLBACK_URL = "/odoo";
const DEFAULT_ICON = "/web/static/img/odoo-icon-192x192.png";

self.addEventListener("install", () => {
    self.skipWaiting();
});

self.addEventListener("activate", (event) => {
    event.waitUntil(self.clients.claim());
});

self.addEventListener("push", (event) => {
    let data = {};
    try {
        data = event.data ? event.data.json() : {};
    } catch (e) {
        data = { body: event.data ? event.data.text() : "" };
    }
    const title = data.title || "Nouveau SMS";
    const options = {
        body: data.body || "",
        icon: data.icon || DEFAULT_ICON,
        badge: data.badge || DEFAULT_ICON,
        tag: data.tag || "sms",
        renotify: true,
        data: {
            url: data.url || FALLBACK_URL,
            thread_id: data.thread_id || null,
        },
    };
    event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
    event.notification.close();
    const targetUrl =
        (event.notification.data && event.notification.data.url) || FALLBACK_URL;
    event.waitUntil(
        (async () => {
            const wins = await self.clients.matchAll({
                type: "window",
                includeUncontrolled: true,
            });
            // Refocalise un onglet Odoo déjà ouvert plutôt que d'en ouvrir un.
            for (const win of wins) {
                if (win.url && win.url.includes("/odoo") && "focus" in win) {
                    return win.focus();
                }
            }
            if (self.clients.openWindow) {
                return self.clients.openWindow(targetUrl);
            }
        })()
    );
});
