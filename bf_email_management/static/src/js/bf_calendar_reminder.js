/** @odoo-module **/

/*
 * Blue Fox calendar reminder — replaces the standard `calendarNotification`
 * service so the popup buttons (Snooze 5/15/60min/Demain/Custom, Dismiss,
 * Détails) hit real server endpoints (`bf_snooze` / `bf_dismiss` on
 * `calendar.attendee`) instead of merely closing the toast.
 *
 * The bus.bus channel `calendar.alarm` is unchanged; only this client-side
 * receiver is overridden, and the standard service is replaced (force: true).
 */

import { _t } from "@web/core/l10n/translation";
import { browser } from "@web/core/browser/browser";
import { Dialog } from "@web/core/dialog/dialog";
import { ConnectionLostError, rpc } from "@web/core/network/rpc";
import { registry } from "@web/core/registry";
import { Component, useState, xml } from "@odoo/owl";

// Le toast ne fait que 400 px de large pour sept boutons : sans mise en
// forme, le gabarit standard les comprime sous la largeur de leur
// libellé, que son `overflow-wrap` coupe alors en plein mot (« 5 min »
// s'affichait sur trois lignes). C'est `bf_calendar_reminder.scss` qui
// garde chaque libellé d'un seul tenant et fait passer la rangée à la
// ligne ; les espaces insécables essayés ici avant lui n'y changeaient
// rien. Garder les libellés courts quand même.
const SNOOZE_PRESETS = [
    { label: _t("5 min"), minutes: 5 },
    { label: _t("15 min"), minutes: 15 },
    { label: _t("1 h"), minutes: 60 },
    { label: _t("Demain"), minutes: null, kind: "tomorrow_8" },
];

// `bus.bus` rejoue jusqu'à 24 h de messages à la reconnexion : `_poll` avec un
// `last` filtre sur `id > last` sans aucune borne de date, et le client garde
// `last_notification_id` en localStorage, donc il survit à la fermeture du
// navigateur. Un portable rouvert se faisait déverser les rappels de la
// veille, chacun avec un `timer` déjà négatif, que `setTimeout` ramène à zéro
// et affiche donc sur-le-champ. Mesuré le 2026-08-31 : `timer: -1011`.
//
// On date donc chaque charge utile à l'horloge du serveur (`sent_ms`, posé par
// `do_notif_reminder`) et on jette celles qui ont trop dormi. Deux minutes :
// large pour une poussée vivante, court devant les 24 h du rejeu.
//
// ⚠️ Contrepartie : un poste dont l'horloge AVANCE de plus de deux minutes sur
// le serveur ne verrait plus aucun rappel arrivé par le bus, sans erreur ni
// trace. D'où le compteur plus bas, qui finit par le dire tout haut. Le
// sondage direct `/calendar/notify` n'est jamais filtré, donc même dans ce cas
// les rappels continuent d'arriver à l'ouverture de la session.
const BUS_REPLAY_TTL_MS = 120000;

function tomorrow8AmIso() {
    const d = new Date();
    d.setDate(d.getDate() + 1);
    d.setHours(8, 0, 0, 0);
    // Convert to UTC ISO without ms, format Odoo expects: YYYY-MM-DD HH:MM:SS
    const pad = (n) => String(n).padStart(2, "0");
    return (
        `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ` +
        `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}`
    );
}

// `browser` n'expose PAS `prompt` : la liste de @web/core/browser/browser est
// explicite et s'arrête aux méthodes qu'un test a besoin de remplacer. Le
// bouton « Autre… » levait donc un TypeError à chaque clic depuis sa mise en
// service — il n'a jamais fonctionné, tâche #25195. `window.prompt` réparerait
// l'appel, mais il gèle le fil d'exécution — donc le bus — tant que la boîte
// native est ouverte, et il ignore le thème.
//
// D'où ce dialogue, sur le patron de `NcPromptDialog` (bf_nextcloud_browser).
// Son gabarit est écrit ici plutôt que dans un .xml d'assets parce qu'ajouter
// un fichier au manifeste oblige à redémarrer le conteneur AVANT la montée
// (le manifeste est en cache mémoire) et que ce module est installé chez onze
// locataires ; un changement de contenu d'un fichier déjà listé se contente
// d'un rechargement de page. Les libellés arrivent en props, traduits par
// l'appelant : un gabarit en ligne n'est pas extrait par `_t`.
export class SnoozeOtherDialog extends Component {
    static template = xml`
        <Dialog title="props.title" size="'sm'">
            <div>
                <label class="form-label" for="bf_snooze_other_minutes" t-esc="props.label"/>
                <input id="bf_snooze_other_minutes" type="number" min="1" step="1"
                       class="form-control" t-model="state.value"
                       t-on-keydown="onKeydown" autofocus="autofocus"/>
            </div>
            <t t-set-slot="footer">
                <button class="btn btn-primary" t-att-disabled="!minutes"
                        t-on-click="confirm" t-esc="props.confirmLabel"/>
                <button class="btn btn-secondary" t-on-click="() => props.close()"
                        t-esc="props.cancelLabel"/>
            </t>
        </Dialog>`;
    static components = { Dialog };
    static props = {
        close: Function,
        title: String,
        label: String,
        confirmLabel: String,
        cancelLabel: String,
        onConfirm: Function,
    };

