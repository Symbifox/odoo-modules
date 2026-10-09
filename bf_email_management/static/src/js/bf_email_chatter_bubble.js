/** @odoo-module **/

import { Message } from "@mail/core/common/message_model";
import { patch } from "@web/core/utils/patch";

// ---------------------------------------------------------------------------
// Un courriel classé garde sa bulle verte ou bleue
// ---------------------------------------------------------------------------
// Depuis la 18.0.11.38.0, classer un courriel dans un dossier le
// verse en NOTE INTERNE : le geste range une trace, il ne prévient aucun abonné
// et ne montre rien au portail. Mais Odoo tire la couleur du même sous-type : il
// ne dessine la bulle (bleue pour autrui, verte pour soi) que d'un message qui
// n'est pas une note. Les courriels classés s'affichaient donc comme des notes
// tapées à la main, et de plus en plus souvent depuis que le classement en
// note s'est répandu.
//
// On ne touche qu'au dessin : le sous-type reste « Note », donc aucun avis,
// rien au portail, et une réponse depuis la Discussion part encore en note.
// La mention (orangé) garde la priorité, comme chez Odoo.
patch(Message.prototype, {
    get bubbleColor() {
        const color = super.bubbleColor;
        if (color || !this.is_note || !this.message_type?.includes("email")) {
            return color;
        }
        return this.isSelfAuthored ? "green" : "blue";
    },
});
