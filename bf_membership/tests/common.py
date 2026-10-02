from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.tests.common import TransactionCase, new_test_user


class MembershipCase(TransactionCase):
    """Décor commun : les deux formes qu'une association rencontre.

    * des personnes, sur un exercice du 1er avril au 31 mars, à 70 $ ;
    * des organisations, sur une période glissante de douze mois, avec deux
      délégués désignés et une seule voix (la forme d'un regroupement dont
      les membres sont des organismes).
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.today = fields.Date.context_today(cls.env["res.company"])
        cls.company = cls.env.company

        cls.type_person = cls.env["bf.membership.type"].create({
            "name": "Membre régulier (essai)",
            "code": "REG",
            "member_kind": "person",
            "fee": 70.0,
            "period_mode": "fixed",
            "period_start_month": "4",
            "period_start_day": 1,
            "grace_days": 30,
        })
        cls.type_org = cls.env["bf.membership.type"].create({
            "name": "Membre organisation (essai)",
            "code": "ORG",
            "member_kind": "organization",
            "fee": 250.0,
            "period_mode": "rolling",
            "duration_months": 12,
            "delegate_count": 2,
            "admission": "decision",
        })
        cls.type_honorary = cls.env["bf.membership.type"].create({
            "name": "Membre honoraire (essai)",
            "code": "HON",
            "member_kind": "person",
            "fee": 0.0,
            "period_mode": "lifetime",
            "voting": False,
        })

        cls.alice = cls.env["res.partner"].create({
            "name": "Alice Essai", "email": "alice@essai.example", "zip": "A1A 1A1",
            "function": "Retraitée",
        })
        cls.bruno = cls.env["res.partner"].create({"name": "Bruno Essai", "email": "bruno@essai.example"})
        cls.member_org = cls.env["res.partner"].create({"name": "Organisme Les Essais", "is_company": True})
        cls.carole = cls.env["res.partner"].create({"name": "Carole Essai", "parent_id": cls.member_org.id})
        cls.denis = cls.env["res.partner"].create({"name": "Denis Essai", "parent_id": cls.member_org.id})
        cls.emma = cls.env["res.partner"].create({"name": "Emma Essai", "parent_id": cls.member_org.id})

        # Sans invitation : dès qu'un greffon amène `auth_signup`, chaque usager
        # créé déclencherait un courriel d'invitation.
        quiet = {"no_reset_password": True}
        cls.agent = new_test_user(
            cls.env, login="agent_membres", groups="bf_membership.group_membership_user",
            name="Agent des membres", context=quiet)
        cls.manager = new_test_user(
            cls.env, login="resp_membres", groups="bf_membership.group_membership_manager",
            name="Responsable des membres", context=quiet)
        cls.employee = new_test_user(cls.env, login="employe_simple", groups="base.group_user", context=quiet)

    @classmethod
    def _membership(cls, partner, mtype=None, **kw):
        vals = {
            "partner_id": partner.id,
            "type_id": (mtype or cls.type_person).id,
        }
        vals.update(kw)
        return cls.env["bf.membership"].create(vals)

    def _day(self, **delta):
        return self.today + relativedelta(**delta)

    def _recompute_status(self, partners):
        self.env.add_to_compute(self.env["res.partner"]._fields["member_status"], partners)
        self.env.add_to_compute(self.env["res.partner"]._fields["current_membership_id"], partners)
        partners.flush_recordset()
        partners.invalidate_recordset()
