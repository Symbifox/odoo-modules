/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";

const messageActionsRegistry = registry.category("mail.message/actions");

// Shown on messages that can carry a bf.email mirror: real emails and
// chatter comments (which project when they notified by email) — never
// internal notes or system notifications.
function bfEmailishCondition(component) {
    const message = component.props.message;
    return (
        Boolean(message?.id) &&
        component.store?.self?.isInternalUser &&
        (message.message_type === "email" ||
            (message.message_type === "comment" && !message.is_note))
    );
}

/**
 * ``nextState`` met la pastille d'en-tête à jour tout de suite : le magasin
 * du chatter n'est pas rechargé après un appel de méthode, donc sans ça le
 * message continuerait d'afficher son ancien état jusqu'au prochain
 * rafraîchissement. Pour « Reporter », l'état réel dépend de la date choisie
 * dans l'assistant, donc on ne présume rien.
 */
function bfCallMessageAction(methodName, nextState) {
    return async (component) => {
        const message = component.props.message;
        const result = await component.env.services.orm.call(
            "mail.message",
            methodName,
            [[message.id]]
        );
        if (nextState !== undefined) {
            message.bfEmailState = nextState;
        }
        if (result) {
            component.env.services.action.doAction(result);
        }
    };
}

// L'état du miroir bf.email de l'usager courant, joint à chaque message par
// mail.message._to_store : "inbox", "handled", "snoozed", ou faux quand aucun
// miroir n'existe encore. Il pilote la pastille d'en-tête (voir
// bf_email_chatter_badge.xml) et, ici, quel bouton mérite d'être proposé.
function bfEmailState(component) {
    return component.props.message?.bfEmailState;
}

// ---------------------------------------------------------------------------
// « Répondre à tous »
// ---------------------------------------------------------------------------
// Le chatter offrait déjà « Reply », celui de mail_quoted_reply, qui n'adresse
// que l'expéditeur d'origine. Répondre à un fil de quatre personnes obligeait
// donc à retaper trois adresses, ou à quitter Odoo pour son client courriel.
//
// L'entrée voisine s'appelle « Reply » en toutes lettres, même en français :
// mail_quoted_reply passe une chaîne nue, sans `_t()`, donc aucune traduction
// ne s'y applique. On la ré-enregistre avec un titre traduisible et une place
// fixe, sinon « Répondre à tous » se retrouve à côté d'un mot anglais et le
// menu a l'air à moitié fini. `sequence` manquait aussi : la comparaison
// `a.sequence - b.sequence` rend NaN et l'ordre devenait celui de la
// déclaration.
if (messageActionsRegistry.contains("reply")) {
    const reply = messageActionsRegistry.get("reply");
    messageActionsRegistry.add(
        "reply",
        { ...reply, title: _t("Répondre"), sequence: 20 },
        { force: true }
    );
}

messageActionsRegistry.add("bf-email-reply-all", {
    // Une réponse a besoin d'une fiche où se poser : sans `res_id`, le
    // composeur n'a pas de fil. Les messages de la boîte de réception
    // (mail.box) n'en ont pas.
    condition: (component) =>
        bfEmailishCondition(component) && Boolean(component.props.message.res_id),
    icon: "fa fa-reply-all",
    title: _t("Répondre à tous"),
    onClick: bfCallMessageAction("action_bf_reply_all"),
    sequence: 21,
});

messageActionsRegistry.add("bf-email-mark-handled", {
    // Un courriel déjà sorti de la boîte n'a pas besoin qu'on le retraite.
    // L'état inconnu (faux) reste offert : le miroir sera créé au clic.
    condition: (component) =>
        bfEmailishCondition(component) &&
        !["handled", "snoozed"].includes(bfEmailState(component)),
    icon: "fa fa-check",
    title: _t("Marquer traité (courriels)"),
    onClick: bfCallMessageAction("action_bf_mark_handled", "handled"),
    sequence: 76,
});

messageActionsRegistry.add("bf-email-snooze", {
    condition: (component) =>
        bfEmailishCondition(component) && bfEmailState(component) !== "snoozed",
    icon: "fa fa-clock-o",
    title: _t("Reporter (courriels)"),
    onClick: bfCallMessageAction("action_bf_snooze"),
    sequence: 77,
});

messageActionsRegistry.add("bf-email-unhandle", {
    // Rien à remettre en boîte tant que le courriel y est déjà, et rien du
    // tout quand aucun miroir n'existe.
    condition: (component) =>
        bfEmailishCondition(component) &&
        ["handled", "snoozed"].includes(bfEmailState(component)),
    icon: "fa fa-inbox",
    title: _t("Remettre en boîte (courriels)"),
    onClick: bfCallMessageAction("action_bf_unhandle", "inbox"),
    sequence: 78,
});

messageActionsRegistry.add("bf-download-eml", {
    condition: (component) => {
        const message = component.props.message;
        return Boolean(message?.id) && component.store?.self?.isInternalUser;
    },
    icon: "fa fa-download",
    title: _t("Télécharger en .eml"),
    onClick: async (component) => {
        const message = component.props.message;
        const result = await component.env.services.orm.call(
            "mail.message",
            "action_download_eml",
            [[message.id]]
        );
        if (result) {
            component.env.services.action.doAction(result);
        }
    },
    sequence: 80,
});
