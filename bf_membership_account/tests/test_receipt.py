from datetime import date

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from odoo.addons.bf_membership_account.models.membership_type import csp_m05_eligible

from .common import MembershipAccountCase


@tagged("post_install", "-at_install", "bf_membership_account")
class TestEligibleAmount(MembershipAccountCase):
    """Les trois tranches de la politique CSP-M05, bornes comprises."""

    def _eligible(self, amount, advantage):
        return csp_m05_eligible(amount, advantage, self.company.currency_id)

    def test_no_advantage_full_receipt(self):
        self.assertEqual(self._eligible(70.0, 0.0), (70.0, False))

    def test_de_minimis_ten_percent_bound_included(self):
        """Cotisation de 100 $ : le seuil est 10 $ (10 % < 75 $)."""
        self.assertEqual(self._eligible(100.0, 10.0), (100.0, True))
        self.assertEqual(self._eligible(100.0, 10.01), (89.99, False))

    def test_de_minimis_75_dollars_bound_included(self):
        """Cotisation de 1 000 $ : le seuil est 75 $ (75 $ < 10 %)."""
        self.assertEqual(self._eligible(1000.0, 75.0), (1000.0, True))
        self.assertEqual(self._eligible(1000.0, 75.01), (924.99, False))

    def test_eighty_percent_bound_included(self):
        self.assertEqual(self._eligible(100.0, 80.0), (20.0, False))
        self.assertEqual(self._eligible(100.0, 80.01), (0.0, False))

    def test_category_amount(self):
        self.assertEqual(self.type_review.receipt_eligible_amount, 70.0)
        self.type_review.receipt_eligible = False
        self.assertEqual(self.type_review.receipt_eligible_amount, 0.0)


