"""L'équipement prêté, et la ligne de chaque prêt.

⚠️ Le détenteur n'est pas un champ qu'on écrit : il se lit sur le prêt ouvert.
Deux vérités (un champ « détenteur » et une liste de prêts) finissent toujours
par se contredire, et c'est la liste qu'on croit au moment d'un litige.
"""
from datetime import timedelta

from odoo import _, api, fields, models


class BfNfcEquipment(models.Model):
    _name = "bf.nfc.equipment"
    _description = "Equipment loaned by tag"
    _inherit = ["mail.thread", "mail.activity.mixin", "bf.nfc.target.mixin"]
    _order = "name"

    name = fields.Char(string="Equipment name", required=True, tracking=True)
    reference = fields.Char(string="Reference", help="Serial number, "
                                                     "inventory label.")
    place = fields.Char(string="Stored at", help="Where it goes back when "
                                               "returned.")
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company, required=True)
    max_days = fields.Integer(
        string="Reminder after (days)",
        help="Beyond this, the person keeping it gets a “to return” "
             "activity. Zero: never a reminder.",
    )
    note = fields.Text()
    # 🔴 Le registre des cadenas (RSST, art. 205) : un cadenas à cléage unique sans nom
    # se remet à une personne qui n'a souvent pas de compte (un sous-traitant). Le
    # registre exige son nom, son téléphone, son employeur, l'heure de remise et de
    # retour. La personne qui tape est celle qui REMET ; celle qui reçoit se nomme.
    registre_externe = fields.Boolean(
        string="Handed out to people without an account", tracking=True,
        help="Each hand-out asks for the name, phone number and employer "
             "of the person receiving it: this is the register of unnamed "
             "single-key padlocks (RSST, s. 205).")
    loan_ids = fields.One2many("bf.nfc.equipment.loan", "equipment_id", string="Loans")
    current_loan_id = fields.Many2one(
        "bf.nfc.equipment.loan", string="Current loan", compute="_compute_current", store=True)
    holder_id = fields.Many2one(
        "res.users", string="Holder", compute="_compute_current", store=True, tracking=True)
    since = fields.Datetime(string="Since", compute="_compute_current", store=True)
    holder_label = fields.Char(string="Who has it", related="current_loan_id.holder_label")

    @api.depends("loan_ids.date_end", "loan_ids.date_start", "loan_ids.user_id")
    def _compute_current(self):
        for equipement in self:
            ouvert = equipement.loan_ids.filtered(lambda l: not l.date_end)[:1]
            equipement.current_loan_id = ouvert
            equipement.holder_id = ouvert.user_id
            equipement.since = ouvert.date_start

    # ------------------------------------------------------------------
    def _prendre(self, personne, moment, differe=False, externe=None):
        """Ouvre un prêt au nom de ``personne``, en fermant celui d'un autre s'il existe.

        ``externe`` : ``{nom, telephone, employeur}`` de la personne sans compte qui
        reçoit l'équipement ; ``personne`` est alors celle qui le remet.
        """
        self.ensure_one()
        precedent = self.sudo().current_loan_id
        if precedent:
            precedent.write({"date_end": moment, "closed_by_id": personne.id})
        valeurs = {"equipment_id": self.id, "user_id": personne.id,
                   "date_start": moment, "offline": differe}
        if externe:
            valeurs.update({"borrower_name": externe["nom"], "borrower_phone": externe["telephone"],
                            "borrower_employer": externe.get("employeur") or False})
        self.env["bf.nfc.equipment.loan"].sudo().create(valeurs)
        return precedent

    def _rendre(self, personne, moment):
        self.ensure_one()
        pret = self.sudo().current_loan_id
        pret.write({"date_end": moment, "closed_by_id": personne.id})
        return pret

    @api.model
    def _cron_rappels(self):
        """Une activité « à rendre » par prêt en retard, une seule fois.

        ⚠️ Marqué sur le PRÊT et pas sur l'équipement : rendu puis repris, le
        même portable doit pouvoir déclencher un nouveau rappel.
        """
        maintenant = fields.Datetime.now()
        prets = self.env["bf.nfc.equipment.loan"].sudo().search([
            ("date_end", "=", False), ("reminded", "=", False),
            ("equipment_id.max_days", ">", 0),
        ])
        type_todo = self.env.ref("mail.mail_activity_data_todo", raise_if_not_found=False)
        for pret in prets:
            equipement = pret.equipment_id
            if pret.date_start + timedelta(days=equipement.max_days) > maintenant:
                continue
            equipement.activity_schedule(
                activity_type_id=type_todo.id if type_todo else False,
                user_id=pret.user_id.id,
                summary=_("To return: %s", equipement.name),
                note=_("You have had it for more than %s days. Bring it "
                       "back and tap its tag with your phone.", equipement.max_days),
            )
            pret.reminded = True


class BfNfcEquipmentLoan(models.Model):
    _name = "bf.nfc.equipment.loan"
    _description = "Equipment loan"
    _order = "date_start desc, id desc"

    equipment_id = fields.Many2one("bf.nfc.equipment", required=True, ondelete="cascade", index=True)
    user_id = fields.Many2one("res.users", string="Holder", required=True, index=True,
                              help="For a hand-out to a person without an "
                                   "account: the person who handed it out.")
    borrower_name = fields.Char(string="Handed out to")
    borrower_phone = fields.Char(string="Phone")
    borrower_employer = fields.Char(string="Employer")
    holder_label = fields.Char(string="Who has it", compute="_compute_holder_label")
    date_start = fields.Datetime(string="Taken on", required=True)
    date_end = fields.Datetime(string="Returned on")
    closed_by_id = fields.Many2one(
        "res.users", string="Closed by",
        help="The person who returned the equipment, or took it back from "
             "someone else.")
    offline = fields.Boolean(string="Taken later (offline)")
    reminded = fields.Boolean(string="Reminder sent")
    duration_hours = fields.Float(string="Duration (h)", compute="_compute_duration")

    @api.depends("user_id", "borrower_name", "borrower_employer")
    def _compute_holder_label(self):
        for pret in self:
            if pret.borrower_name:
                pret.holder_label = " · ".join(filter(None, [pret.borrower_name, pret.borrower_employer]))
            else:
                pret.holder_label = pret.user_id.name

    def _registre(self):
        """Les colonnes du registre des cadenas, pour un prêt interne comme externe."""
        self.ensure_one()
        if self.borrower_name:
            return self.borrower_name, self.borrower_phone, self.borrower_employer or "", self.user_id.name
        partenaire = self.user_id.partner_id
        return (self.user_id.name, partenaire.phone or partenaire.mobile or "",
                self.equipment_id.company_id.name, self.user_id.name)

    @api.depends("date_start", "date_end")
    def _compute_duration(self):
        maintenant = fields.Datetime.now()
        for pret in self:
            fin = pret.date_end or maintenant
            pret.duration_hours = (fin - pret.date_start).total_seconds() / 3600 if pret.date_start else 0.0
