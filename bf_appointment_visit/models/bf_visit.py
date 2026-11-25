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
from odoo.tools.misc import get_lang
from odoo.exceptions import AccessError, UserError

from .langue import langue_de

_logger = logging.getLogger(__name__)


class BfVisit(models.Model):
    _name = "bf.visit"
    _description = "Property showing"
    _inherit = ["mail.thread"]
    _order = "start desc, id desc"

    name = fields.Char(
        string="Number", required=True, copy=False, default="Nouveau", index=True,
    )
    listing_id = fields.Many2one(
        "bf.visit.listing", string="Listing", required=True,
        ondelete="cascade", index=True, tracking=True,
    )
    booking_id = fields.Many2one(
        "resource.booking", string="Booking", ondelete="cascade",
        index=True, copy=False,
        help="Empty for a person who showed up without an appointment.",
    )
    company_id = fields.Many2one(
        related="listing_id.company_id", store=True, string="Company",
    )
    is_walkin = fields.Boolean(
        string="Walk-in", readonly=True,
        help="Signed in on site, during an open house.",
    )
    start = fields.Datetime(string="Start", tracking=True, index=True)
    duration = fields.Float(string="Duration (hours)")
    state = fields.Selection(
        [
            ("requested", "Requested"),
            ("approved", "Confirmed"),
            ("declined", "Refused"),
            ("cancelled", "Cancelled"),
            ("done", "Done"),
            ("no_show", "Nobody showed up"),
        ],
        default="requested", required=True, string="Status", tracking=True,
        index=True,
    )

    # --- Qui est venu -------------------------------------------------------
    visitor_partner_id = fields.Many2one("res.partner", string="Visitor")
    visitor_name = fields.Char(string="Visitor's name", tracking=True)
    visitor_email = fields.Char(string="Email")
    visitor_phone = fields.Char(string="Phone")
    visitor_count = fields.Integer(
        string="Number of people", default=1,
        help="The spouse, the parents, an inspector: what the seller "
             "wants to know before opening the door.",
    )

    # --- La question qui décide de la rémunération --------------------------
    representation = fields.Selection(
        [
            ("unknown", "Question not asked"),
            ("none", "Not represented"),
            ("broker", "Represented by a broker"),
        ],
        default="unknown", required=True, string="Representation", tracking=True,
    )
    representation_asked_at = fields.Datetime(
        string="Question asked on", readonly=True, copy=False,
        help="Time stamp of the answer. This is the piece of evidence "
             "that counts if remuneration is disputed later.",
    )
    buyer_broker_name = fields.Char(string="Visitor's broker")
    buyer_broker_agency = fields.Char(string="Broker's agency")
    buyer_broker_email = fields.Char(string="Broker's email")
    buyer_broker_phone = fields.Char(string="Broker's phone")

    # --- L'identité, consignée sans être conservée --------------------------
    identity_checked = fields.Boolean(string="Identity verified", tracking=True)
    identity_method = fields.Selection(
        [
            ("in_person", "In person, against an official document"),
            ("remote", "Remotely, with an authenticated document"),
        ],
        string="Verification method",
    )
    identity_doc_kind = fields.Selection(
        [
            ("licence", "Driver's licence"),
            ("health", "Health insurance card"),
            ("passport", "Passport"),
            ("other", "Other official document"),
        ],
        string="Document seen",
        help="⚠️ The kind of document, and nothing else. A verification "
             "done in person does not keep a photocopy: it is recorded.",
    )
    identity_checked_by = fields.Many2one(
        "res.users", string="Verified by", readonly=True, copy=False,
    )
    identity_checked_at = fields.Datetime(
        string="Verified on", readonly=True, copy=False,
    )

    # --- Ce que le courtier constate ---------------------------------------
    accompanied_by_id = fields.Many2one(
        "res.users", string="Accompanied by", tracking=True,
    )
    arrived_at = fields.Datetime(string="Arrival", readonly=True, copy=False)
    departed_at = fields.Datetime(string="Departure", readonly=True, copy=False)

    # --- La boucle vendeur --------------------------------------------------
    seller_token = fields.Char(string="Decision token", copy=False, readonly=True)
    seller_decision_at = fields.Datetime(
        string="Seller's decision", readonly=True, copy=False,
    )
    seller_note = fields.Text(string="Word from the seller")
    proposed_start = fields.Datetime(
        string="Proposed alternative time",
        help="What the seller suggests when the requested time does not work.",
    )
    access_released = fields.Boolean(
        string="Access released", readonly=True, copy=False,
        help="The access instructions only go out on confirmation.",
    )

    # --- Le locataire -------------------------------------------------------
    tenant_notice_sent_at = fields.Datetime(
        string="Notice to tenant sent on", readonly=True, copy=False,
        help="The record of the 24 hours' notice required by article 1931.",
    )

    # --- Après la visite ----------------------------------------------------
    feedback_interest = fields.Selection(
        [
            ("1", "No interest"),
            ("2", "Low"),
            ("3", "Worth considering"),
            ("4", "Strong"),
            ("5", "Offer coming"),
        ],
        string="Interest",
    )
    feedback_price = fields.Selection(
        [
            ("high", "Above market"),
            ("fair", "At market"),
            ("low", "Below market"),
        ],
        string="Price perception",
    )
    feedback_comment = fields.Text(string="Comments")
    feedback_received_at = fields.Datetime(string="Received on", readonly=True)
    feedback_shared = fields.Boolean(
        string="Shared with the seller",
        help="A visitor's comment is not always fit to pass on as is. "
             "Nothing goes out without this box.",
    )
    feedback_token = fields.Char(copy=False, readonly=True)
    url_decision = fields.Char(
        string="Decision link", compute="_compute_urls",
        help="What the seller receives in order to decide, with no "
             "account to create.",
    )
    url_feedback = fields.Char(
        string="Feedback link", compute="_compute_urls",
    )
    setup_done = fields.Boolean(
        string="Flow started", copy=False, readonly=True,
        help="Guard against a double send, kept on the record: "
             "`action_confirm` is called more than once in the life of a "
             "booking.",
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
                        "\"%s\" has already arrived: what has been "
                        "recorded is not rewritten. A manager can correct "
                        "a typing mistake, and the correction stays on "
                        "the record."
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
                ) or _("Showing")
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
                    _("\"%s\" is not waiting for a decision.") % rec.display_name
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
                    _("\"%s\" is no longer waiting.") % rec.display_name
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
                    _("The arrival of \"%s\" has already been recorded.") % rec.display_name
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
                    _("Nobody arrived: there is no departure to record.")
                )
            rec.departed_at = fields.Datetime.now()
            rec.state = "done"
        return True

    def action_mark_no_show(self):
        for rec in self:
            if rec.arrived_at:
                raise UserError(
                    _("\"%s\" has arrived: it cannot be marked as a no-show.")
                    % rec.display_name
                )
            rec.state = "no_show"
        return True

    def action_confirm_identity(self):
        """Consigner la vérification d'identité, avec qui l'a faite et quand."""
        for rec in self:
            if not rec.identity_method:
                raise UserError(
                    _("Say first how the identity was verified.")
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
            # Dans la langue du visiteur s'il a une fiche, sinon de la société : la demande d'avis
            # part d'un travail planifié, sans langue au contexte.
            gabarit.sudo().with_context(lang=langue_de(
                rec.env, rec.visitor_partner_id, societe=rec.company_id)).send_mail(
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
            gabarit.sudo().with_context(lang=langue_de(
                rec.env, destinataires[:1], societe=rec.company_id)).send_mail(
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
            gabarit.sudo().with_context(lang=langue_de(
                rec.env, locataire[:1], societe=rec.company_id)).send_mail(
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
        # Sans langue au contexte (travail planifié, XML-RPC), les libellés écrits au chatter
        # (suivi des états, message de création) sortaient en anglais depuis la bascule.
        if not self.env.lang:
            self = self.with_context(lang=get_lang(self.env).code)
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
