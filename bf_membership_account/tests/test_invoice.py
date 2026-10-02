from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .common import MembershipAccountCase, quiet_new_test_user


@tagged("post_install", "-at_install", "bf_membership_account")
class TestInvoice(MembershipAccountCase):

    def test_invoice_created_posted_and_linked(self):
        membership = self._membership(self.alice)
        move = self._invoice(membership)
        self.assertEqual(move.state, "posted", "Validée : le portail ne paie pas un brouillon.")
        self.assertEqual(move.move_type, "out_invoice")
        self.assertEqual(move.partner_id, self.alice)
        self.assertEqual(move.amount_untaxed, 70.0)
        self.assertEqual(move.invoice_user_id, self.agent)
        line = move.invoice_line_ids
        self.assertEqual(line.name, "Cotisation %s %s" % (self.type_person.name, membership.period_label))
        self.assertEqual(line.price_unit, 70.0)
        self.assertEqual(line.product_id, self.type_person.product_id)
        self.assertEqual(membership.payment_source, "invoice")
        self.assertEqual(membership.state, "waiting")
        self.assertTrue(membership.invoice_drives)

    def test_product_created_once_without_tax(self):
        self.assertFalse(self.type_person.product_id)
        self._invoice(self._membership(self.alice))
        product = self.type_person.product_id
        self.assertEqual(product.type, "service")
        self.assertFalse(product.taxes_id, "Aucune taxe d'office : l'organisme la pose s'il y a lieu.")
        self._invoice(self._membership(self.bruno))
        self.assertEqual(self.type_person.product_id, product, "Un seul article par catégorie.")

    def test_organization_invoiced_in_its_own_name(self):
        """🔴 La facture est toujours au nom du membre lui-même, jamais de son
        contact de facturation : ce contact se rattache ailleurs par une simple
        écriture, et le portail de l'autre entreprise verrait la facture."""
        org_type = self.type_org.copy({"code": "ORGF", "admission": "automatic"})
        move = self._invoice(self._membership(self.member_org, org_type))
        self.assertEqual(move.partner_id, self.member_org)
        third = self.env["res.partner"].create({"name": "Entreprise tierce (essai)", "is_company": True})
        portal = quiet_new_test_user(self.env, login="portail_tierce", groups="base.group_portal")
        portal.partner_id.parent_id = third
        self.org_billing.write({"parent_id": third.id})
        self._commit_like()
        self.assertEqual(move.partner_id, self.member_org)
        self.assertFalse(self.env["account.move"].with_user(portal).search([("move_type", "=", "out_invoice")]))

    def test_person_of_an_organization_is_not_invoiced(self):
        """🔴 Odoo porte toute facture au partenaire commercial : la cotisation
        personnelle d'une personne rattachée à une entreprise serait une créance
        de l'entreprise, lue au portail par ses collègues. Refusé, avec ce qu'il
        faut faire à la place."""
        membership = self._membership(self.org_employee)
        with self.assertRaises(UserError) as caught:
            membership.with_user(self.agent).action_create_invoice()
        self.assertIn("détachez", str(caught.exception))
        self._commit_like()
        self.assertFalse(membership.invoice_id)
        self.assertFalse(self.env["account.move"].search([("commercial_partner_id", "=", self.member_org.id)]))

    def test_cannot_invoice_twice_or_nothing(self):
        membership = self._membership(self.alice)
        self._invoice(membership)
        with self.assertRaises(UserError):
            membership.with_user(self.agent).action_create_invoice()
        free = self._membership(self.bruno, self.type_honorary)
        with self.assertRaises(UserError):
            free.with_user(self.agent).action_create_invoice()

    def test_renewal_does_not_inherit_the_invoice(self):
        membership = self._membership(self.alice)
        self._pay(self._invoice(membership))
        membership.action_renew()
        renewal = membership.renewal_ids
        self.assertFalse(renewal.invoice_id)
        self.assertFalse(renewal.payment_source)
        self.assertEqual(renewal.state, "waiting")


