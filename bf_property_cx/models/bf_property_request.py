"""La demande d'avis, au moment où la demande d'entretien se referme.

Le point d'accroche est `action_done`, et lui seul. Il y a trois autres façons
de sortir une demande de la file — la refuser, la rouvrir, l'archiver — et
aucune ne dit qu'un service a été rendu.

🔴 **Le refus ne déclenche rien, et ce n'est pas un oubli.** `action_refuse`
exige qu'on dise pourquoi la demande sort de l'objet du syndicat de l'art. 1039
C.c.Q. Envoyer « comment ça s'est passé ? » là-dessus mesurerait le refus. Un
test l'éprouve, parce que c'est exactement le genre de comportement qu'un
raccourci ajouterait un jour « pour couvrir tous les cas ».

⚠️ **Rien de ceci ne peut faire échouer la fermeture.** Le concierge vient de
dire ce qui a été fait, et ce geste ne se perd pas parce qu'un courriel n'est
pas parti : la demande d'avis tient dans un point de reprise, et l'échec s'écrit
au journal.
"""
import logging

from odoo import _, fields, models

from odoo.addons.bf_cx.models.bf_cx_feedback import param_is_true

from odoo.addons.bf_property_core.models.mail_template import bf_mail_layout

_logger = logging.getLogger(__name__)


class BfPropertyRequest(models.Model):
    # ⚠️ Ajouter un mixin à un modèle qui existe déjà se fait en le nommant des
    # DEUX côtés : `_name` le désigne, `_inherit` le reprend avec le mixin. Sans
    # `_name`, Odoo créerait un modèle neuf nommé d'après la classe.
    #
    # 🔴 **Et `rating.mixin` passe AVANT, pas après.** Il hérite lui-même de
    # `mail.thread`, qui est déjà une base directe de `bf.property.request`.
    # Nommé en dernier, il devrait se linéariser après un `mail.thread` qui est
    # pourtant sa propre base : Odoo meurt au chargement sur « Cannot create a
    # consistent method resolution order », et le message ne nomme pas le
    # coupable. L'ordre inverse laisse `rating.mixin` précéder `mail.thread`.
    _name = "bf.property.request"
    _inherit = ["rating.mixin", "bf.property.request"]

    cx_feedback_sent = fields.Boolean(
        string="Avis demandé", copy=False, readonly=True,
        help="Une demande réglée ne fait partir qu'une seule demande d'avis. "
             "Rouvrir puis refermer la demande n'en fait pas partir une "
             "deuxième : la personne a déjà été sollicitée.",
    )

    def _rating_get_partner(self):
        """Le demandeur, et non un `partner_id` que ce modèle n'a pas.

        `rating.mixin` cherche `partner_id` par défaut. Ici la personne est au
        champ `requester_partner_id` : sans cette redirection, le jeton d'accès,
        le gabarit et le garde-fou de bf_cx viseraient tous le vide.
        """
        self.ensure_one()
        if self.requester_partner_id:
            return self.requester_partner_id
        return super()._rating_get_partner()

    def action_done(self):
        res = super().action_done()
        for request in self:
            try:
                with self.env.cr.savepoint():
                    request._cx_maybe_request_feedback()
            except Exception as exc:  # noqa: BLE001 — jamais au prix de la fermeture
                _logger.exception(
                    "bf_property_cx : demande d'avis impossible pour %s",
                    request.id,
                )
                # 🔴 Sans ce message, l'échec n'était écrit qu'au
                # journal du serveur. Le syndicat, lui, ne voyait rien et
                # croyait l'avis demandé.
                request.message_post(
                    body=_("Avis non demandé : l'envoi a échoué (%s).")
                    % (str(exc).splitlines() or [type(exc).__name__])[0][:200]
                )
        return res

    def rating_send_request(self, template, lang=False, force_send=True):
        """Le même envoi qu'Odoo, dans la mise en page de marque si elle est là.

        Odoo impose ici la mise en page légère. Les
        courriels de la suite s'alignent sur l'habillage de la société, comme
        les factures, les résumés quotidiens et les banques d'heures.
        """
        if lang:
            template = template.with_context(lang=lang)
        self.with_context(mail_notify_force_send=force_send).message_post_with_source(
            template,
            email_layout_xmlid=bf_mail_layout(self.env),
            force_send=force_send,
            subtype_xmlid="mail.mt_note",
        )

    def _cx_maybe_request_feedback(self):
        """Envoie la demande d'avis, ou dit au fil pourquoi elle ne part pas.

        Chaque refus laisse une trace lisible plutôt qu'un silence : un syndicat
        qui a ouvert la mesure et ne reçoit rien doit pouvoir lire pourquoi sans
        ouvrir un journal de serveur.
        """
        self.ensure_one()
        if self.state != "done" or self.cx_feedback_sent:
            return
        if not self.organisation_id._cx_feedback_open():
            return
        if not param_is_true(self.env, "bf_cx.ingest_ratings", default=True):
            # Le registre de bf_cx n'ingère pas : demander un avis qui n'irait
            # nulle part solliciterait quelqu'un pour rien.
            return
        template = self.env.ref(
            "bf_property_cx.mail_template_request_rating",
            raise_if_not_found=False,
        )
        if not template:
            return
        partner = self._rating_get_partner()
        if not partner or not partner.email:
            self.message_post(
                body=_(
                    "Avis non demandé : %s n'a pas d'adresse de courriel au "
                    "registre."
                )
                % (partner.display_name if partner else _("le demandeur"))
            )
            return
        # 🔴 En `sudo`, et c'est la seule lecture qui l'est. Le pont `bf_cx_privacy` lit les préférences de contact
        # (`privacy.contact.preference`), réservées au groupe vie privée. Un
        # gestionnaire qui réglait une demande tombait sur une AccessError,
        # avalée plus haut : aucun avis ne partait jamais, sauf en admin.
        # Savoir si une personne peut être sollicitée est une règle du système,
        # pas un droit de celui qui ferme la demande.
        partner = partner.sudo()
        allowed, blocked = partner._bf_cx_split_solicitable()
        if blocked:
            self.message_post(
                body=_(
                    "Avis non demandé : %s a été sollicité récemment "
                    "(garde-fou anti-sursollicitation)."
                )
                % partner.display_name
            )
            return
        self.rating_send_request(template, lang=partner.lang, force_send=False)
        partner._bf_cx_mark_solicited()
        self.cx_feedback_sent = True
        self.message_post(
            body=_("Avis demandé à %s.") % partner.display_name
        )