    setup() {
        this.state = useState({ value: "30" });
    }

    // Un `<input type="number">` rend "" pour toute saisie qu'il juge invalide,
    // donc il ne reste ici qu'à écarter le vide, le zéro et le négatif.
    get minutes() {
        const n = parseInt(this.state.value, 10);
        return Number.isFinite(n) && n > 0 ? n : 0;
    }

    confirm() {
        const minutes = this.minutes;
        if (!minutes) {
            return;
        }
        this.props.close();
        this.props.onConfirm(minutes);
    }

    onKeydown(ev) {
        if (ev.key === "Enter") {
            ev.preventDefault();
            this.confirm();
        }
    }
}

export const bfCalendarNotificationService = {
    dependencies: ["action", "bus_service", "dialog", "notification", "orm"],

    start(env, { action, bus_service, dialog, notification, orm }) {
        let calendarNotifTimeouts = {};
        let nextCalendarNotifTimeout = null;
        let staleBusNotifs = 0;
        let everDisplayed = false;
        // Clé "<event_id>,<alarm_id>" -> fonction qui retire le toast. Une
        // Map plutôt qu'un Set : il faut pouvoir fermer un rappel décidé
        // ailleurs, pas seulement savoir qu'il est affiché.
        const displayedNotifications = new Map();

        bus_service.subscribe("calendar.alarm", (payload) => {
            displayCalendarNotification(payload, { fromBus: true });
        });

        // Un rappel reporté ou marqué vu dans une autre fenêtre doit
        // disparaître ici aussi : sans ça, chaque onglet garde son toast et
        // réclame un geste déjà posé. Le serveur diffuse sur le canal du
        // participant (voir `_bf_broadcast_reminder_closed`), donc l'onglet
        // d'origine reçoit son propre message — retirer un toast déjà retiré
        // ne fait rien.
        bus_service.subscribe("bf_calendar_reminder/close", (payload) => {
            const eventId = payload && payload.event_id;
            if (!eventId) {
                return;
            }
            const prefix = `${eventId},`;
            for (const [key, remove] of [...displayedNotifications]) {
                if (key.startsWith(prefix)) {
                    remove();
                }
            }
        });
        bus_service.start();

        // On service start, proactively pull any pending alarms via
        // /calendar/notify. Without this, refreshing the page when an
        // alarm has already fired (but is still pending — not snoozed
        // or dismissed) silently drops it: bus.bus only re-emits when
        // _notify_next_alarm is called server-side, not on client
        // connect.
        getNextCalendarNotif();

        // Un seul chemin pour les cinq reports : préréglage et valeur libre ne
        // diffèrent que par les kwargs.
        async function snooze(eventId, kwargs, notificationRemove) {
            try {
                await orm.call("calendar.attendee", "bf_snooze", [eventId], kwargs);
            } catch (e) {
                notification.add(_t("Snooze a échoué"), { type: "danger" });
                throw e;
            }
            notificationRemove();
        }

        function buildSnoozeButtons(notif, notificationRemove) {
            const buttons = SNOOZE_PRESETS.map((preset) => ({
                name: preset.label,
                onClick: () =>
                    snooze(
                        notif.event_id,
                        preset.kind === "tomorrow_8"
                            ? { until: tomorrow8AmIso() }
                            : { minutes: preset.minutes },
                        notificationRemove
                    ),
            }));
            buttons.push({
                name: _t("Autre…"),
                onClick: () => {
                    dialog.add(SnoozeOtherDialog, {
                        title: _t("Reporter le rappel"),
                        label: _t("Reporter de combien de minutes ?"),
                        confirmLabel: _t("Reporter"),
                        cancelLabel: _t("Annuler"),
                        // Le dialogue s'est déjà fermé : l'erreur ne remonte
                        // plus à un gestionnaire d'événement, donc la retenir
                        // ici — le toast rouge l'a dite — plutôt que de laisser
                        // une promesse non traitée rouvrir la boîte de
                        // plantage, celle-là même qui a ouvert #25195.
                        onConfirm: (minutes) =>
                            snooze(
                                notif.event_id,
                                { minutes },
                                notificationRemove
                            ).catch((e) => console.error("[bf_calendar_reminder]", e)),
                    });
                },
            });
            buttons.push({
                // `primary` est le seul levier d'apparence que le gabarit
                // standard expose par bouton ; il porte le bleu de la marque
                // sur le geste le plus fréquent. Les reports passent en
                // contour par la feuille de style, qui les reconnaît à leur
                // classe grise (voir bf_calendar_reminder.scss).
                name: _t("Vu"),
                primary: true,
                onClick: async () => {
                    await orm.call("calendar.attendee", "bf_dismiss", [notif.event_id]);
                    await rpc("/calendar/notify_ack");
                    notificationRemove();
                },
            });
            buttons.push({
                name: _t("Ouvrir"),
                onClick: async () => {
                    await action.doAction({
                        type: "ir.actions.act_window",
                        res_model: "calendar.event",
                        res_id: notif.event_id,
                        views: [[false, "form"]],
                    });
                    notificationRemove();
                },
            });
            return buttons;
        }

        function isStaleBusNotif(notif) {
            if (typeof notif.sent_ms !== "number") {
                return false; // serveur pas encore à jour : on ne jette rien
            }
            return Date.now() - notif.sent_ms > BUS_REPLAY_TTL_MS;
        }

        function displayCalendarNotification(notifications, { fromBus = false } = {}) {
            let lastNotifTimer = 0;

            if (fromBus) {
                const fresh = notifications.filter((n) => !isStaleBusNotif(n));
                staleBusNotifs += notifications.length - fresh.length;
                if (staleBusNotifs >= 5 && !everDisplayed) {
                    console.warn(
                        `[bf_calendar_reminder] ${staleBusNotifs} rappels écartés ` +
                        "comme périmés et aucun affiché. Horloge du poste en avance " +
                        "sur celle du serveur ?"
                    );
                }
                if (!fresh.length) {
                    // Une poussée entièrement périmée n'apprend rien : garder
                    // l'horaire déjà armé plutôt que le remplacer par du vide.
                    return;
                }
                notifications = fresh;
            }

            browser.clearTimeout(nextCalendarNotifTimeout);
            Object.values(calendarNotifTimeouts).forEach((id) => browser.clearTimeout(id));
            calendarNotifTimeouts = {};

            notifications.forEach(function (notif) {
                const key = notif.event_id + "," + notif.alarm_id;
                if (displayedNotifications.has(key)) {
                    return;
                }
                calendarNotifTimeouts[key] = browser.setTimeout(function () {
                    let notificationRemove = () => {};
                    notificationRemove = notification.add(notif.message, {
                        title: notif.title,
                        type: "warning",
                        sticky: true,
                        // Cible de `bf_calendar_reminder.scss` ; sans elle,
                        // la mise en forme toucherait tous les toasts.
                        className: "o_bf_calendar_reminder",
                        onClose: () => {
                            displayedNotifications.delete(key);
                        },
                        buttons: buildSnoozeButtons(notif, () => notificationRemove()),
                    });
                    displayedNotifications.set(key, () => notificationRemove());
                    everDisplayed = true;
                }, notif.timer * 1000);
                lastNotifTimer = Math.max(lastNotifTimer, notif.timer);
            });

            if (lastNotifTimer > 0) {
                nextCalendarNotifTimeout = browser.setTimeout(
                    getNextCalendarNotif,
                    lastNotifTimer * 1000
                );
            }
        }

        async function getNextCalendarNotif() {
            try {
                const result = await rpc("/calendar/notify", {}, { silent: true });
                displayCalendarNotification(result);
            } catch (error) {
                if (!(error instanceof ConnectionLostError)) {
                    throw error;
                }
            }
        }
    },
};

// Replace the standard service with ours (Odoo registry supports force overwrite).
registry
    .category("services")
    .add("calendarNotification", bfCalendarNotificationService, { force: true });