@tagged("post_install", "-at_install", "bf_membership_account")
class TestPaymentFollowsInvoice(MembershipAccountCase):
    """Le paiement suit la facture, par les vrais assistants d'Odoo.

    🔴 `payment_state` de la facture est un champ calculé stocké : il ne passe
    jamais par `write()`. Ces essais enregistrent un vrai paiement et de vrais
    avoirs, puis relisent la base.
    """

    def setUp(self):
        super().setUp()
        self.membership = self._membership(self.alice)
        self.move = self._invoice(self.membership)

    def test_real_payment_makes_member(self):
        payment_day = self._day(days=-3)
        self._pay(self.move, payment_day)
        self.assertIn(self.move.payment_state, ("paid", "in_payment"))
        self.assertEqual(self.membership.payment_state, "paid")
        self.assertEqual(self.membership.state, "active")
        self.assertEqual(self.membership.payment_date, payment_day, "La date du paiement, pas celle de la saisie.")
        self.assertEqual(self.membership.payment_reference, self.move.name)
        self.assertEqual(self.alice.member_status, "member")
        self._assert_nothing_sent(self.alice)

    def test_partial_payment_is_not_enough(self):
        wizard = self.env["account.payment.register"].with_user(self.accountant).with_context(
            active_model="account.move", active_ids=self.move.ids).create({"amount": 30.0})
        wizard._create_payments()
        self._commit_like()
        self.assertEqual(self.move.payment_state, "partial")
        self.assertEqual(self.membership.state, "waiting")

    def test_full_reversal_puts_back_to_pay(self):
        """Avoir d'annulation complète : Odoo lettre l'avoir avec la facture."""
        self._pay(self.move)
        self.assertEqual(self.membership.state, "active")
        reversal = self.env["account.move.reversal"].with_user(self.accountant).with_context(
            active_model="account.move", active_ids=self.move.ids).create({
                "reason": "Erreur de catégorie", "journal_id": self.move.journal_id.id})
        reversal.modify_moves()
        self._commit_like()
        self.assertEqual(self.move.payment_state, "reversed")
        self.assertEqual(self.membership.payment_state, "to_pay")
        self.assertEqual(self.membership.state, "waiting")
        self.assertFalse(self.membership.payment_date)

    def test_refund_of_a_paid_invoice_puts_back_to_pay(self):
        """Avoir de remboursement : la facture reste « payée » aux yeux d'Odoo."""
        self._pay(self.move)
        reversal = self.env["account.move.reversal"].with_user(self.accountant).with_context(
            active_model="account.move", active_ids=self.move.ids).create({
                "reason": "Remboursement", "journal_id": self.move.journal_id.id})
        reversal.refund_moves()
        credit_note = self.move.reversal_move_ids
        self.assertEqual(len(credit_note), 1)
        credit_note.with_user(self.accountant).action_post()
        self._commit_like()
        self.assertEqual(self.membership.payment_state, "to_pay")
        self.assertEqual(self.membership.state, "waiting")
        # Annuler l'avoir rend la cotisation payée de nouveau.
        credit_note.with_user(self.accountant).button_draft()
        self._commit_like()
        self.assertEqual(self.membership.state, "active")

    def test_cancelled_invoice_puts_back_to_pay_and_can_be_reinvoiced(self):
        self._pay(self.move)
        self.move.with_user(self.accountant).button_draft()
        self._commit_like()
        self.assertEqual(self.membership.state, "waiting")
        self.move.with_user(self.accountant).button_cancel()
        self._commit_like()
        self.assertEqual(self.membership.payment_state, "to_pay")
        self.assertFalse(self.membership.invoice_drives)
        new_move = self._invoice(self.membership)
        self.assertNotEqual(new_move, self.move)
        self._pay(new_move)
        self.assertEqual(self.membership.state, "active")

    def test_bounced_payment_puts_back_to_pay(self):
        payment = self._pay(self.move)
        self.assertEqual(self.membership.state, "active")
        payment.with_user(self.accountant).action_draft()
        payment.with_user(self.accountant).action_cancel()
        self._commit_like()
        self.assertEqual(self.move.payment_state, "not_paid")
        self.assertEqual(self.membership.state, "waiting")

    def test_manual_payment_refused_while_invoice_open(self):
        with self.assertRaises(UserError):
            self.membership.with_user(self.agent).action_mark_paid()
        with self.assertRaises(UserError):
            self.membership.with_user(self.agent).action_exempt()
        with self.assertRaises(UserError):
            self.membership.with_user(self.agent).write({"payment_state": "paid"})
        self.assertEqual(self.membership.payment_state, "to_pay")

    def test_paid_elsewhere_after_reversal(self):
        """L'avoir rend la main : le paiement reçu par chèque se note."""
        reversal = self.env["account.move.reversal"].with_user(self.accountant).with_context(
            active_model="account.move", active_ids=self.move.ids).create({
                "reason": "Payé par chèque", "journal_id": self.move.journal_id.id})
        reversal.modify_moves()
        self._commit_like()
        self.membership.with_user(self.agent).action_mark_paid()
        self.assertEqual(self.membership.state, "active")
        self.assertEqual(self.membership.payment_source, "other", "Plus « Facture » : elle ne paie plus rien.")


