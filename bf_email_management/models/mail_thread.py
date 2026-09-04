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

from markupsafe import Markup

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

        ⚠️ C'est aussi ici que se décide s'il faut en poser une : quand le
        corps en porte DÉJÀ une — mode « brouillon », le défaut — le marqueur
        ``data-bf-signature`` le dit, et le gabarit n'en ajoute pas une
        seconde. C'est la seule garde qui tienne quel que soit le chemin
        d'envoi ; sans elle le destinataire en reçoit deux.

        On ne touche à rien quand l'identité n'a pas de signature propre : la
        valeur calculée plus haut reste, y compris celle que
        ``bf_multi_company_email`` substitue quand le message parle pour une
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
        # Le corps porte déjà sa signature : ne pas en ajouter une deuxième.
        corps = (msg_vals or {}).get("body") or message.body or ""
        if self.env["bf.email"].SIGNATURE_MARKER in corps:
            values["email_add_signature"] = False
            return values
        author_user = values.get("author_user")
        if not author_user or author_user.share:
            return values
        email_from = (msg_vals or {}).get("email_from") or message.email_from
        identity = self.env["bf.email.identity"].sudo()._for_sender(
            email_from, author_user)
        if not identity:
            return values
        # Même cascade que le brouillon et que l'aperçu du composeur : la
        # signature de l'identité, puis celle de la société de son COMPTE,
        # puis rien (Odoo pose alors `res.users.signature`). Sans le deuxième
        # temps, une identité d'une autre société sans signature propre
        # repartait signée de la société principale de la personne.
        signature = self.env["bf.email"].with_user(
            author_user)._signature_for_identity(identity)
        if (signature or "").strip():
            values["signature"] = signature
        return values

    # Ce qui ouvre une citation, dans les trois chemins du module : la réponse
    # depuis la boîte et le bouton Répondre du chatter posent un
    # ``<blockquote>``, le transfert pose son entête « Forwarded message ».
    # ⚠️ Pas ``data-o-mail-quote`` : cet attribut décore aussi les signatures
    # elles-mêmes, l'ancre tomberait n'importe où.
    _BF_QUOTE_MARKERS = ("<blockquote", "---------- forwarded message")

    def _bf_quote_offset(self, body):
        """Où commence la citation dans ce corps, -1 s'il n'y en a pas.

        La PREMIÈRE occurrence, donc la citation la plus externe : dans un fil
        qui s'empile, la signature doit passer au-dessus de tout le bloc cité,
        pas se glisser entre deux niveaux.
        """
        minuscule = (body or "").lower()
        positions = [p for p in (minuscule.find(m) for m in self._BF_QUOTE_MARKERS) if p >= 0]
        return min(positions) if positions else -1

    def _notify_by_email_render_layout(self, message, recipients_group,
                                       msg_vals=False, render_values=None):
        """Poser la signature au-dessus de la citation, pas sous elle.

        Le gabarit d'Odoo rend ``message.body`` d'un bloc puis ajoute la
        signature dessous : dans une réponse, elle se retrouve **après** tout
        le fil cité. Avant que la signature quitte le corps, elle tombait au
        bon endroit parce qu'elle était écrite là — au prix du doublon que la
        18.0.11.9.0 a supprimé.

        On garde donc l'unicité et on récupère l'ordre de lecture : le gabarit
        n'ajoute rien, et la signature est insérée dans le rendu juste avant
        l'ouverture de la citation.

        ⚠️ Trois cas retombent volontairement sur le comportement d'Odoo, la
        signature en fin de courriel : pas de citation (un courriel neuf n'a
        rien au-dessus de quoi passer), une citation qui commence au tout
        début du corps (personne n'a écrit au-dessus), et un corps qui ne
        ressort pas tel quel du rendu. Ce dernier repli compte : perdre la
        signature parce qu'on n'a pas retrouvé son ancre serait pire que la
        poser trop bas.
        """
        valeurs = dict(render_values or {})
        signature = valeurs.get("signature") or ""
        corps = (msg_vals or {}).get("body") or message.body or ""
        depart = self._bf_quote_offset(corps)
        if not (valeurs.get("email_add_signature") and signature.strip() and depart > 0):
            return super()._notify_by_email_render_layout(
                message, recipients_group, msg_vals=msg_vals,
                render_values=render_values)

        valeurs["email_add_signature"] = False
        rendu = super()._notify_by_email_render_layout(
            message, recipients_group, msg_vals=msg_vals, render_values=valeurs)
        rendu = Markup(rendu if isinstance(rendu, str) else (rendu or b"").decode())
        # ⚠️ ``Markup.replace`` échappe tout argument qui n'est pas déjà du
        # Markup : un bloc en ``str`` ressortait en clair dans le courriel,
        # `&lt;div style=…` au lieu de la signature. Les deux côtés doivent
        # donc être déclarés sûrs — ils le sont, c'est le HTML que le gabarit
        # aurait rendu tel quel.
        ancre = Markup(corps[depart:depart + 120])
        if ancre not in rendu:
            _logger.info(
                "bf_email_management: ancre de citation introuvable dans le "
                "rendu du message %s, signature laissée en fin de courriel",
                message.id)
            return super()._notify_by_email_render_layout(
                message, recipients_group, msg_vals=msg_vals,
                render_values=render_values)
        bloc = Markup('<div style="font-size: 13px;">%s</div>') % Markup(signature)
        return rendu.replace(ancre, bloc + ancre, 1)

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
