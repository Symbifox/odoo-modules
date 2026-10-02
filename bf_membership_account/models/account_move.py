from odoo import _, fields, models


class AccountMove(models.Model):
    _inherit = "account.move"

    def _compute_payment_state(self):
        """La facture d'une cotisation fait passer l'adhésion d'« à payer » à
        « en règle », et inversement.

        🔴 `payment_state` est un champ calculé STOCKÉ : Odoo le recalcule au
        vidage du cache et l'écrit en base sans jamais passer par `write()`.
        Surcharger `write()` de la facture ne voit donc ni le paiement ni
        l'avoir, et `_invoice_paid_hook()` ne voit que le passage à « payée »,
        jamais le retour (paiement annulé, facture renversée, remise en
        brouillon) ni le paiement enregistré sans pièce comptable. Le seul point
        par où passent TOUS ces chemins est ce calcul : on le laisse faire, puis
        on écrit l'adhésion par son `write()`, qui porte la transition d'état
        du socle (`_sync_state_from_payment`).

        Un avoir posté ne change pas l'état de paiement d'une facture déjà
        payée (Odoo la garde « payée » et rembourse à part) : le calcul de
        l'avoir lui-même renvoie donc à la facture qu'il renverse.

        Les enregistrements en cours d'édition (`NewId`, dans le formulaire de
        la facture) sont écartés : un aperçu n'écrit pas l'adhésion.
        """
        super()._compute_payment_state()
        moves = self.filtered(lambda m: isinstance(m.id, int))
        invoices = moves.filtered(lambda m: m.move_type == "out_invoice")
        invoices |= moves.filtered(lambda m: m.move_type == "out_refund").reversed_entry_id
        if invoices:
            memberships = self.env["bf.membership"].sudo().search([("invoice_id", "in", invoices.ids)])
            memberships._sync_payment_from_invoice()

    def _bf_membership_fee_members(self):
        """{facture : membre} pour celles de ces pièces qui sont des factures de
        cotisation (liées à une adhésion), ou leurs avoirs."""
        memberships = self.env["bf.membership"].sudo().with_context(active_test=False).search(
            [("invoice_id", "in", (self | self.reversed_entry_id).ids)])
        by_invoice = {m.invoice_id.id: m.partner_id.id for m in memberships}
        return {move.id: by_invoice.get(move.id) or by_invoice.get(move.reversed_entry_id.id)
                for move in self if by_invoice.get(move.id) or by_invoice.get(move.reversed_entry_id.id)}

    def _bf_membership_credited(self):
        """Vrai si des avoirs postés renversent la facture en entier.

        Couvre les deux chemins de l'assistant d'avoir : l'annulation complète
        (Odoo lettre l'avoir avec la facture, qui devient « renversée ») et le
        remboursement d'une facture déjà payée (la facture reste « payée » aux
        yeux d'Odoo, l'avoir se rembourse à part).
        """
        self.ensure_one()
        if self.payment_state == "reversed":
            return True
        credit = sum(self.reversal_move_ids.filtered(
            lambda m: m.state == "posted" and m.move_type == "out_refund").mapped("amount_total"))
        return self.currency_id.compare_amounts(credit, self.amount_total) >= 0 and bool(credit)

    def _bf_membership_settled(self):
        """La cotisation est réglée par cette facture."""
        self.ensure_one()
        return (self.state == "posted" and self.payment_state in ("paid", "in_payment")
                and not self._bf_membership_credited())

    def _bf_membership_cash_received(self):
        """(encaissé, motif) : l'argent reçu pour cette facture, en devise de la
        société, ou un motif de refus.

        🔴 Le reçu fiscal certifie l'ENCAISSÉ, pas ce que le lettrage déclare
        réglé. Un paiement de 40 $ « marqué payé en entier » (l'écart radié en
        escompte accordé) lettre 70 $ avec la facture : additionner les
        lettrages certifierait 70 $. Ne compte donc que l'argent reçu sur un
        compte de liquidité par des pièces de paiement lettrées à la facture,
        borné par le lettrage :

        * une pièce de paiement entrant, sur un journal de banque ou de caisse,
          dont toutes les lignes sont la créance lettrée ou un compte de
          liquidité du paiement. Odoo compte parmi ces comptes celui des
          paiements en attente (« Outstanding Receipts ») : un paiement
          enregistré par la comptabilité y attend le rapprochement bancaire, et
          Odoo le tient pour reçu. Le module aussi ;
        * une ligne de relevé bancaire lettrée directement, sans compte
          d'attente ;
        * un paiement sans pièce comptable, entrant, pour cette seule facture.

        Une radiation, un escompte, une différence de change, un avoir lettré
        ou toute contrepartie qui n'est pas de l'argent : refus.
        """
        self.ensure_one()
        receivable = self.line_ids.filtered(lambda l: l.account_id.account_type == "asset_receivable")
        if receivable.matched_debit_ids:
            return 0.0, _("un mouvement inverse est lettré à la facture")
        cash = 0.0
        for partial in receivable.matched_credit_ids:
            if not partial.credit_move_id.move_id._bf_membership_cash_lines_only():
                return 0.0, _("le lettrage porte une radiation, un escompte, une différence de change ou un avoir")
            cash += partial.amount
        for payment in self.matched_payment_ids.filtered(
                lambda p: not p.move_id and p.state in ("in_process", "paid")):
            if payment.payment_type != "inbound" or payment.invoice_ids != self:
                return 0.0, _("un paiement sans pièce comptable couvre aussi d'autres factures")
            cash += abs(payment.amount_company_currency_signed)
        return self.company_id.currency_id.round(cash), None

    def _bf_membership_cash_lines_only(self):
        """Vrai si cette pièce n'est que de l'argent reçu contre la créance."""
        self.ensure_one()
        payment = self.origin_payment_id
        if payment:
            if payment.payment_type != "inbound" or payment.journal_id.type not in ("bank", "cash"):
                return False
            cash_accounts = payment._get_valid_liquidity_accounts()
        elif self.statement_line_id:
            cash_accounts = self.statement_line_id.journal_id.default_account_id
        else:
            return False
        return all(line.account_id in cash_accounts or line.account_id.account_type == "asset_receivable"
                   for line in self.line_ids)

    def _bf_membership_payment_date(self):
        """La date du paiement : la plus récente des pièces lettrées avec la
        facture (paiement, relevé bancaire), ou d'un paiement sans pièce
        comptable. À défaut, aujourd'hui.

        C'est la « date de réception » du reçu fiscal : la date où le chèque a
        été encaissé, pas celle où quelqu'un l'a saisi.
        """
        self.ensure_one()
        lines = self.line_ids.filtered(lambda l: l.account_id.account_type == "asset_receivable")
        dates = lines.matched_credit_ids.credit_move_id.mapped("date")
        dates += self.matched_payment_ids.filtered(lambda p: p.state in ("in_process", "paid")).mapped("date")
        dates = [d for d in dates if d]
        return max(dates) if dates else fields.Date.context_today(self)