@tagged("post_install", "-at_install", "bf_membership_account")
class TestInvoiceAccess(MembershipAccountCase):

    def test_plain_employee_cannot_invoice(self):
        membership = self._membership(self.alice)
        with self.assertRaises(AccessError):
            membership.with_user(self.employee).action_create_invoice()
        self.assertFalse(membership.invoice_id)

    def test_agent_without_accounting_rights_invoices_but_does_not_open(self):
        membership = self._membership(self.alice)
        action = membership.with_user(self.agent).action_create_invoice()
        self.assertIs(action, True, "Pas de formulaire de facture pour qui ne peut pas le lire.")
        self.assertTrue(membership.invoice_id)
        self.assertEqual(membership.with_user(self.agent).invoice_payment_state, "not_paid",
                         "L'agent lit l'état de la facture sur l'adhésion.")


@tagged("post_install", "-at_install", "bf_membership_account")
class TestFrozenPayment(MembershipAccountCase):
    """🔴 Le reçu fiscal lit le montant et la date du paiement : ils ne
    changent plus une fois la cotisation facturée ou réglée."""

    def test_amount_frozen_once_invoiced(self):
        membership = self._membership(self.alice)
        self._invoice(membership)
        with self.assertRaises(UserError):
            membership.with_user(self.agent).write({"amount": 5000.0})
        membership.with_user(self.agent).write({"amount": 70.0})  # la même valeur reste permise
        self._commit_like()
        self.assertEqual(membership.amount, 70.0)

    def test_amount_and_date_frozen_once_paid(self):
        membership = self._membership(self.alice)
        self._pay(self._invoice(membership), self._day(days=-2))
        agent = membership.with_user(self.agent)
        changes = [{"amount": 900.0}, {"payment_date": self._day(years=-1)},
                   {"type_id": self.type_review.id}]  # la catégorie recalculerait la cotisation
        for vals in changes:
            with self.subTest(vals=vals), self.assertRaises(UserError):
                agent.write(vals)
        with self.assertRaises(UserError, msg="La clé de synchronisation ne vaut qu'en superutilisateur."):
            agent.with_context(bf_membership_invoice_sync=True).write({"amount": 900.0})
        agent.write({"amount": 70.0, "payment_date": self._day(days=-2)})
        self._commit_like()
        self.assertEqual((membership.amount, membership.payment_date, membership.type_id),
                         (70.0, self._day(days=-2), self.type_person))

    def test_manual_payment_frozen_once_paid(self):
        membership = self._membership(self.alice)
        membership.with_user(self.agent).write({"payment_source": "cheque", "payment_date": self._day(days=-1)})
        membership.with_user(self.agent).action_mark_paid()
        with self.assertRaises(UserError):
            membership.with_user(self.agent).write({"payment_date": self._day(years=-1)})
        with self.assertRaises(UserError):
            membership.with_user(self.agent).write({"amount": 5000.0})


