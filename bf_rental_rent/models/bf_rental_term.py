"""L'échéance de loyer : ce qui est dû, quand, et ce qui a été versé.

**Art. 1903 al. 2** : le loyer « est payable par versements égaux, sauf le
dernier qui peut être moindre ; il est aussi payable **le premier jour de chaque
terme**, à moins qu'il n'en soit convenu autrement ». Le module reprend ce défaut
et laisse le bail en convenir autrement.

🔴 **Le module ne rend JAMAIS « le total du loyer restant ».** L'art. 1905 :
« Est sans effet la clause d'un bail stipulant que le loyer total sera exigible
en cas de défaut du locataire d'effectuer un versement. » La déchéance du terme
est interdite en louage résidentiel — c'est banal partout ailleurs, et c'est nul
ici. Un champ « solde total du bail » inviterait à le réclamer ; il n'existe pas,
et `test_refusals.py` garde cette absence.

🔴 **Impayé n'est pas « en défaut ».** L'art. 1907 al. 2 permet au locataire de
**déposer son loyer au greffe du tribunal**, sur préavis de 10 jours et
autorisation. Le loyer est alors versé — ailleurs. Un module qui compterait tout
non-encaissé comme un arrérage accuserait un locataire qui a fait exactement ce
que la loi lui permet. D'où l'état `deposited`.
"""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class BfRentalTerm(models.Model):
    _name = "bf.rental.term"
    _description = "Terme de loyer"
    _order = "date_due, id"

    lease_id = fields.Many2one(
        "bf.rental.lease", string="Bail", required=True,
        ondelete="cascade", index=True,
    )
    company_id = fields.Many2one(
        related="lease_id.company_id", store=True, readonly=True,
    )
    currency_id = fields.Many2one(
        related="lease_id.currency_id", readonly=True,
    )
    date_due = fields.Date(string="Exigible le", required=True, index=True)
    amount_due = fields.Monetary(string="Montant dû", required=True)

    payment_ids = fields.One2many(
        "bf.rental.payment", "term_id", string="Versements",
    )
    amount_paid = fields.Monetary(
        string="Versé", compute="_compute_amounts", store=True,
    )
    amount_outstanding = fields.Monetary(
        string="Solde", compute="_compute_amounts", store=True,
    )

    deposited_at_court = fields.Boolean(
        string="Déposé au greffe (art. 1907)",
        help="⚠️ Le locataire autorisé par le tribunal dépose son loyer au "
             "greffe. Il a PAYÉ : il n'est pas en défaut, et ce terme ne "
             "compte pas dans les arrérages. Le compter serait accuser "
             "quelqu'un d'avoir fait ce que la loi lui permet.",
    )
    state = fields.Selection(
        [("pending", "À venir"), ("paid", "Acquitté"),
         ("partial", "Partiel"), ("late", "En retard"),
         ("deposited", "Déposé au greffe")],
        string="État", compute="_compute_state", store=True,
    )
    days_late = fields.Integer(
        string="Jours de retard", compute="_compute_state", store=True,
        help="Compté depuis la date d'exigibilité, et seulement sur ce qui "
             "reste dû. Un terme acquitté en retard n'a plus de retard : il a "
             "été payé.",
    )

    @api.depends("payment_ids.amount", "amount_due")
    def _compute_amounts(self):
        for term in self:
            paid = sum(term.payment_ids.mapped("amount"))
            term.amount_paid = paid
            term.amount_outstanding = max(term.amount_due - paid, 0.0)

    @api.depends("amount_outstanding", "date_due", "deposited_at_court")
    def _compute_state(self):
        today = fields.Date.context_today(self)
        for term in self:
            if term.deposited_at_court:
                term.state = "deposited"
                term.days_late = 0
                continue
            if term.amount_outstanding <= 0:
                term.state = "paid"
                term.days_late = 0
                continue
            late = term.date_due and today > term.date_due
            term.days_late = (today - term.date_due).days if late else 0
            if not late:
                term.state = "pending"
            elif term.amount_paid > 0:
                term.state = "partial"
            else:
                term.state = "late"

    @api.constrains("amount_due")
    def _check_the_amount_is_a_single_term(self):
        """🔴 Aucun terme ne porte plus que le loyer convenu.

        L'art. 1905 rend sans effet la clause de déchéance du terme, et
        l'art. 1904 al. 1 interdit d'exiger un versement qui excède un mois de
        loyer. Un terme gonflé serait l'un ou l'autre selon l'intention, et
        aucun des deux n'est licite.
        """
        for term in self:
            lease = term.lease_id
            if not lease.rent_total:
                continue
            if term.amount_due > lease.rent_total + 0.01:
                raise ValidationError(_(
                    "Un terme ne peut pas excéder le loyer convenu de "
                    "%(rent)s. Réclamer d'un coup ce qui reste du bail est "
                    "sans effet (art. 1905 C.c.Q.), et exiger un versement "
                    "supérieur à un mois de loyer est interdit "
                    "(art. 1904 al. 1).",
                    rent=lease.rent_total,
                ))
