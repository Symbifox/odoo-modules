"""Rattraper au routage les réponses qui visent la boîte plutôt qu'un dossier.

``bf.email`` hérite de ``mail.thread`` : la passerelle sait donc déposer un
message sur une rangée de la boîte, et elle le fait dès qu'un correspondant
répond à un envoi parti de la boîte avant que la rangée soit classée — son
Message-ID porte alors ``openerp-<id>-bf.email``. Le fil quitte le dossier, et
il n'y revient pas tout seul : chaque réponse suivante cite le même en-tête.

``bf.email._composer_target`` empêche les nouveaux cas. Cette garde-ci sert les
fils DÉJÀ partis : le Message-ID est chez le correspondant, on ne peut pas le
reprendre, mais on peut lire où la rangée visée est classée et y rediriger le
message. Une réécriture de route, pas un second envoi.
"""

import logging

from odoo import api, models

_logger = logging.getLogger(__name__)


class MailThread(models.AbstractModel):
    _inherit = "mail.thread"

    # ------------------------------------------------------------------
    # Signature : posée ici, jamais dans le corps
    # ------------------------------------------------------------------
    def _notify_by_email_prepare_rendering_context(
            self, message, msg_vals=False, model_description=False,
            force_email_company=False, force_email_lang=False):
        """Signer de l'identité qui expédie, quand elle a sa propre signature.

        Odoo signe de ``res.users.signature`` : une personne, une signature.
        Ce module lui donne plusieurs adresses d'envoi vérifiées, chacune
        pouvant porter la sienne. L'identité est résolue par l'adresse du
        « De » du message — le composeur, transitoire, n'existe plus à
        l'instant du rendu, et le « De » est de toute façon ce que le
        destinataire lit.

        ⚠️ C'est le SEUL endroit où la signature entre dans un courriel de ce
        module. Le corps ne la porte jamais : ni le composeur « Nouveau
        courriel », ni la citation d'une réponse, ni l'entête d'un transfert,
        ni l'application mobile. Un corps qui la porterait la ferait sortir
        deux fois, puisque le gabarit de notification l'ajoute ici de toute
        façon.

        On ne touche à rien quand l'identité n'a pas de signature propre : la
        valeur calculée plus haut reste, y compris celle qu'un module de
        signature multi-sociétés substituerait quand le message parle pour une
        autre société que celle de son auteur.
        """
        values = super()._notify_by_email_prepare_rendering_context(
            message,
            msg_vals=msg_vals,
            model_description=model_description,
            force_email_company=force_email_company,
            force_email_lang=force_email_lang,
        )
        if not values.get("email_add_signature"):
            return values
        author_user = values.get("author_user")
        if not author_user or author_user.share:
            return values
        email_from = (msg_vals or {}).get("email_from") or message.email_from
        identity = self.env["bf.email.identity"].sudo()._for_sender(
            email_from, author_user)
        if identity and (identity.signature_html or "").strip():
            values["signature"] = identity.signature_html
        return values

    @api.model
    def message_route(self, message, message_dict, model=None,
                      thread_id=None, custom_values=None):
        routes = super().message_route(
            message, message_dict, model=model, thread_id=thread_id,
            custom_values=custom_values,
        )
        return [self._bf_email_redirect_route(route) for route in routes]

    @api.model
    def _bf_email_redirect_route(self, route):
        """Réécrire une route qui vise la boîte vers le dossier du fil.

        ``route`` est le 5-uplet d'Odoo ``(modèle, id, valeurs, usager,
        alias)``. Seuls les deux premiers changent : l'alias et les valeurs
        par défaut que la passerelle a choisis restent ceux qu'elle a choisis.
        """
        if not isinstance(route, (list, tuple)) or len(route) < 2:
            return route
        if route[0] != "bf.email" or not route[1]:
            return route
        row = self.env["bf.email"].sudo().browse(route[1]).exists()
        if not row:
            return route
        target = row._filing_target()
        if not target:
            return route
        _logger.info(
            "bf.email : message routé sur la rangée #%s redirigé vers %s/%s",
            row.id, target[0], target[1],
        )
        return (target[0], target[1]) + tuple(route[2:])