@tagged("post_install", "-at_install", "bf_membership_account")
class TestInvoiceGuards(MembershipAccountCase):

    def test_create_ignores_a_forged_invoice_default(self):
        """🔴 L'ORM applique `default_invoice_id` du contexte APRÈS les contrôles
        des valeurs : un appel direct lierait la facture d'un autre client."""
        foreign = self._invoice(self._membership(self.bruno))
        self.env.invalidate_all()
        created = self.env["bf.membership"].with_user(self.agent).with_context(
            default_invoice_id=foreign.id).create({"partner_id": self.denis.id, "type_id": self.type_person.id})
        self._commit_like()
        self.assertFalse(created.invoice_id)
        self.assertFalse(created.with_user(self.agent).invoice_payment_state)

    def test_invoice_link_only_by_the_module(self):
        """🔴 `readonly` ne protège que la vue : par RPC, un agent lierait la
        facture d'un autre client et en lirait le nom et l'état."""
        other = self._membership(self.bruno)
        foreign = self._invoice(other)
        membership = self._membership(self.alice)
        self.env.invalidate_all()
        for user in (self.agent, self.manager):
            with self.subTest(user=user.login), self.assertRaises(AccessError):
                membership.with_user(user).write({"invoice_id": foreign.id})
        with self.assertRaises(AccessError):
            self.env["bf.membership"].with_user(self.agent).create({
                "partner_id": self.denis.id, "type_id": self.type_person.id, "invoice_id": foreign.id})
        self._commit_like()
        self.assertFalse(membership.invoice_id)
        other.with_user(self.agent).write({"invoice_id": foreign.id})  # la même valeur reste permise

    def test_parent_company_given_to_an_invoiced_organization_is_refused(self):
        """🔴 Une société mère donnée par RPC à une organisation facturée ouvrirait
        sa facture au portail de la société mère. Sans le rôle, refus neutre ;
        avec le rôle, refus qui dit pourquoi."""
        org_type = self.type_org.copy({"code": "ORGP", "admission": "automatic"})
        move = self._invoice(self._membership(self.member_org, org_type))
        parent = self.env["res.partner"].create({"name": "Société mère (essai)", "is_company": True})
        contacts = quiet_new_test_user(self.env, login="gestion_contacts_mere",
                                       groups="base.group_user,base.group_partner_manager")
        with self.assertRaises(UserError) as caught:
            self.member_org.with_user(contacts).write({"parent_id": parent.id})
        self.assertIn("avec vos droits", str(caught.exception))
        for word in ("adhésion", "facture", "cotisation"):
            self.assertNotIn(word, str(caught.exception))
        agent_contacts = quiet_new_test_user(self.env, login="agent_contacts_mere",
                                             groups="bf_membership.group_membership_user,base.group_partner_manager")
        with self.assertRaises(UserError) as caught:
            self.member_org.with_user(agent_contacts).write({"parent_id": parent.id})
        self.assertIn("société mère", str(caught.exception))
        self._commit_like()
        self.assertFalse(self.member_org.parent_id)
        self.assertEqual(move.partner_id, self.member_org)

    def test_invoiced_organization_not_merged_under_another_company(self):
        """🔴 Fusionner l'organisation facturée DANS un doublon rattaché à une
        autre entreprise ferait passer sa facture sous cette entreprise."""
        org_type = self.type_org.copy({"code": "ORGM", "admission": "automatic"})
        move = self._invoice(self._membership(self.member_org, org_type))
        third = self.env["res.partner"].create({"name": "Entreprise tierce (essai)", "is_company": True})
        self.member_org.email = "organisation@essai.example"
        duplicate = self.env["res.partner"].create({
            "name": "Organisation membre (doublon)", "is_company": True, "parent_id": third.id,
            "email": "organisation@essai.example"})
        merger = quiet_new_test_user(
            self.env, login="fusion_membres",
            groups="bf_membership.group_membership_manager,base.group_partner_manager,account.group_account_invoice")
        wizard = self.env["base.partner.merge.automatic.wizard"].with_user(merger).create({
            "partner_ids": [Command.set((self.member_org | duplicate).ids)], "dst_partner_id": duplicate.id})
        with self.assertRaises(UserError) as caught:
            wizard._merge((self.member_org | duplicate).ids, duplicate)
        self.assertIn("facture de cotisation", str(caught.exception))
        self._commit_like()
        self.assertTrue(self.member_org.exists())
        self.assertEqual(move.partner_id, self.member_org)

    def test_identity_frozen_while_invoiced(self):
        """Une facture validée fige le membre et la catégorie, même impayée :
        payée, elle ferait membre une autre personne que celle facturée."""
        membership = self._membership(self.alice)
        self._invoice(membership)
        self.assertEqual(membership.state, "waiting")
        for vals in ({"partner_id": self.bruno.id}, {"type_id": self.type_review.id}):
            with self.subTest(vals=vals), self.assertRaises(UserError):
                membership.with_user(self.agent).write(vals)
        self._commit_like()
        self.assertEqual((membership.partner_id, membership.type_id), (self.alice, self.type_person))

    def test_payment_source_frozen_while_invoiced(self):
        """🔴 Une seule vérité : facture ouverte, la source ne change pas, et un
        paiement noté « par chèque » dans la même écriture est refusé."""
        membership = self._membership(self.alice)
        move = self._invoice(membership)
        agent = membership.with_user(self.agent)
        with self.assertRaises(UserError):
            agent.write({"payment_source": "cheque", "payment_state": "paid"})
        with self.assertRaises(UserError):
            agent.write({"payment_source": "cheque"})
        self._commit_like()
        self.assertEqual((membership.payment_source, membership.payment_state), ("invoice", "to_pay"))
        move.with_user(self.accountant).button_draft()
        move.with_user(self.accountant).button_cancel()
        self._commit_like()
        agent.write({"payment_source": "cheque"})  # la facture annulée ne porte plus rien
        self.assertEqual(membership.payment_source, "cheque")

    def test_agent_invoices_only_the_category_fee(self):
        """L'agent sans droits comptables ne valide pas une facture au montant
        de son choix : une cotisation particulière passe par la personne
        responsable ou par la comptabilité."""
        membership = self._membership(self.alice)
        membership.with_user(self.agent).write({"amount": 5000.0})
        with self.assertRaises(AccessError):
            membership.with_user(self.agent).action_create_invoice()
        self._commit_like()
        self.assertFalse(membership.invoice_id)
        self.assertFalse(self.env["account.move"].search([("partner_id", "=", self.alice.id)]))
        move = self._invoice(membership, user=self.agent_billing)
        self.assertEqual(move.amount_total, 5000.0)

    def test_invoiced_membership_neither_refused_nor_deleted(self):
        """Refuser ou supprimer l'adhésion laisserait une créance ouverte."""
        membership = self._membership(self.alice)
        move = self._invoice(membership)
        self.assertFalse(membership._refusable())
        with self.assertRaises(UserError):
            membership.with_user(self.agent).action_refuse()
        request = self._membership(self.member_org, self.type_org)
        self.assertEqual(request.state, "draft")
        self._invoice(request)
        with self.assertRaises(UserError):
            request.with_user(self.manager).unlink()
        move.with_user(self.accountant).button_draft()
        move.with_user(self.accountant).button_cancel()
        self._commit_like()
        membership.with_user(self.agent).action_refuse()
        self.assertEqual(membership.state, "refused")

    def test_pdf_task_does_not_mark_the_invoice_sent(self):
        """Produire le PDF officiel n'envoie rien : la facture n'est pas « envoyée »."""
        move = self._invoice(self._membership(self.alice))
        self.assertFalse(move.is_move_sent)
        self.env["bf.membership"]._cron_invoice_pdf()
        self._commit_like()
        self.assertTrue(move.invoice_pdf_report_id, "Le PDF officiel est produit.")
        self.assertFalse(move.is_move_sent)
        self._assert_nothing_sent(self.alice)


