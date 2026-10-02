from odoo import _, api, fields, models
from odoo.exceptions import UserError

from odoo.addons.bf_membership.models.membership import keep_trace

RECEIPT_SEQUENCE_CODE = "bf.membership.receipt"


class MembershipReceipt(models.Model):
    """Le reçu officiel d'une cotisation, aux fins de l'impôt sur le revenu.

    🔴 Un reçu est une COPIE figée de ce qui a été délivré. Tout ce qu'il porte
    (nom et adresse du membre et de l'organisme, numéro d'enregistrement,
    montants, signataire) est recopié au moment de le préparer, jamais relu sur
    le contact ou la société : un membre qui déménage ne change pas le reçu de
    l'an dernier, et un duplicata doit dire exactement ce que disait
    l'original. L'organisme garde ses reçus (paragraphe 230(2) de la Loi de
    l'impôt sur le revenu) : rien ne se modifie ni ne se supprime. Une erreur
    se corrige en ANNULANT le reçu, qui reste au registre marqué « annulé », et
    en le REMPLAÇANT par un reçu au numéro neuf qui nomme l'original
    (article 3501, paragraphes (4) et (5), du Règlement).

    Délivrer, c'est produire le PDF (`report_membership_receipt`) : la première
    production est l'original, chacune des suivantes un duplicata, marqué
    comme tel, au même numéro.
    """

    _name = "bf.membership.receipt"
    _description = "Reçu fiscal de cotisation"
    _inherit = ["mail.thread"]
    _order = "date_issued desc, id desc"
    _check_company_auto = True

    name = fields.Char(string="Numéro de série", required=True, readonly=True, copy=False, index=True)
    membership_id = fields.Many2one(
        "bf.membership", string="Adhésion", required=True, readonly=True, index=True,
        ondelete="restrict", check_company=True,
    )
    partner_id = fields.Many2one(
        "res.partner", string="Membre", required=True, readonly=True, index=True, ondelete="restrict")
    company_id = fields.Many2one("res.company", string="Société", required=True, readonly=True, index=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    state = fields.Selection(
        [("issued", "Délivré"), ("cancelled", "Annulé")],
        string="État", required=True, default="issued", readonly=True, tracking=True,
    )

    date_received = fields.Date(string="Date de réception de la cotisation", required=True, readonly=True)
    date_issued = fields.Date(string="Date de délivrance", required=True, readonly=True)
    gift_amount = fields.Monetary(string="Montant de la cotisation", readonly=True)
    advantage_amount = fields.Monetary(string="Valeur de l'avantage", readonly=True)
    advantage_disregarded = fields.Boolean(
        string="Avantage négligé", readonly=True,
        help="L'avantage ne dépasse pas le moindre de 75 $ et de 10 % de la cotisation : "
             "la politique CSP-M05 de l'ARC permet de ne pas le déduire.",
    )
    advantage_description = fields.Char(string="Nature de l'avantage", readonly=True)
    eligible_amount = fields.Monetary(string="Montant admissible", readonly=True)

    donor_name = fields.Char(string="Nom du membre", readonly=True)
    donor_address = fields.Text(string="Adresse du membre", readonly=True)
    org_name = fields.Char(string="Organisme", readonly=True)
    org_address = fields.Text(string="Adresse de l'organisme", readonly=True)
    registration_number = fields.Char(string="Numéro d'enregistrement", readonly=True)
    place = fields.Char(string="Lieu de délivrance", readonly=True)
    signer_name = fields.Char(string="Personne autorisée", readonly=True)
    signer_title = fields.Char(string="Titre", readonly=True)
    signature = fields.Image(string="Signature", readonly=True, max_width=600, max_height=200)

    delivery_count = fields.Integer(
        string="Délivrances", readonly=True, copy=False,
        help="Combien de fois le PDF a été produit. La première fois est l'original ; "
             "les suivantes sont des duplicatas, au même numéro.",
    )
    replaces_id = fields.Many2one(
        "bf.membership.receipt", string="Remplace le reçu", readonly=True, copy=False,
        ondelete="restrict")
    replaced_by_ids = fields.One2many("bf.membership.receipt", "replaces_id", string="Remplacé par")
    cancel_date = fields.Date(string="Date d'annulation", readonly=True, copy=False)
    cancel_reason = fields.Char(string="Motif de l'annulation", readonly=True, copy=False)

    _sql_constraints = [
        ("name_company_uniq", "unique(company_id, name)",
         "Ce numéro de reçu existe déjà dans cette société."),
    ]

    # ------------------------------------------------------------------

    @api.model
    def _receipt_sequence(self, company):
        """La série des reçus de la société, créée à son premier reçu.

        Une série par société, « sans trou » : l'ARC veut des numéros de série
        uniques, et un trou dans une série de reçus officiels se fait
        questionner à la vérification. Une séquence PostgreSQL ordinaire ne
        recule pas quand la transaction échoue (un PDF qui ne se produit pas)
        et laisserait des trous. Le préfixe est l'année : la série repart à 1
        chaque année et reste unique.
        """
        Sequence = self.env["ir.sequence"].sudo()
        sequence = Sequence.search([("code", "=", RECEIPT_SEQUENCE_CODE), ("company_id", "=", company.id)], limit=1)
        if not sequence:
            sequence = Sequence.create({
                "name": _("Reçus fiscaux de cotisation (%s)", company.name),
                "code": RECEIPT_SEQUENCE_CODE,
                "company_id": company.id,
                "implementation": "no_gap",
                "use_date_range": True,
                "prefix": "%(range_year)s-",
                "padding": 5,
            })
        return sequence

    @api.model
    def _next_number(self, company, date):
        return self._receipt_sequence(company).next_by_id(sequence_date=date)

    @api.model_create_multi
    def create(self, vals_list):
        return super(MembershipReceipt, keep_trace(self)).create(vals_list)

    def write(self, vals):
        """🔴 Un reçu délivré ne se modifie pas, même par un responsable : les
        droits d'accès n'en donnent que la lecture, et ses gestes (délivrer,
        annuler) passent par le code, en superutilisateur, sans jamais effacer
        leur trace (`keep_trace`). Le fil de discussion, lui, reste libre."""
        if not self.env.su:
            raise UserError(_("Un reçu fiscal ne se modifie pas : annulez-le et remplacez-le."))
        return super(MembershipReceipt, keep_trace(self)).write(vals)

    def unlink(self):
        raise UserError(_(
            "Un reçu fiscal ne se supprime pas : l'organisme garde la copie de chaque reçu, "
            "annulé ou non. Annulez-le."))

    # ------------------------------------------------------------------

    def _check_deliverable(self):
        """Un reçu (original ou duplicata) ne se délivre que s'il dit encore vrai.

        🔴 Pas seulement « la cotisation est payée » : un chèque sans provision
        puis un nouveau règlement par 40 $ et un écart radié rendent l'adhésion
        « payée » de nouveau, et le duplicata du reçu de 70 $ certifierait un
        argent jamais reçu. Chaque délivrance refait donc le contrôle complet
        du reçu (`_receipt_payment` : facture de cotisation seule, encaissé égal
        à la cotisation, aucun avoir), et exige le MÊME montant et la MÊME date
        que le reçu figé. Sinon : refus, et le reçu s'annule ou se remplace.
        """
        for receipt in self:
            if receipt.state != "issued":
                continue
            membership = receipt.membership_id.sudo()
            if membership.payment_state != "paid":
                raise UserError(_(
                    "La cotisation du reçu %s n'est plus payée : annulez le reçu plutôt que d'en "
                    "délivrer un duplicata.", receipt.name))
            try:
                amount, date_received = membership._receipt_payment()
            except UserError as error:
                raise UserError(_(
                    "Le reçu %(receipt)s ne dit plus vrai : %(reason)s Annulez-le, et remplacez-le "
                    "s'il y a lieu.", receipt=receipt.name, reason=error.args[0])) from error
            if membership.currency_id.compare_amounts(amount, receipt.gift_amount) or date_received != receipt.date_received:
                raise UserError(_(
                    "Le reçu %s ne dit plus vrai : le paiement de la cotisation a changé depuis sa "
                    "délivrance (montant ou date). Annulez-le, et remplacez-le s'il y a lieu.",
                    receipt.name))

    def _register_delivery(self):
        """Compter une délivrance ; vrai si c'est un duplicata."""
        self.ensure_one()
        duplicate = self.delivery_count >= 1
        self.sudo().delivery_count = self.delivery_count + 1
        self.sudo().message_post(subtype_xmlid="mail.mt_note", body=(
            _("Duplicata délivré.") if duplicate else _("Reçu délivré (original).")))
        return duplicate

    def _cancel(self, reason):
        for receipt in self:
            if receipt.state != "issued":
                raise UserError(_("Le reçu %s est déjà annulé.", receipt.name))
        self.sudo().write({
            "state": "cancelled",
            "cancel_date": fields.Date.context_today(self),
            "cancel_reason": reason,
        })

    def action_cancel_wizard(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "bf.membership.receipt.cancel",
            "view_mode": "form",
            "target": "new",
            "context": {"default_receipt_id": self.id},
        }

    def action_print(self):
        self.ensure_one()
        return self.env.ref("bf_membership_account.action_report_membership_receipt").report_action(self)
