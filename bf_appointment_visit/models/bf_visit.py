"""Le registre des visites.

L'OACIQ, sur la visite libre : « pour des raisons de sécurité et de protection
de la propriété à vendre, nous vous rappelons que le courtier devrait vérifier
l'identité des visiteurs et inscrire leurs noms dans un registre des visites ».
Et, dès qu'on demande à visiter, le courtier du vendeur doit demander à la
première occasion si la personne est représentée par un courtier, puis dire
qu'il représente le vendeur. La réponse à cette question décide parfois de la
rémunération, par le critère de la cause efficiente de la vente.

Ce modèle est donc un document, pas un pense-bête. Deux conséquences :

* on consigne le TYPE de pièce d'identité vue, jamais son numéro ni une copie.
  L'OACIQ l'écrit noir sur blanc pour la vérification en personne, et la Loi 25
  dit la même chose autrement;
* une fois l'arrivée constatée, le registre se ferme. 🔴 Patron repris d'un
  autre journal de visiteurs de la maison, dont un audit avait trouvé qu'un hôte
  marquait son visiteur arrivé, antidatait l'heure et changeait le nom après
  coup. `readonly=True` ne vaut que pour l'écran : un appel direct écrit sans
  obstacle, et c'est le document qu'on consultera après un incident.
"""

import logging
import secrets
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

_logger = logging.getLogger(__name__)


