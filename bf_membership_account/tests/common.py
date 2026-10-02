from unittest.mock import patch

from odoo import fields
from odoo.tests import new_test_user

from odoo.addons.bf_membership.tests import common as membership_common
from odoo.addons.bf_membership.tests.common import MembershipCase


def quiet_new_test_user(env, login="", groups="base.group_user", context=None, **kwargs):
    """`new_test_user`, sans l'invitation à s'inscrire.

    🔴 `account` amène `portal`, qui amène `auth_signup` : chaque usager créé
    reçoit alors une invitation par courriel, envoyée de force
    (`force_send`). Le décor du socle crée ses usagers sans cette précaution,
    parce que le socle n'installe pas `auth_signup`. Aucun courriel ne part
    d'un essai : on la prend ici, sans toucher au socle.
    """
    context = dict(context or {}, no_reset_password=True)
    return new_test_user(env, login=login, groups=groups, context=context, **kwargs)


class MembershipAccountCase(MembershipCase):
    """Le décor du socle, avec une comptabilité et un organisme de bienfaisance.

    L'agent n'a AUCUN droit de facturation : c'est la forme réelle d'une
    permanence qui accueille les membres sans tenir les livres. Les paiements
    et les avoirs, eux, sont saisis par une personne de la comptabilité, avec
    les vrais assistants d'Odoo.
    """

    @classmethod
    def setUpClass(cls):
        with patch.object(membership_common, "new_test_user", quiet_new_test_user):
            super().setUpClass()
        if not cls.company.chart_template:
            cls.env["account.chart.template"].try_loading("generic_coa", company=cls.company, install_demo=False)
        cls.company.write({
            "street": "12, rue des Essais",
            "city": "Ville-Essai",
            "zip": "A1A 1A1",
            "country_id": cls.env.ref("base.ca").id,
            "membership_charity_number": "123456789 RR 0001",
            "membership_receipt_place": False,
            "membership_receipt_signer": "Claire Trésorière",
            "membership_receipt_signer_title": "Trésorière",
        })
        cls.type_person.write({"receipt_eligible": True, "advantage_amount": 0.0})
        cls.type_review = cls.env["bf.membership.type"].create({
            "name": "Membre avec revue (essai)",
            "code": "REV",
            "member_kind": "person",
            "fee": 100.0,
            "period_mode": "rolling",
            "duration_months": 12,
            "receipt_eligible": True,
            "advantage_amount": 30.0,
            "advantage_description": "Abonnement à la revue",
        })
        cls.alice.write({"street": "1, rue Alice", "city": "Ville-Essai", "zip": "A1A 1A1"})
        cls.bruno.write({"street": "2, rue Bruno", "city": "Bourg-Exemple"})
        # Une personne seule, sans adresse : le décor du socle rattache ses
        # autres personnes à une organisation, qui ne se facturent pas.
        cls.dora = cls.env["res.partner"].create({"name": "Dora Essai"})
        cls.member_org = cls.env["res.partner"].create({"name": "Organisation membre (essai)", "is_company": True})
        cls.org_employee = cls.env["res.partner"].create({
            "name": "Personne salariée (essai)", "parent_id": cls.member_org.id})
        cls.org_billing = cls.env["res.partner"].create({
            "name": "Service de la comptabilité", "type": "invoice", "parent_id": cls.member_org.id,
            "email": "compta@organisation.example",
        })
        cls.accountant = quiet_new_test_user(
            cls.env, login="compta_membres", name="Comptable des essais",
            groups="account.group_account_manager,base.group_user")
        # Un agent qui tient aussi la facturation : il délivre le reçu d'un
        # paiement noté à la main, comme la personne responsable.
        cls.agent_billing = quiet_new_test_user(
            cls.env, login="agent_facturation", name="Agent et facturation",
            groups="bf_membership.group_membership_user,account.group_account_invoice")

    # ------------------------------------------------------------------

    def _invoice(self, membership, user=None):
        membership.with_user(user or self.agent).action_create_invoice()
        return membership.invoice_id

    def _pay(self, move, date=None):
        wizard = self.env["account.payment.register"].with_user(self.accountant).with_context(
            active_model="account.move", active_ids=move.ids).create({
                "payment_date": date or fields.Date.context_today(move),
            })
        payments = wizard._create_payments()
        self._commit_like()
        return payments

    def _assert_nothing_sent(self, partners):
        """Aucun courriel en file ni notification par courriel pour ces contacts."""
        self.assertFalse(self.env["mail.mail"].search([("recipient_ids", "in", partners.ids)]))
        self.assertFalse(self.env["mail.notification"].search([
            ("res_partner_id", "in", partners.ids), ("notification_type", "=", "email")]))

    def _commit_like(self):
        """Ce que fait la fin de chaque requête : vider les calculs en attente,
        puis relire la base. Une valeur qui ne vivrait que dans le cache ne
        passerait pas ce point."""
        self.env.flush_all()
        self.env.invalidate_all()

    def _render_receipt(self, receipt, user=None):
        report = self.env["ir.actions.report"].with_user(user or self.agent)
        html, _type = report._render_qweb_pdf("bf_membership_account.report_membership_receipt", receipt.ids)
        self._commit_like()
        return html.decode() if isinstance(html, bytes) else html
