/** @odoo-module **/

import { AttachmentList } from "@mail/core/common/attachment_list";
import { useService } from "@web/core/utils/hooks";
import { patch } from "@web/core/utils/patch";

import { onWillStart, onWillUpdateProps, useState } from "@odoo/owl";

/**
 * Le bouton « Modifier dans Collabora » ne doit apparaître que si la personne
 * peut vraiment écrire la pièce.
 *
 * Amont, `canWrite` est `async` : le `t-if` du gabarit reçoit une PROMESSE,
 * toujours vraie, donc le bouton s'affiche toujours. Et même sans ça, le côté
 * serveur rend une chaîne JSON là où le client lit `result?.can_write`, donc la
 * réponse serait `undefined`. Deux défauts qui se compensent en apparence : le
 * bouton s'affiche, et il n'aurait pas dû.
 *
 * Ce n'est pas une faille. Le contrôleur `/collabora_odoo/frame/<id>/write`
 * revérifie `has_access('write')` et retombe en lecture seule. C'est un bouton
 * qui promet ce qu'il ne tiendra pas.
 *
 * Le correctif précharge les droits de toutes les pièces du fil en un seul
 * appel, et rend `canWrite` synchrone. L'ordre des greffes est garanti par la
 * dépendance du module : nos actifs se chargent après ceux de l'amont.
 */
patch(AttachmentList.prototype, {
    setup() {
        super.setup(...arguments);
        this.bfCoolOrm = useService("orm");
        this.bfCool = useState({ modifiables: {} });
        onWillStart(() => this.bfCoolChargerDroits(this.props));
        onWillUpdateProps((props) => this.bfCoolChargerDroits(props));
    },

    async bfCoolChargerDroits(props) {
        const ids = (props.attachments || [])
            .filter((piece) => this.isCoolAttachment(piece))
            .map((piece) => piece.id)
            .filter((id) => typeof id === "number" && id > 0);
        if (!ids.length) {
            this.bfCool.modifiables = {};
            return;
        }
        let permises = [];
        try {
            permises = await this.bfCoolOrm.call(
                "bf.collabora.helper", "pieces_modifiables", [ids]);
        } catch {
            // Un appel refusé ne doit pas empêcher le chatter de s'afficher :
            // aucun bouton d'édition, ce qui est le repli sûr.
            this.bfCool.modifiables = {};
            return;
        }
        const table = {};
        for (const id of permises) {
            table[id] = true;
        }
        this.bfCool.modifiables = table;
    },

    /** Remplace la version `async` de l'amont : le gabarit a besoin d'un booléen. */
    canWrite(attachment) {
        if (!attachment || !this.isCoolAttachment(attachment)) {
            return false;
        }
        return Boolean(this.bfCool.modifiables[attachment.id]);
    },
});
