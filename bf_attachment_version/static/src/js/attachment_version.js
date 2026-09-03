/** @odoo-module **/

import { AttachmentList } from "@mail/core/common/attachment_list";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";
import { patch } from "@web/core/utils/patch";

import { onWillStart, onWillUpdateProps, useState } from "@odoo/owl";

/**
 * Un bouton d'historique sur les pièces du chatter, et SEULEMENT sur celles qui
 * ont réellement une version conservée.
 *
 * Le connecteur ONLYOFFICE et celui de Collabora rapiècent déjà ce composant :
 * un troisième bouton posé sur chaque pièce rendrait la carte illisible. Ici le
 * bouton n'apparaît que si le compte est non nul, ce qui est faux pour la quasi
 * totalité des pièces. Le clic ouvre la vue liste du module, qui porte déjà le
 * téléchargement et la restauration.
 */
patch(AttachmentList.prototype, {
    setup() {
        super.setup(...arguments);
        this.bfOrm = useService("orm");
        this.bfAction = useService("action");
        this.bfVersions = useState({ comptes: {} });
        onWillStart(() => this.bfChargerComptesVersions(this.props));
        onWillUpdateProps((props) => this.bfChargerComptesVersions(props));
    },

    async bfChargerComptesVersions(props) {
        const ids = (props.attachments || [])
            .map((piece) => piece.id)
            .filter((id) => typeof id === "number" && id > 0);
        if (!ids.length) {
            this.bfVersions.comptes = {};
            return;
        }
        let lignes = [];
        try {
            // searchRead plutôt que readGroup : le regroupement a changé de
            // signature entre versions d'Odoo, la lecture simple non. Le volume
            // est celui d'un fil de discussion, pas d'un parc.
            lignes = await this.bfOrm.searchRead(
                "bf.attachment.version",
                [["attachment_id", "in", ids]],
                ["attachment_id"]
            );
        } catch {
            // Module absent d'un autre locataire, ou droits refusés : le
            // chatter doit s'afficher quand même.
            this.bfVersions.comptes = {};
            return;
        }
        const comptes = {};
        for (const ligne of lignes) {
            const cle = ligne.attachment_id[0];
            comptes[cle] = (comptes[cle] || 0) + 1;
        }
        this.bfVersions.comptes = comptes;
    },

    bfNombreVersions(attachment) {
        return this.bfVersions.comptes[attachment.id] || 0;
    },

    bfOuvrirVersions(attachment) {
        this.bfAction.doAction({
            type: "ir.actions.act_window",
            name: _t("Versions de %s", attachment.name),
            res_model: "bf.attachment.version",
            views: [
                [false, "list"],
                [false, "form"],
            ],
            domain: [["attachment_id", "=", attachment.id]],
            target: "new",
            context: { create: false, delete: false },
        });
    },
});