@tagged("post_install", "-at_install", "bf_membership_account")
class TestTrace(MembershipAccountCase):
    """🔴 Les clés de contexte qui effacent la trace (`tracking_disable`,
    `mail_notrack`, `mail_create_nolog`) ne valent que pour le
    superutilisateur lui-même, même quand le module écrit en superutilisateur
    pour le compte d'une personne."""

    NO_TRACE = {"tracking_disable": True, "mail_notrack": True, "mail_create_nolog": True}

    def _tracked(self, record, field):
        self.env.flush_all()
        self.env.cr.precommit.run()
        self.env.invalidate_all()
        return record.message_ids.tracking_value_ids.filtered(lambda t: t.field_id.name == field)

    def _end_request(self):
        """La fin d'une requête : Odoo écarte le suivi d'un enregistrement créé
        dans la transaction (et le décor crée sous `tracking_disable`) jusqu'à
        sa fin. Délivrer puis annuler un reçu, ce sont deux requêtes."""
        self.env.flush_all()
        self.env.cr.precommit.run()

    def test_traces_kept_on_membership_receipt_and_category(self):
        membership = self._membership(self.alice)
        self._end_request()
        membership.with_user(self.agent).with_context(**self.NO_TRACE).action_create_invoice()
        self.assertTrue(self._tracked(membership, "invoice_id"), "Adhésion : la facture liée laisse sa trace.")
        self._pay(membership.invoice_id)
        membership.with_user(self.agent).action_issue_receipt()
        receipt = membership.receipt_ids
        self._end_request()
        self.env["bf.membership.receipt.cancel"].with_user(self.agent).with_context(**self.NO_TRACE).create({
            "receipt_id": receipt.id, "reason": "Erreur", "replace": False}).action_confirm()
        self.assertEqual(receipt.state, "cancelled")
        self.assertTrue(self._tracked(receipt, "state"), "Reçu : l'annulation laisse sa trace.")
        self.type_review.with_user(self.manager).with_context(**self.NO_TRACE).write({"advantage_amount": 20.0})
        self.assertTrue(self._tracked(self.type_review, "advantage_amount"), "Catégorie : le changement laisse sa trace.")