class BfVisit(models.Model):
    _name = "bf.visit"
    _description = "Visite d'une propriété"
    _inherit = ["mail.thread"]
    _order = "start desc, id desc"

    name = fields.Char(
        string="Numéro", required=True, copy=False, default="Nouveau", index=True,
    )
    listing_id = fields.Many2one(
        "bf.visit.listing", string="Inscription", required=True,
        ondelete="cascade", index=True, tracking=True,
    )
    booking_id = fields.Many2one(
        "resource.booking", string="Réservation", ondelete="cascade",
        index=True, copy=False,
        help="Vide pour une personne qui s'est présentée sans rendez-vous.",
    )
    company_id = fields.Many2one(
        related="listing_id.company_id", store=True, string="Société",
    )
    is_walkin = fields.Boolean(
        string="Sans rendez-vous", readonly=True,
        help="Inscrite sur place, pendant une visite libre.",
    )
    start = fields.Datetime(string="Début", tracking=True, index=True)
    duration = fields.Float(string="Durée (heures)")
    state = fields.Selection(
        [
            ("requested", "Demandée"),
            ("approved", "Confirmée"),
            ("declined", "Refusée"),
            ("cancelled", "Annulée"),
            ("done", "Faite"),
            ("no_show", "Personne ne s'est présenté"),
        ],
        default="requested", required=True, string="État", tracking=True,
        index=True,
    )

    # --- Qui est venu -------------------------------------------------------
    visitor_partner_id = fields.Many2one("res.partner", string="Visiteur")
    visitor_name = fields.Char(string="Nom du visiteur", tracking=True)
    visitor_email = fields.Char(string="Courriel")
    visitor_phone = fields.Char(string="Téléphone")
    visitor_count = fields.Integer(
        string="Nombre de personnes", default=1,
        help="Le conjoint, les parents, un inspecteur : ce que le vendeur veut "
             "savoir avant d'ouvrir sa porte.",
    )

    # --- La question qui décide de la rémunération --------------------------
    representation = fields.Selection(
        [
            ("unknown", "Question non posée"),
            ("none", "Non représenté"),
            ("broker", "Représenté par un courtier"),
        ],
        default="unknown", required=True, string="Représentation", tracking=True,
    )
    representation_asked_at = fields.Datetime(
        string="Question posée le", readonly=True, copy=False,
        help="Horodatage de la réponse. C'est la pièce qui compte si la "
             "rémunération se discute plus tard.",
    )
    buyer_broker_name = fields.Char(string="Courtier du visiteur")
    buyer_broker_agency = fields.Char(string="Agence du courtier")
    buyer_broker_email = fields.Char(string="Courriel du courtier")
    buyer_broker_phone = fields.Char(string="Téléphone du courtier")

    # --- L'identité, consignée sans être conservée --------------------------
    identity_checked = fields.Boolean(string="Identité vérifiée", tracking=True)
    identity_method = fields.Selection(
        [
            ("in_person", "En personne, sur pièce officielle"),
            ("remote", "À distance, pièce authentifiée"),
        ],
        string="Mode de vérification",
    )
    identity_doc_kind = fields.Selection(
        [
            ("licence", "Permis de conduire"),
            ("health", "Carte d'assurance maladie"),
            ("passport", "Passeport"),
            ("other", "Autre pièce officielle"),
        ],
        string="Pièce vue",
        help="⚠️ Le type de pièce, et rien d'autre. Une vérification faite en "
             "personne ne garde pas de photocopie : elle se consigne.",
    )
    identity_checked_by = fields.Many2one(
        "res.users", string="Vérifiée par", readonly=True, copy=False,
    )
    identity_checked_at = fields.Datetime(
        string="Vérifiée le", readonly=True, copy=False,
    )

    # --- Ce que le courtier constate ---------------------------------------
    accompanied_by_id = fields.Many2one(
        "res.users", string="Accompagnée par", tracking=True,
    )
    arrived_at = fields.Datetime(string="Arrivée", readonly=True, copy=False)
    departed_at = fields.Datetime(string="Départ", readonly=True, copy=False)

    # --- La boucle vendeur --------------------------------------------------
    seller_token = fields.Char(string="Jeton de décision", copy=False, readonly=True)
    seller_decision_at = fields.Datetime(
        string="Décision du vendeur", readonly=True, copy=False,
    )
    seller_note = fields.Text(string="Mot du vendeur")
    proposed_start = fields.Datetime(
        string="Autre heure proposée",
        help="Ce que le vendeur suggère quand l'heure demandée ne va pas.",
    )
    access_released = fields.Boolean(
        string="Accès transmis", readonly=True, copy=False,
        help="Les consignes d'accès ne partent qu'à la confirmation.",
    )

    # --- Le locataire -------------------------------------------------------
    tenant_notice_sent_at = fields.Datetime(
        string="Avis au locataire envoyé le", readonly=True, copy=False,
        help="La trace du préavis de 24 heures de l'article 1931.",
    )

    # --- Après la visite ----------------------------------------------------
    feedback_interest = fields.Selection(
        [
            ("1", "Aucun intérêt"),
            ("2", "Faible"),
            ("3", "À considérer"),
            ("4", "Fort"),
            ("5", "Offre à venir"),
        ],
        string="Intérêt",
    )
    feedback_price = fields.Selection(
        [
            ("high", "Au-dessus du marché"),
            ("fair", "Dans le marché"),
            ("low", "En dessous du marché"),
        ],
        string="Perception du prix",
    )
    feedback_comment = fields.Text(string="Commentaires")
    feedback_received_at = fields.Datetime(string="Reçue le", readonly=True)
    feedback_shared = fields.Boolean(
        string="Partagée au vendeur",
        help="Un commentaire de visiteur n'est pas toujours bon à transmettre "
             "tel quel. Rien ne part sans cette case.",
    )
    feedback_token = fields.Char(copy=False, readonly=True)
    url_decision = fields.Char(
        string="Lien de décision", compute="_compute_urls",
        help="Ce que le vendeur reçoit pour trancher, sans compte à créer.",
    )
    url_feedback = fields.Char(
        string="Lien de rétroaction", compute="_compute_urls",
    )
    setup_done = fields.Boolean(
        string="Parcours amorcé", copy=False, readonly=True,
        help="Garde du double envoi, posée sur la fiche : `action_confirm` est "
             "appelée plus d'une fois dans la vie d'une réservation.",
    )

    @api.depends("seller_token", "feedback_token")
    def _compute_urls(self):
        """Les liens publics, calculés là plutôt que passés par le contexte.

        Un gabarit de courriel qui lit le contexte dépend de qui l'appelle :
        renvoyé à la main depuis l'écran des courriels, il rendrait un bouton
        vers nulle part. L'objet, lui, sait toujours répondre.
        """
        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url") or ""
        for rec in self:
            rec.url_decision = (
                "%s/visite/vendeur/%s" % (base, rec.seller_token)
                if rec.seller_token else ""
            )
            rec.url_feedback = (
                "%s/visite/retour/%s" % (base, rec.feedback_token)
                if rec.feedback_token else ""
            )

    # ------------------------------------------------------------------
    # Le registre se ferme quand la visite a commencé
    # ------------------------------------------------------------------
    _registre = (
        "visitor_name", "visitor_email", "visitor_phone", "visitor_count",
        "visitor_partner_id", "representation", "representation_asked_at",
        "buyer_broker_name", "buyer_broker_agency", "buyer_broker_email",
        "buyer_broker_phone", "identity_checked", "identity_method",
        "identity_doc_kind", "identity_checked_by", "identity_checked_at",
        "arrived_at", "departed_at", "accompanied_by_id", "start",
    )

    def write(self, vals):
        """Ce qui a été constaté ne se réécrit pas.

        Le registre sert le jour où quelque chose a mal tourné pendant une
        visite. Un document qu'on peut retoucher après coup ne vaut rien ce
        jour-là. Le gestionnaire garde la main pour corriger une erreur de
        saisie, et sa correction laisse une trace au fil de discussion.
        """
        if not self.env.su and not self.env.user.has_group(
            "bf_appointment_visit.group_bf_visit_manager"
        ):
            touche = set(vals) & set(self._registre)
            commencees = self.filtered(lambda v: v.arrived_at)
            if touche and commencees:
                raise AccessError(
                    _(
                        "« %s » est déjà arrivée : ce qui a été constaté ne se "
                        "réécrit pas. Un gestionnaire peut corriger une erreur "
                        "de saisie, et la correction reste au dossier."
                    )
                    % commencees[0].display_name
                )
        return super().write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "Nouveau") == "Nouveau":
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "bf.visit"
                ) or _("Visite")
            if not vals.get("seller_token"):
                vals["seller_token"] = secrets.token_urlsafe(24)
            if not vals.get("feedback_token"):
                vals["feedback_token"] = secrets.token_urlsafe(24)
        return super().create(vals_list)

    @api.depends("name", "visitor_name", "listing_id.name")
    def _compute_display_name(self):
        for rec in self:
            morceaux = [rec.name]
            if rec.visitor_name:
                morceaux.append(rec.visitor_name)
            if rec.listing_id:
                morceaux.append(rec.listing_id.name)
            rec.display_name = " · ".join(m for m in morceaux if m)

    # ------------------------------------------------------------------
    # Le parcours
    # ------------------------------------------------------------------
    def _post_booking_setup(self):
        """Ce qui se passe juste après qu'une réservation ait produit la visite."""
        for rec in self:
            rec._read_representation_from_intake()
            if rec.listing_id.approval_mode == "auto":
                rec.action_approve(silencieux_vendeur=True)
            else:
                rec._send_seller_request()
                rec._notify_visitor("bf_appointment_visit.mail_visit_pending")

    def _read_representation_from_intake(self):
        """Verser la réponse du formulaire public dans le registre.

        La question vit comme un champ d'accueil du type de rendez-vous, donc
        sa réponse arrive avec la réservation. On la recopie ici parce que le
        registre doit se lire seul, sans aller fouiller la réservation.
        """
        for rec in self:
            if not rec.booking_id:
                continue
            reponses = self.env["appointment.intake.answer"].sudo().search(
                [("booking_id", "=", rec.booking_id.id)]
            )
            for reponse in reponses:
                role = reponse.field_id.bf_visit_role
                valeur = (reponse.value or "").strip()
                if not valeur or not role:
                    continue
                if role == "representation":
                    # Une case cochée « Oui » comme un champ rempli à la main :
                    # tout ce qui n'est pas un refus explicite compte comme
                    # « représenté », parce que se tromper dans ce sens-là ne
                    # coûte qu'une question de plus à l'arrivée.
                    refus = valeur.lower().startswith(("non", "no"))
                    rec.representation = "none" if refus else "broker"
                    rec.representation_asked_at = fields.Datetime.now()
                elif role == "broker_name":
                    rec.buyer_broker_name = valeur

    def action_approve(self, silencieux_vendeur=False):
        for rec in self:
            if rec.state not in ("requested", "declined"):
                raise UserError(
                    _("« %s » n'est pas en attente d'une décision.") % rec.display_name
                )
            rec.state = "approved"
            if not silencieux_vendeur:
                rec.seller_decision_at = fields.Datetime.now()
            rec._release_access()
            rec._notify_visitor("bf_appointment_visit.mail_visit_approved")
            rec._send_tenant_notice()
        return True

    def action_decline(self):
        for rec in self:
            if rec.state not in ("requested", "approved"):
                raise UserError(
                    _("« %s » n'est plus en attente.") % rec.display_name
                )
            rec.state = "declined"
            rec.seller_decision_at = fields.Datetime.now()
            rec._notify_visitor("bf_appointment_visit.mail_visit_declined")
            rec._release_slot()
        return True

    def action_cancel(self):
        for rec in self:
            rec.state = "cancelled"
            rec._release_slot()
        return True

    def action_mark_arrived(self):
        """Constater l'arrivée. Le registre se ferme à cet instant."""
        for rec in self:
            if rec.arrived_at:
                raise UserError(
                    _("L'arrivée de « %s » est déjà constatée.") % rec.display_name
                )
            rec.arrived_at = fields.Datetime.now()
            if not rec.accompanied_by_id:
                rec.accompanied_by_id = self.env.user
            if rec.state == "requested":
                rec.state = "approved"
        return True

    def action_mark_departed(self):
        for rec in self:
            if not rec.arrived_at:
                raise UserError(
                    _("Personne n'est arrivé : il n'y a pas de départ à constater.")
                )
            rec.departed_at = fields.Datetime.now()
            rec.state = "done"
        return True

    def action_mark_no_show(self):
        for rec in self:
            if rec.arrived_at:
                raise UserError(
                    _("« %s » est arrivée : elle ne peut pas être absente.")
                    % rec.display_name
                )
            rec.state = "no_show"
        return True

    def action_confirm_identity(self):
        """Consigner la vérification d'identité, avec qui l'a faite et quand."""
        for rec in self:
            if not rec.identity_method:
                raise UserError(
                    _("Dites d'abord comment l'identité a été vérifiée.")
                )
            rec.write({
                "identity_checked": True,
                "identity_checked_by": self.env.user.id,
                "identity_checked_at": fields.Datetime.now(),
            })
        return True

    def _release_access(self):
        """Les consignes d'accès ne sortent qu'une fois la visite confirmée."""
        for rec in self:
            if rec.listing_id.access_instructions:
                rec.access_released = True

    def _release_slot(self):
        """Rendre le créneau quand la visite ne se fera pas.

        La réservation est annulée plutôt que supprimée : un créneau qui se
        libère tout seul ne s'explique pas une semaine plus tard.
        """
        for rec in self:
            if rec.booking_id and rec.booking_id.state != "canceled":
                rec.booking_id.sudo().action_cancel()

    # ------------------------------------------------------------------
    # Les envois
    # ------------------------------------------------------------------
    def _notify_visitor(self, template_xmlid):
        for rec in self:
            if not rec.visitor_email:
                continue
            gabarit = self.env.ref(template_xmlid, raise_if_not_found=False)
            if not gabarit:
                _logger.warning("Gabarit %s introuvable", template_xmlid)
                continue
            gabarit.sudo().send_mail(
                rec.id, force_send=False
            )

    def _send_seller_request(self):
        gabarit = self.env.ref(
            "bf_appointment_visit.mail_visit_seller_request", raise_if_not_found=False
        )
        for rec in self:
            destinataires = rec.listing_id.seller_ids
            if rec.listing_id.approval_mode == "broker":
                destinataires = rec.booking_id.user_id.partner_id or destinataires
            if not gabarit or not destinataires:
                continue
            gabarit.sudo().send_mail(
                rec.id,
                force_send=False,
                email_values={
                    "recipient_ids": [(6, 0, destinataires.ids)],
                },
            )

    def _send_tenant_notice(self):
        """L'avis de 24 heures au locataire, et sa trace.

        L'article 1931 veut un avis d'au moins 24 heures. On l'envoie à la
        confirmation, et on garde l'heure d'envoi : sans elle, il n'y a rien à
        montrer si le locataire conteste.
        """
        gabarit = self.env.ref(
            "bf_appointment_visit.mail_visit_tenant_notice", raise_if_not_found=False
        )
        for rec in self:
            locataire = rec.listing_id.tenant_id
            if rec.listing_id.occupancy != "tenant" or not locataire or not gabarit:
                continue
            gabarit.sudo().send_mail(
                rec.id,
                force_send=False,
                email_values={"recipient_ids": [(6, 0, locataire.ids)]},
            )
            rec.tenant_notice_sent_at = fields.Datetime.now()

    def _send_feedback_request(self):
        self._notify_visitor("bf_appointment_visit.mail_visit_feedback_request")

    # ------------------------------------------------------------------
    # Passe de nuit
    # ------------------------------------------------------------------
    @api.model
    def _cron_close_visits(self):
        """Fermer les visites passées, et demander ce qu'on en a pensé.

        Une visite confirmée dont personne n'a constaté l'arrivée ne devient
        pas « faite » : elle devient « personne ne s'est présenté ». Les deux
        se comptent différemment, et le vendeur a le droit de savoir lequel des
        deux s'est produit.
        """
        maintenant = fields.Datetime.now()
        a_fermer = self.search([
            ("state", "=", "approved"),
            ("start", "<", maintenant - timedelta(hours=2)),
        ])
        for visite in a_fermer:
            if visite.arrived_at:
                visite.state = "done"
                if not visite.departed_at:
                    visite.departed_at = visite.start + timedelta(
                        hours=visite.duration or 0.5
                    )
            else:
                visite.state = "no_show"
        faites = a_fermer.filtered(lambda v: v.state == "done" and v.visitor_email)
        faites._send_feedback_request()
        return True