@tagged("post_install", "-at_install", "bf_membership_account")
class TestReceipt(MembershipAccountCase):

    def _paid(self, partner, mtype=None):
        membership = self._membership(partner, mtype)
        self._pay(self._invoice(membership), self._day(days=-2))
        return membership

    def _issue(self, membership, user=None):
        membership.with_user(user or self.agent).action_issue_receipt()
        return membership.receipt_ids.filtered(lambda r: r.state == "issued")

    def test_receipt_carries_every_mention(self):
        membership = self._paid(self.alice, self.type_review)
        receipt = self._issue(membership)
        self.assertEqual(receipt.eligible_amount, 70.0)
        self.assertEqual(receipt.date_received, self._day(days=-2))
        html = self._render_receipt(receipt)
        for text in ("Reçu officiel aux fins de l'impôt sur le revenu",
                     self.company.name, "12, rue des Essais",
                     "123456789 RR 0001", receipt.name, "Ville-Essai",
                     "Alice Essai", "1, rue Alice",
                     "Abonnement à la revue", "Claire Trésorière",
                     "Agence du revenu du Canada", "canada.ca/organismes-bienfaisance-dons"):
            self.assertIn(text, html, "Mention absente du reçu : %s" % text)
        self.assertNotIn("DUPLICATA", html)
        self._assert_nothing_sent(self.alice)

    def test_second_delivery_is_a_duplicate_with_the_same_number(self):
        receipt = self._issue(self._paid(self.alice))
        self._render_receipt(receipt)
        again = self._issue(receipt.membership_id)
        self.assertEqual(again, receipt, "Un seul reçu par cotisation.")
        html = self._render_receipt(receipt)
        self.assertIn("DUPLICATA", html)
        self.assertIn(receipt.name, html)
        self.assertEqual(receipt.delivery_count, 2)

    def test_numbers_unique_and_without_gap_per_company(self):
        first = self._issue(self._paid(self.alice))
        second = self._issue(self._paid(self.bruno))
        year = first.date_issued.year
        self.assertEqual(first.name, "%s-00001" % year)
        self.assertEqual(second.name, "%s-00002" % year)

        other = self.env["res.company"].create({
            "name": "Autre organisme (essai)", "street": "3, rue Autre", "city": "Val-Exemple",
            "zip": "B2B 2B2", "country_id": self.env.ref("base.ca").id,
            "membership_charity_number": "987654321 RR 0001", "membership_receipt_signer": "Autre signataire"})
        other_type = self.type_person.copy({"company_id": other.id, "code": "REGX"})
        elsewhere = self.env["bf.membership"].create({
            "partner_id": self.alice.id, "type_id": other_type.id, "company_id": other.id,
            "payment_state": "paid", "payment_source": "cheque", "payment_date": self.today})
        receipt = elsewhere._issue_receipt()
        self.assertEqual(receipt.name, "%s-00001" % year, "Chaque société a sa série.")
        self.assertEqual(receipt.registration_number, "987654321 RR 0001")

    def test_refused_when_unpaid_exempt_or_not_eligible(self):
        unpaid = self._membership(self.alice)
        with self.assertRaises(UserError):
            unpaid.with_user(self.agent).action_issue_receipt()
        exempt = self._membership(self.bruno, self.type_honorary)
        exempt.action_exempt()
        with self.assertRaises(UserError):
            exempt.with_user(self.agent).action_issue_receipt()
        self.type_review.receipt_eligible = False
        not_eligible = self._paid(self.dora, self.type_review)
        with self.assertRaises(UserError):
            not_eligible.with_user(self.agent).action_issue_receipt()
        self.assertFalse(self.env["bf.membership.receipt"].search([]))

    def test_refused_when_advantage_over_eighty_percent(self):
        self.type_review.advantage_amount = 85.0
        membership = self._paid(self.alice, self.type_review)
        with self.assertRaises(UserError):
            membership.with_user(self.agent).action_issue_receipt()

    def test_refused_without_member_address(self):
        """L'adresse manquait au paiement : compléter le contact ne suffit pas,
        une personne responsable met à jour le nom et l'adresse au reçu."""
        membership = self._paid(self.dora)
        with self.assertRaises(UserError):
            membership.with_user(self.agent).action_issue_receipt()
        self.dora.write({"street": "4, rue Dora", "city": "Ville-Essai"})
        with self.assertRaises(UserError):
            membership.with_user(self.agent).action_issue_receipt()
        with self.assertRaises(AccessError):
            membership.with_user(self.agent).action_refresh_donor()
        membership.with_user(self.manager).action_refresh_donor()
        receipt = self._issue(membership)
        self.assertIn("4, rue Dora", receipt.donor_address)

    def test_receipt_is_frozen(self):
        receipt = self._issue(self._paid(self.alice))
        self.alice.write({"street": "99, rue Nouvelle"})
        self.assertEqual(receipt.donor_address.splitlines()[0], "1, rue Alice")
        with self.assertRaises(UserError):
            receipt.with_user(self.manager).write({"eligible_amount": 1.0})
        with self.assertRaises(UserError):
            receipt.with_user(self.manager).unlink()

    def test_replacement_gets_its_own_number_and_names_the_original(self):
        """Art. 3501 (4) du Règlement : le reçu de remplacement a son numéro."""
        original = self._issue(self._paid(self.alice))
        self._render_receipt(original)
        wizard = self.env["bf.membership.receipt.cancel"].with_user(self.agent).create({
            "receipt_id": original.id, "reason": "Reçu perdu", "replace": True})
        wizard.action_confirm()
        self.assertEqual(original.state, "cancelled")
        replacement = original.replaced_by_ids
        self.assertEqual(len(replacement), 1)
        self.assertNotEqual(replacement.name, original.name)
        html = self._render_receipt(replacement)
        self.assertIn("annule et remplace le reçu n°", html)
        self.assertIn(original.name, html)
        self.assertNotIn("DUPLICATA", html)
        cancelled_copy = self._render_receipt(original)
        self.assertIn("ANNULÉ", cancelled_copy)

    def test_reversed_payment_blocks_duplicates(self):
        membership = self._paid(self.alice)
        receipt = self._issue(membership)
        self._render_receipt(receipt)
        reversal = self.env["account.move.reversal"].with_user(self.accountant).with_context(
            active_model="account.move", active_ids=membership.invoice_id.ids).create({
                "reason": "Remboursé", "journal_id": membership.invoice_id.journal_id.id})
        reversal.modify_moves()
        self._commit_like()
        self.assertEqual(membership.payment_state, "to_pay")
        self.assertIn(receipt.name, membership.message_ids[:1].body, "La note demande d'annuler le reçu.")
        with self.assertRaises(UserError):
            self._render_receipt(receipt)

    def test_paid_by_cheque_also_receipted(self):
        """Le reçu ne dépend pas de la facture : un chèque noté suffit."""
        membership = self._membership(self.alice)
        membership.write({"payment_source": "cheque", "payment_date": date.today()})
        membership.action_mark_paid()
        receipt = self._issue(membership, user=self.manager)
        self.assertEqual(receipt.eligible_amount, 70.0)

    def test_receipt_amount_and_date_come_from_the_invoice(self):
        """🔴 Facturée, la cotisation se reçoit pour ce que la facture a encaissé,
        à la date de ses pièces. Une date changée sur l'adhésion (ici en base,
        comme par un chemin oublié) ne passe pas au reçu ; un montant qui ne
        concorde plus avec la ligne de la facture fait refuser le reçu."""
        membership = self._paid(self.alice)
        self.env.cr.execute("UPDATE bf_membership SET payment_date = '2020-06-01' WHERE id = %s", [membership.id])
        self.env.invalidate_all()
        receipt = self._issue(membership)
        self.assertEqual(receipt.gift_amount, 70.0)
        self.assertEqual(receipt.eligible_amount, 70.0)
        self.assertEqual(receipt.date_received, self._day(days=-2))
        other = self._paid(self.bruno)
        self.env.cr.execute("UPDATE bf_membership SET amount = 900 WHERE id = %s", [other.id])
        self.env.invalidate_all()
        with self.assertRaises(UserError):
            other.with_user(self.agent).action_issue_receipt()

    def test_receipt_refused_when_member_or_category_left_the_invoice(self):
        """🔴 Le reçu d'une cotisation facturée part au membre de la facture,
        pour l'article de la catégorie. Un membre ou une catégorie changés après
        coup (ici en base, comme par un chemin oublié) ne reçoivent rien."""
        membership = self._paid(self.alice)
        for column, value in (("partner_id", self.bruno.id), ("type_id", self.type_review.id)):
            with self.subTest(column=column):
                self.env.cr.execute(
                    "UPDATE bf_membership SET %s = %%s WHERE id = %%s" % column, [value, membership.id])
                self.env.invalidate_all()
                with self.assertRaises(UserError):
                    membership.with_user(self.agent).action_issue_receipt()
                self.env.cr.execute(
                    "UPDATE bf_membership SET partner_id = %s, type_id = %s WHERE id = %s",
                    [self.alice.id, self.type_person.id, membership.id])
                self.env.invalidate_all()
        self.assertFalse(self.env["bf.membership.receipt"].search([]))
        self.assertEqual(len(self._issue(membership)), 1, "Rétablie, l'adhésion reçoit son reçu.")

    def test_donor_frozen_at_payment(self):
        """🔴 Le reçu porte le nom et l'adresse figés au paiement : un agent qui
        renomme le contact entre le paiement et la délivrance, puis remet le
        nom, ne fait pas délivrer un reçu au nom d'un tiers."""
        membership = self._paid(self.alice)
        self.alice.write({"name": "Tiers Usurpé", "street": "66, rue Tierce"})
        receipt = self._issue(membership)
        self.alice.write({"name": "Alice Essai", "street": "1, rue Alice"})
        self.assertEqual(receipt.donor_name, "Alice Essai")
        self.assertIn("1, rue Alice", receipt.donor_address)
        self.assertNotIn("Tierce", receipt.donor_address)
        with self.assertRaises(AccessError):
            membership.with_user(self.manager).write({"donor_name": "Tiers Usurpé"})

    def test_frozen_address_has_no_blank_lines(self):
        """Le gabarit d'adresse d'Odoo laisse une ligne vide par champ absent et
        des espaces derrière la ville : le reçu n'en garde rien."""
        membership = self._paid(self.alice)
        address = membership.donor_address
        self.assertTrue(address)
        self.assertNotIn("\n\n", address)
        self.assertEqual(address, address.strip())
        for line in address.splitlines():
            self.assertEqual(line, line.strip(), "Une ligne de l'adresse garde des espaces : %r" % line)

    def test_missing_snapshots_filled_by_migration(self):
        """La migration fige le donateur des adhésions déjà payées."""
        membership = self._paid(self.alice)
        self.env.cr.execute("UPDATE bf_membership SET donor_name = NULL, donor_address = NULL WHERE id = %s",
                            [membership.id])
        self.env.invalidate_all()
        self.env["bf.membership"]._fill_missing_donor_snapshots()
        self._commit_like()
        self.assertEqual(membership.donor_name, "Alice Essai")
        self.assertIn("1, rue Alice", membership.donor_address)

    def test_receipt_refused_when_the_invoice_carries_other_lines(self):
        """🔴 La comptabilité remet la facture de cotisation en brouillon, y ajoute
        un billet de souper-bénéfice, la valide et l'encaisse : le reçu
        certifierait le tout comme cotisation. Seule une facture de cotisation
        seule donne un reçu."""
        membership = self._membership(self.alice)
        move = self._invoice(membership)
        move.with_user(self.accountant).button_draft()
        move.with_user(self.accountant).write({"invoice_line_ids": [Command.create({
            "name": "Billet souper-bénéfice", "quantity": 1.0, "price_unit": 500.0})]})
        move.with_user(self.accountant).action_post()
        self._pay(move)
        self.assertEqual(membership.payment_state, "paid")
        with self.assertRaises(UserError) as caught:
            membership.with_user(self.agent).action_issue_receipt()
        self.assertIn("facture de cotisation seule", str(caught.exception))
        self.assertFalse(self.env["bf.membership.receipt"].search([]))

    def test_receipt_refused_when_the_fee_line_was_changed(self):
        """🔴 La comptabilité remet la facture en brouillon et touche la ligne de
        l'article de cotisation : une seconde ligne de l'article, une quantité
        de 2, un prix de 500 $. Le reçu certifierait le tout comme cotisation."""
        eve = self.env["res.partner"].create({"name": "Eve Essai", "street": "5, rue Eve", "city": "Ville-Essai"})
        variants = [
            ("seconde ligne", self.alice, lambda move: move.write({"invoice_line_ids": [Command.create({
                "product_id": self.type_person.product_id.id, "name": "Supplément",
                "quantity": 1.0, "price_unit": 450.0})]})),
            ("quantité", self.bruno, lambda move: move.invoice_line_ids.write({"quantity": 2.0})),
            ("prix", eve, lambda move: move.invoice_line_ids.write({"price_unit": 500.0})),
        ]
        for label, partner, change in variants:
            with self.subTest(variant=label):
                membership = self._membership(partner)
                move = self._invoice(membership)
                move.with_user(self.accountant).button_draft()
                change(move.with_user(self.accountant))
                move.with_user(self.accountant).action_post()
                self._pay(move)
                self.assertEqual(membership.payment_state, "paid")
                with self.assertRaises(UserError) as caught:
                    membership.with_user(self.agent).action_issue_receipt()
                self.assertIn("facture de cotisation seule", str(caught.exception))
        self.assertFalse(self.env["bf.membership.receipt"].search([]))

    # ------------------------------------------------------------------
    # 🔴 Le reçu certifie l'encaissé : l'argent reçu, égal à la cotisation.
    # ------------------------------------------------------------------

    def _assert_no_cash_receipt(self, membership):
        self.assertEqual(membership.payment_state, "paid", "Odoo tient la facture pour payée.")
        with self.assertRaises(UserError) as caught:
            membership.with_user(self.agent).action_issue_receipt()
        self.assertIn("argent reçu", str(caught.exception))
        self.assertFalse(membership.receipt_ids)

    def test_receipt_refused_after_a_written_off_difference(self):
        """40 $ reçus sur 70 $, la facture « marquée payée en entier » (l'écart
        radié en escompte accordé) : le lettrage dit 70 $, l'argent reçu 40 $."""
        membership = self._membership(self.alice)
        move = self._invoice(membership)
        writeoff = self.env["account.account"].search([("account_type", "=", "expense")], limit=1)
        self.env["account.payment.register"].with_user(self.accountant).with_context(
            active_model="account.move", active_ids=move.ids).create({
                "amount": 40.0, "payment_difference_handling": "reconcile",
                "writeoff_account_id": writeoff.id, "writeoff_label": "Escompte accordé",
            })._create_payments()
        self._commit_like()
        self.assertEqual(move.payment_state, "paid")
        self._assert_no_cash_receipt(membership)

    def test_receipt_refused_after_an_early_payment_discount(self):
        """L'escompte pour paiement anticipé d'Odoo : 10 % de moins, la facture
        payée en entier."""
        membership = self._membership(self.alice)
        move = self._invoice(membership)
        term = self.env["account.payment.term"].create({
            "name": "Escompte 10 % (essai)", "early_discount": True,
            "discount_percentage": 10.0, "discount_days": 10,
            "line_ids": [Command.create({"value": "percent", "value_amount": 100.0, "nb_days": 30})],
        })
        move.with_user(self.accountant).button_draft()
        move.with_user(self.accountant).write({"invoice_payment_term_id": term.id})
        move.with_user(self.accountant).action_post()
        self._pay(move)
        self.assertEqual(move.payment_state, "paid")
        self.assertTrue(move.matched_payment_ids.filtered(lambda p: p.amount < 70.0), "L'escompte a joué.")
        self._assert_no_cash_receipt(membership)

    def test_receipt_refused_after_a_credit_note(self):
        """Un avoir de la facture, remboursé à part : le membre n'a plus versé la
        cotisation entière."""
        membership = self._paid(self.alice)
        move = membership.invoice_id
        reversal = self.env["account.move.reversal"].with_user(self.accountant).with_context(
            active_model="account.move", active_ids=move.ids).create({
                "reason": "Remboursement partiel", "journal_id": move.journal_id.id})
        reversal.refund_moves()
        credit_note = move.reversal_move_ids
        credit_note.with_user(self.accountant).invoice_line_ids.price_unit = 20.0
        credit_note.with_user(self.accountant).action_post()
        self._commit_like()
        self._assert_no_cash_receipt(membership)

    def test_receipt_refused_after_a_credit_note_entered_separately(self):
        """Un avoir saisi à part, sans lien avec la facture, au même membre pour
        l'article de cotisation."""
        membership = self._paid(self.alice)
        credit_note = self.env["account.move"].with_user(self.accountant).create({
            "move_type": "out_refund", "partner_id": self.alice.id,
            "invoice_line_ids": [Command.create({
                "product_id": self.type_person.product_id.id, "quantity": 1.0, "price_unit": 30.0})],
        })
        credit_note.action_post()
        self._commit_like()
        self._assert_no_cash_receipt(membership)

    def test_receipt_refused_after_a_credit_note_without_product(self):
        """Un avoir saisi à part, sur une ligne sans article (un libellé et un
        compte de revenu) : règle prudente, tout avoir client qui suit la
        facture bloque le reçu."""
        membership = self._paid(self.alice)
        credit_note = self.env["account.move"].with_user(self.accountant).create({
            "move_type": "out_refund", "partner_id": self.alice.id,
            "invoice_line_ids": [Command.create({"name": "Remboursement", "quantity": 1.0, "price_unit": 70.0})],
        })
        credit_note.action_post()
        self._commit_like()
        self._assert_no_cash_receipt(membership)

    def test_duplicate_refused_when_the_payment_changed(self):
        """🔴 Reçu de 70 $ délivré, chèque sans provision, puis nouveau règlement :
        par 40 $ et un écart radié, ou en argent mais à une autre date. Le
        duplicata ne certifie pas ce qui n'est plus vrai."""
        writeoff = self.env["account.account"].search([("account_type", "=", "expense")], limit=1)
        settlements = {
            "écart radié": lambda move: self.env["account.payment.register"].with_user(self.accountant).with_context(
                active_model="account.move", active_ids=move.ids).create({
                    "amount": 40.0, "payment_difference_handling": "reconcile",
                    "writeoff_account_id": writeoff.id, "writeoff_label": "Escompte accordé",
                })._create_payments(),
            "autre date": lambda move: self._pay(move, self._day(days=-1)),
        }
        for label, (partner, settle) in zip(settlements, ((self.alice, settlements["écart radié"]),
                                                          (self.bruno, settlements["autre date"]))):
            with self.subTest(settlement=label):
                membership = self._membership(partner)
                move = self._invoice(membership)
                payment = self._pay(move, self._day(days=-2))
                receipt = self._issue(membership)
                self._render_receipt(receipt)
                payment.with_user(self.accountant).action_draft()
                payment.with_user(self.accountant).action_cancel()
                self._commit_like()
                self.assertEqual(membership.payment_state, "to_pay")
                settle(move)
                self._commit_like()
                self.assertEqual(membership.payment_state, "paid")
                with self.assertRaises(UserError):
                    self._render_receipt(receipt)
                self.assertEqual(receipt.delivery_count, 1)

    def test_receipt_refused_on_a_partial_payment(self):
        membership = self._membership(self.alice)
        move = self._invoice(membership)
        self.env["account.payment.register"].with_user(self.accountant).with_context(
            active_model="account.move", active_ids=move.ids).create({"amount": 40.0})._create_payments()
        self._commit_like()
        self.assertEqual(move.payment_state, "partial")
        with self.assertRaises(UserError):
            membership.with_user(self.agent).action_issue_receipt()

    def test_receipt_on_a_full_payment_certifies_the_cash(self):
        receipt = self._issue(self._paid(self.alice))
        self.assertEqual((receipt.gift_amount, receipt.eligible_amount), (70.0, 70.0))

    def test_manual_payment_receipt_reserved_to_manager_or_accounting(self):
        """🔴 Hors facture, le montant et la date viennent de l'agent : le reçu
        se délivre par la personne responsable ou par la comptabilité."""
        membership = self._membership(self.alice)
        membership.with_user(self.agent).write({"payment_source": "cheque", "payment_date": self._day(days=-1)})
        membership.with_user(self.agent).action_mark_paid()
        with self.assertRaises(AccessError):
            membership.with_user(self.agent).action_issue_receipt()
        self.assertFalse(membership.receipt_ids)
        self.assertEqual(len(self._issue(membership, user=self.agent_billing)), 1)
        other = self._membership(self.bruno)
        other.write({"payment_source": "transfer", "payment_date": self._day(days=-1)})
        other.action_mark_paid()
        self.assertEqual(len(self._issue(other, user=self.manager)), 1)

    @staticmethod
    def _missing(caught):
        """La liste de ce qui manque, à la fin du message de refus."""
        return str(caught.exception).rsplit(" : ", 1)[-1]

    def test_agent_cannot_cancel_a_manual_payment_receipt(self):
        """Annuler un reçu, c'est le même cercle que le délivrer : hors facture,
        la personne responsable ou la comptabilité."""
        membership = self._membership(self.alice)
        membership.write({"payment_source": "cheque", "payment_date": self._day(days=-1)})
        membership.action_mark_paid()
        receipt = self._issue(membership, user=self.manager)
        wizard = self.env["bf.membership.receipt.cancel"].with_user(self.agent).create({
            "receipt_id": receipt.id, "reason": "Erreur", "replace": False})
        with self.assertRaises(AccessError):
            wizard.action_confirm()
        self._commit_like()
        self.assertEqual(receipt.state, "issued")
        self.env["bf.membership.receipt.cancel"].with_user(self.manager).create({
            "receipt_id": receipt.id, "reason": "Erreur", "replace": False}).action_confirm()
        self._commit_like()
        self.assertEqual(receipt.state, "cancelled")

    def test_cancel_circle_decided_on_the_real_invoice(self):
        """🔴 Le cercle de l'annulation se décide sur la facture réelle, pas sur
        la source notée : un agent passait la source à « Facture » le temps
        d'annuler le reçu d'un paiement noté à la main."""
        membership = self._membership(self.alice)
        membership.write({"payment_source": "cheque", "payment_date": self._day(days=-1)})
        membership.action_mark_paid()
        receipt = self._issue(membership, user=self.manager)
        agent = membership.with_user(self.agent)
        with self.assertRaises(UserError, msg="Réglée : la source ne change plus."):
            agent.write({"payment_source": "invoice"})
        agent.write({"payment_state": "to_pay"})
        with self.assertRaises(UserError, msg="Un reçu délivré : la source ne change plus."):
            agent.write({"payment_source": "invoice"})
        self.env.cr.execute("UPDATE bf_membership SET payment_source = 'invoice', payment_state = 'paid' "
                            "WHERE id = %s", [membership.id])
        self.env.invalidate_all()
        wizard = self.env["bf.membership.receipt.cancel"].with_user(self.agent).create({
            "receipt_id": receipt.id, "reason": "Erreur", "replace": False})
        with self.assertRaises(AccessError, msg="La source dit « Facture », mais aucune facture ne paie."):
            wizard.action_confirm()
        self._commit_like()
        self.assertEqual(receipt.state, "issued")

    def test_refused_without_canadian_address_or_place(self):
        """Art. 3501(1)(b) et (e) : l'adresse au Canada de l'organisme et le lieu
        de délivrance. Le refus nomme ce qui manque."""
        membership = self._paid(self.alice)
        original = {"street": self.company.street, "city": self.company.city, "zip": self.company.zip,
                    "country_id": self.company.country_id.id}
        cases = [
            ({"zip": False}, "le code postal"),
            ({"street": False}, "la rue"),
            ({"country_id": self.env.ref("base.us").id}, "le pays (Canada)"),
            ({"city": False}, "le lieu de délivrance"),
        ]
        for vals, missing in cases:
            with self.subTest(missing=missing):
                self.company.write(vals)
                with self.assertRaises(UserError) as caught:
                    membership.with_user(self.manager).action_issue_receipt()
                self.assertIn(missing, self._missing(caught))
                self.company.write(original)
        self.company.write({"city": False, "membership_receipt_place": "Ville-Essai"})
        with self.assertRaises(UserError) as caught:
            membership.with_user(self.manager).action_issue_receipt()
        self.assertEqual(self._missing(caught), "la ville.")
        self.assertFalse(self.env["bf.membership.receipt"].search([]))

    def test_html_preview_is_not_a_delivery(self):
        """Seul le PDF délivre : l'aperçu HTML ne consomme pas l'original."""
        receipt = self._issue(self._paid(self.alice))
        report = self.env["ir.actions.report"].with_user(self.agent)
        preview, _type = report._render_qweb_html("bf_membership_account.report_membership_receipt", receipt.ids)
        self._commit_like()
        self.assertEqual(receipt.delivery_count, 0)
        self.assertIn("APERÇU", preview.decode() if isinstance(preview, bytes) else preview)
        html = self._render_receipt(receipt)
        self.assertEqual(receipt.delivery_count, 1)
        self.assertNotIn("DUPLICATA", html)
        self.assertNotIn("APERÇU", html)


@tagged("post_install", "-at_install", "bf_membership_account")
class TestReceiptAccess(MembershipAccountCase):

    def test_plain_employee_neither_issues_nor_reads(self):
        membership = self._membership(self.alice)
        self._pay(self._invoice(membership))
        with self.assertRaises(AccessError):
            membership.with_user(self.employee).action_issue_receipt()
        membership.with_user(self.agent).action_issue_receipt()
        receipt = membership.receipt_ids
        self.assertEqual(len(receipt), 1, "L'agent délivre le reçu.")
        self.env.invalidate_all()
        with self.assertRaises(AccessError):
            receipt.with_user(self.employee).read(["name"])
        with self.assertRaises(AccessError):
            self._render_receipt(receipt, user=self.employee)

    def test_agent_cannot_create_a_receipt_by_hand(self):
        membership = self._membership(self.alice)
        self._pay(self._invoice(membership))
        with self.assertRaises(AccessError):
            self.env["bf.membership.receipt"].with_user(self.agent).create({
                "name": "FAUX-1", "membership_id": membership.id, "partner_id": self.alice.id,
                "company_id": self.company.id, "date_received": self.today, "date_issued": self.today})
