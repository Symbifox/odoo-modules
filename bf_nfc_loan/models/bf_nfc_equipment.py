"""L'équipement prêté, et la ligne de chaque prêt.

⚠️ Le détenteur n'est pas un champ qu'on écrit : il se lit sur le prêt ouvert.
Deux vérités (un champ « détenteur » et une liste de prêts) finissent toujours
par se contredire, et c'est la liste qu'on croit au moment d'un litige.
"""
from datetime import timedelta

from odoo import _, api, fields, models


class BfNfcEquipment(models.Model):
    _name = "bf.nfc.equipment"
    _description = "Équipement prêté par pastille"
    _inherit = ["mail.thread", "mail.activity.mixin", "bf.nfc.target.mixin"]
    _order = "name"

    name = fields.Char(string="Équipement", required=True, tracking=True)
    reference = fields.Char(string="Référence", help="Numéro de série, étiquette d'inventaire.")
    place = fields.Char(string="Rangé à", help="Où il revient quand on le rend.")
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company, required=True)
    max_days = fields.Integer(
        string="Rappel après (jours)",
        help="Au-delà, la personne qui le garde reçoit une activité « à rendre ». "
             "Zéro : jamais de rappel.",
    )
    note = fields.Text()
    # 🔴 Le registre des cadenas (RSST, art. 205) : un cadenas à cléage unique sans nom
    # se remet à une personne qui n'a souvent pas de compte (un sous-traitant). Le
    # registre exige son nom, son téléphone, son employeur, l'heure de remise et de
    # retour. La personne qui tape est celle qui REMET ; celle qui reçoit se nomme.
    registre_externe = fields.Boolean(
        string="Remis à des personnes sans compte", tracking=True,
        help="Chaque remise demande le nom, le téléphone et l'employeur de la personne qui "
             "reçoit : c'est le registre des cadenas à cléage unique sans nom (RSST, art. 205).")
    loan_ids = fields.One2many("bf.nfc.equipment.loan", "equipment_id", string="Prêts")
    current_loan_id = fields.Many2one(
        "bf.nfc.equipment.loan", string="Prêt en cours", compute="_compute_current", store=True)
    holder_id = fields.Many2one(
        "res.users", string="Détenteur", compute="_compute_current", store=True, tracking=True)
    since = fields.Datetime(string="Depuis", compute="_compute_current", store=True)
    holder_label = fields.Char(string="Qui l'a", related="current_loan_id.holder_label")

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
                summary=_("À rendre : %s", equipement.name),
                note=_("Vous l'avez depuis plus de %s jours. Rapportez-le et approchez "
                       "le téléphone de sa pastille.", equipement.max_days),
            )
            pret.reminded = True


class BfNfcEquipmentLoan(models.Model):
    _name = "bf.nfc.equipment.loan"
    _description = "Prêt d'équipement"
    _order = "date_start desc, id desc"

    equipment_id = fields.Many2one("bf.nfc.equipment", required=True, ondelete="cascade", index=True)
    user_id = fields.Many2one("res.users", string="Détenteur", required=True, index=True,
                              help="Pour une remise à une personne sans compte : la personne qui a remis.")
    borrower_name = fields.Char(string="Remis à")
    borrower_phone = fields.Char(string="Téléphone")
    borrower_employer = fields.Char(string="Employeur")
    holder_label = fields.Char(string="Qui l'a", compute="_compute_holder_label")
    date_start = fields.Datetime(string="Pris le", required=True)
    date_end = fields.Datetime(string="Rendu le")
    closed_by_id = fields.Many2one(
        "res.users", string="Fermé par",
        help="La personne qui a rendu l'équipement, ou qui l'a repris à quelqu'un d'autre.")
    offline = fields.Boolean(string="Pris en différé")
    reminded = fields.Boolean(string="Rappel envoyé")
    duration_hours = fields.Float(string="Durée (h)", compute="_compute_duration")

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
