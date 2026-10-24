import logging
from collections import namedtuple

from dateutil.relativedelta import relativedelta
from markupsafe import Markup

from odoo import SUPERUSER_ID, Command, _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import clean_context, float_compare, float_is_zero, float_round

from .fee import RL24, split

#: Set by the billing only, and what a contract is born with.
BILLING_DEFAULTS = {"billed_on": False, "registration_billed": 0.0, "sibling_rank": 0, "sibling_percent": 0.0}
BILLING_SYSTEM_FIELDS = set(BILLING_DEFAULTS)

_logger = logging.getLogger(__name__)

#: One part of the price, as it is billed: its label, its amount, child care or not.
Component = namedtuple("Component", "label amount rl24")


class SchoolContractAccessory(models.Model):
    _inherit = "bf.school.contract.accessory"

    fee_id = fields.Many2one(
        "bf.school.fee", "From the catalogue", ondelete="restrict",
        domain="[('year_id', '=', parent.year_id), ('category', 'in', ('accessory', 'option'))]")
    rl24 = fields.Selection(RL24, "Relevé 24", required=True, default="none")

    @api.onchange("fee_id")
    def _onchange_fee_id(self):
        if self.fee_id:
            self.update({"name": self.fee_id.name, "price": self.fee_id.amount, "rl24": self.fee_id.rl24})


class SchoolContract(models.Model):
    # 🔴 The refund after a termination is an activity: without the mixin, an activity on the
    # contract would be invisible here and break the activity board everywhere.
    _name = "bf.school.contract"
    _inherit = ["bf.school.contract", "mail.activity.mixin"]

    level_id = fields.Many2one("bf.school.level", "Level", ondelete="restrict")
    profile_id = fields.Many2one(
        "bf.school.profile", "Profile", ondelete="restrict", domain="[('school_id', '=', school_id)]")
    sibling_rank = fields.Integer(
        "Rank in the family", readonly=True, copy=False,
        help="Among the students enrolled this year who share a payer, by birth date: 1 is the "
             "eldest. Set when the contract is prepared from the catalogue.")
    sibling_percent = fields.Float("Sibling discount (%)", readonly=True, copy=False)
    tuition_rl24 = fields.Monetary(
        "Of which child care (Relevé 24)",
        help="The part of the educational services that is child care: all of it in pre-K and "
             "kindergarten (Revenu Québec, letter 17-038083-001), none at compulsory school age.")
    share_ids = fields.One2many("bf.school.billing.share", "contract_id", "Payers", copy=False)
    school_move_ids = fields.One2many("account.move", "school_contract_id", "Invoices and credit notes")
    is_billed = fields.Boolean("Billed", compute="_compute_is_billed")
    amount_paid = fields.Monetary(
        compute="_compute_amount_paid", store=True, readonly=False,
        help="Follows the payments of the contract's invoices once it is billed; typed by hand "
             "for a contract billed outside Symbifox.")
    billed_on = fields.Datetime(readonly=True, copy=False)
    registration_billed = fields.Monetary(
        "Registration fee invoiced by the re-enrolment", readonly=True, copy=False,
        help="Part of the price, invoiced before the contract: not billed again.")

    @api.constrains("tuition", "tuition_rl24")
    def _check_tuition_rl24(self):
        for contract in self:
            if contract.tuition_rl24 < 0 or float_compare(
                    contract.tuition_rl24, contract.tuition, precision_rounding=contract.currency_id.rounding) > 0:
                raise ValidationError(_("The child-care part is between nothing and the educational services."))

    def _compute_is_billed(self):
        for contract in self:
            contract.is_billed = bool(contract.share_ids._school_billed())

    @api.depends("school_move_ids.amount_residual", "school_move_ids.state",
                 "school_move_ids.reversal_move_ids.amount_residual", "school_move_ids.reversal_move_ids.state")
    def _compute_amount_paid(self):
        for contract in self:
            moves = contract.sudo().school_move_ids
            if moves:
                # Every invoice cancelled: nothing paid any more, not the last value. A credit note
                # made in the accounting (Reverse) is not the contract's, and what it paid back counts.
                posted = moves.filtered(lambda m: m.state == "posted")
                refunds = posted.reversal_move_ids.filtered(
                    lambda m: m.state == "posted" and m.move_type == "out_refund")
                contract.amount_paid = (posted | refunds)._school_paid_amount()
            else:
                # Not billed here: what the office typed stays.
                contract.amount_paid = contract.amount_paid

    @api.model_create_multi
    def create(self, vals_list):
        # 🔴 Refused when forged, then written explicitly: Odoo adds `default_billed_on` from the
        # context and the user's own ir.default AFTER this check, and a contract born "billed"
        # was never billed. In sudo too, where the user's own ir.default still
        # applies: only the code passes these values. The contract guards its own state.
        for vals in vals_list:
            if not self.env.su and any(vals.get(f) and vals[f] != v for f, v in BILLING_DEFAULTS.items()):
                raise UserError(_("The billing sets this, not a person."))
            for field, value in BILLING_DEFAULTS.items():
                vals.setdefault(field, value)
        return super().create(vals_list)

    def _school_neutral_env(self):
        """Where the billing creates its invoices, credit notes, payment terms and statements.

        🔴 Not `sudo()`: it keeps the user who clicked, and their own ir.default (any internal user
        sets them for themselves) went into what the code did not write: invoices posted at 0 $ by
        a 100 % discount, in USD for a CAD contract, with a forged payment note, and credit notes
        of a termination at 0 $. The superuser has none. The context is cleaned here too: Odoo
        only cleans it for a caller not yet in sudo, and a caller in sudo kept its default_*.
        """
        return self.env(user=SUPERUSER_ID, su=True, context=clean_context(self.env.context))

    def write(self, vals):
        if not self.env.su and BILLING_SYSTEM_FIELDS & set(vals):
            raise UserError(_("The billing sets this, not a person."))
        if not self.env.su and "amount_paid" in vals and self.sudo().school_move_ids:
            raise UserError(_("The amount paid of a billed contract follows its invoices."))
        if not self.env.su and {"tuition_rl24", "level_id", "profile_id"} & set(vals):
            self._school_check_editable()
        return super().write(vals)

    # --- Prepared from the catalogue -----------------------------------------------------

    @api.model
    def _school_family_rank(self, student, year):
        """The student's rank among the students of the year who share a payer with them."""
        payers = student.student_guardian_link_ids.filtered("is_payer").guardian_id
        if not payers:
            return 1
        kids = self.env["bf.school.guardian.link"].search(
            [("guardian_id", "in", payers.ids), ("is_payer", "=", True)]).student_id
        enrolled = self.env["bf.school.enrollment"].search([
            ("year_id", "=", year.id), ("state", "=", "active"), ("student_id", "in", kids.ids)]).student_id
        ordered = enrolled.sorted(lambda s: (not s.student_birthdate, s.student_birthdate or fields.Date.today(), s.id))
        return list(ordered).index(student) + 1 if student in ordered else 1

    @api.model
    def _school_price(self, year, level, profile, rank):
        """{tuition, tuition_rl24, accessory_ids, sibling_percent} from the year's catalogue."""
        fees = year.fee_ids.filtered(lambda f: f.active and f._applies_to(level, profile))
        percent = year.school_id._school_sibling_percent(rank)

        def net(fee):
            if fee.sibling_discount and percent:
                return float_round(fee.amount * (100.0 - percent) / 100.0, 2)
            return fee.amount

        educational = fees.filtered(lambda f: f.category == "educational")
        return {
            "tuition": sum(net(f) for f in educational),
            "tuition_rl24": sum(net(f) for f in educational if f.rl24 == "childcare"),
            "accessory_ids": [Command.create({
                "name": f.name, "price": net(f), "fee_id": f.id, "rl24": f.rl24, "sequence": f.sequence,
            }) for f in fees.filtered(lambda f: f.category == "accessory")],
            "sibling_percent": percent if any(f.sibling_discount for f in fees) else 0.0,
        }

    @api.model
    def _school_campaign_fee(self, student, year, kind):
        """The fee of the student's application of this kind for this year, if any."""
        if "bf.school.admission" not in self.env:
            return 0.0
        apps = self.env["bf.school.admission"].sudo().search([
            ("student_id", "=", student.id), ("kind", "=", kind),
            ("school_id", "=", year.school_id.id), ("campaign_id.target_year", "=", year.name),
            ("state", "not in", ("withdrawn", "refused"))], limit=1)
        return apps.campaign_id.fee_amount if apps else 0.0

    @api.model
    def _school_prepare(self, enrollment):
        """A draft contract for this enrolment, priced from the catalogue, with its payers."""
        student, year = enrollment.student_id, enrollment.year_id
        rank = self._school_family_rank(student, year)
        price = self._school_price(year, enrollment.level_id, enrollment.profile_id, rank)
        family = {"sibling_rank": rank, "sibling_percent": price.pop("sibling_percent")}
        contract = self.create({
            "student_id": student.id, "school_id": year.school_id.id, "year_id": year.id,
            "date_start": year.date_start, "date_end": year.date_end,
            "level_id": enrollment.level_id.id, "profile_id": enrollment.profile_id.id,
            "installment_count": year.school_id.billing_installment_count,
            "admission_fee": self._school_campaign_fee(student, year, "reenrollment"),
            "eligibility_fee": self._school_campaign_fee(student, year, "admission"),
            **price,
        })
        contract.sudo().write(family)
        contract._school_default_shares()
        return contract

    def action_school_reprice(self):
        """Price a draft again from the catalogue (level or profile changed)."""
        for contract in self:
            if contract.state != "draft":
                raise UserError(_("Only a draft contract is priced again."))
            if not contract.level_id:
                raise UserError(_("Choose the student's level first."))
            rank = self._school_family_rank(contract.student_id, contract.year_id)
            price = self._school_price(contract.year_id, contract.level_id, contract.profile_id, rank)
            # Optional services the family chose stay; the catalogue's accessory lines are redone.
            redo = contract.accessory_ids.filtered(lambda a: not a.fee_id or a.fee_id.category == "accessory")
            price["accessory_ids"] = [Command.unlink(a.id) for a in redo] + price["accessory_ids"]
            family = {"sibling_rank": rank, "sibling_percent": price.pop("sibling_percent")}
            contract.write(price)
            contract.sudo().write(family)
        return True

    # --- Payers ------------------------------------------------------------------------

    def _school_default_shares(self):
        """The adults who pay for the student, in equal shares, when the contract has no payer yet."""
        for contract in self.filtered(lambda c: not c.share_ids):
            payers = contract.student_id.student_guardian_link_ids.filtered("is_payer").guardian_id
            if not payers:
                continue
            parts = split(100.0, [1.0] * len(payers), digits=4)
            contract.share_ids = [Command.create({"partner_id": p.id, "kind": "family", "percent": pct})
                                  for p, pct in zip(payers, parts)]

    def action_school_default_shares(self):
        self._school_default_shares()
        return True

    def _school_check_shares_editable(self):
        if self.env.su:
            return
        for contract in self:
            if contract.share_ids._school_billed():
                raise UserError(_(
                    "The payers of %s are already invoiced: a change goes through a credit note "
                    "in the accounting.", contract.name))

    def _school_registration_invoice(self):
        """The registration fee already invoiced by the re-enrolment, for this year."""
        self.ensure_one()
        if "bf.school.admission" not in self.env:
            return self.env["account.move"]
        apps = self.env["bf.school.admission"].sudo().search([
            ("student_id", "=", self.student_id.id), ("kind", "=", "reenrollment"),
            ("school_id", "=", self.school_id.id), ("campaign_id.target_year", "=", self.year_id.name),
            # A withdrawn re-enrolment reverses its invoice: it stays "posted", but owes nothing.
            ("invoice_id.state", "=", "posted"), ("invoice_id.payment_state", "!=", "reversed")], limit=1)
        return apps.invoice_id

    def _school_registration_billed(self):
        """Frozen when the contract is billed: what was deducted then stays deducted."""
        self.ensure_one()
        if self.billed_on:
            return self.registration_billed
        invoice = self._school_registration_invoice()
        return min(self.admission_fee, invoice.amount_total) if invoice else 0.0

    def _school_check_registration(self):
        """🔴 A contract prepared before the re-enrolment has no registration fee in its price:
        billing it would invoice the fee twice over the price, and raise the penalty cap."""
        invoice = self._school_registration_invoice()
        if invoice and float_compare(invoice.amount_total, self.admission_fee,
                                     precision_rounding=self.currency_id.rounding) > 0:
            raise UserError(_(
                "The re-enrolment of %(student)s invoiced a registration fee of %(fee)s that is not "
                "in the price of %(contract)s: terminate this contract and make a new one with it.",
                student=self.student_id.name, fee=invoice.currency_id.format(invoice.amount_total),
                contract=self.name))

    def _school_billable(self):
        """What the billing invoices: the price, less the registration fee already invoiced."""
        self.ensure_one()
        return float_round(self.total_price - self._school_registration_billed(), 2)

    def _school_share_amounts(self, raise_if_invalid=True):
        """{share id: amount}: the bursaries first, the families split the rest."""
        self.ensure_one()

        def invalid(message):
            if raise_if_invalid:
                raise UserError(message)
            return {}

        billable = self._school_billable()
        bursaries = self.share_ids.filtered(lambda s: s.kind == "bursary")
        families = self.share_ids.filtered(lambda s: s.kind == "family")
        fixed = sum(bursaries.mapped("amount"))
        rounding = self.currency_id.rounding
        if not self.share_ids:
            return invalid(_("%s has no payer: tick « Pays » on a guardian, or add a payer.", self.name))
        if float_compare(fixed, billable, precision_rounding=rounding) > 0:
            return invalid(_("The bursaries of %(contract)s (%(fixed)s) exceed what is billed (%(billable)s).",
                             contract=self.name, fixed=fixed, billable=billable))
        if families and float_compare(sum(families.mapped("percent")), 100.0, precision_digits=4):
            return invalid(_("The family shares of %s must add up to 100 %%.", self.name))
        if not families and float_compare(fixed, billable, precision_rounding=rounding):
            return invalid(_("Nobody pays what the bursaries of %s leave.", self.name))
        amounts = dict(zip(bursaries.ids, bursaries.mapped("amount")))
        amounts.update(zip(families.ids, split(billable - fixed, families.mapped("percent"))))
        return amounts

    # --- Invoices ----------------------------------------------------------------------

    def _school_components(self, env):
        """The billed parts of the price, labelled in the language of `env`."""
        self.ensure_one()
        parts = []
        admission = float_round(self.admission_fee - self._school_registration_billed(), 2)
        if admission > 0:
            parts.append(Component(env._("Admission or registration fee"), admission, False))
        care = self.tuition_rl24
        if care:
            parts.append(Component(env._("Educational services (child care)"), care, True))
        if self.tuition - care:
            parts.append(Component(env._("Educational services"), float_round(self.tuition - care, 2), False))
        parts += [Component(a.name, a.price, a.rl24 == "childcare")
                  for a in self.accessory_ids if a.price]
        return parts

    def _school_payment_term(self, invoice_date):
        """The contract's due dates as a payment term: the admission fee now, then the
        instalments, the first when the services begin (Act s. 70)."""
        self.ensure_one()
        schedule = []
        admission = float_round(self.admission_fee - self._school_registration_billed(), 2)
        if admission > 0:
            schedule.append((invoice_date, admission))
        schedule += [(due, amount) for due, amount in self._installments() if amount]
        total = sum(amount for _due, amount in schedule)
        digits = self.env["decimal.precision"].precision_get("Payment Terms")
        lines, given = [], 0.0
        for i, (due, amount) in enumerate(schedule):
            last = i == len(schedule) - 1
            percent = float_round(100.0 - given, digits) if last else float_round(amount * 100.0 / total, digits)
            given += percent
            lines.append(Command.create({"value": "percent", "value_amount": percent,
                                         "delay_type": "days_after", "nb_days": max(0, (due - invoice_date).days)}))
        # Archived: a term made for one contract does not belong in everyone's list.
        return self._school_neutral_env()["account.payment.term"].create({
            "name": _("Contract %s", self.name), "company_id": self.company_id.id,
            "active": False, "line_ids": lines})

    def action_school_bill(self):
        """Invoice the payers of a signed contract."""
        self.check_access("write")
        for contract in self:
            if contract.state != "signed":
                raise UserError(_("Only a signed contract is billed."))
            if contract.share_ids._school_billed() or contract.billed_on:
                raise UserError(_("%s is already billed.", contract.name))
            contract._school_default_shares()
            contract._school_share_amounts()
            contract._school_check_registration()
        self.sudo()._school_create_invoices()
        return True

    def _school_create_invoices(self):
        invoice_date = fields.Date.context_today(self)
        moves = self.env["account.move"]
        for contract in self:
            amounts = contract._school_share_amounts()
            term = contract._school_payment_term(invoice_date)
            registration = contract._school_registration_invoice()
            contract.write({"billed_on": fields.Datetime.now(),
                            "registration_billed": contract._school_registration_billed()})
            if registration and not registration.school_contract_id:
                registration.school_contract_id = contract
            for share in contract.share_ids:
                amount = amounts.get(share.id, 0.0)
                if share._school_billed() or float_is_zero(amount, precision_rounding=contract.currency_id.rounding):
                    continue
                # The invoice speaks its payer's language, whoever clicked. 🔴 And nobody is
                # subscribed to it but its payer: the creator (a parent signing on the portal,
                # the office) received the other parent's invoice by email and saw it on their
                # portal.
                lang = share.partner_id.lang or contract.company_id.partner_id.lang or "fr_CA"
                env = contract.with_env(contract._school_neutral_env()).with_context(
                    lang=lang, mail_create_nosubscribe=True).env
                prefix = "%s, %s" % (contract.year_id.name, contract.student_id.name)
                if share.kind == "bursary":
                    # A bursary fund or an employer needs its share, not the family's options.
                    lines = [Command.create({
                        "name": "%s : %s" % (prefix, env._("bursary or third-party share of contract %s",
                                                           contract.name)),
                        "quantity": 1, "price_unit": amount, "tax_ids": [Command.clear()],
                        "school_rl24": False})]
                else:
                    components = contract._school_components(env)
                    lines = [Command.create({
                        "name": "%s : %s" % (prefix, c.label), "quantity": 1, "price_unit": part,
                        # Educational services and child care are exempt in Québec.
                        "tax_ids": [Command.clear()], "school_rl24": c.rl24,
                    }) for c, part in zip(components, split(amount, [c.amount for c in components])) if part]
                move = env["account.move"].with_company(contract.company_id).create({
                    "move_type": "out_invoice", "partner_id": share.partner_id.id,
                    # 🔴 Not the person who clicked (lesson of the admission fee and the meals).
                    "invoice_user_id": False,
                    "invoice_date": invoice_date, "invoice_payment_term_id": term.id,
                    "invoice_origin": contract.name, "ref": contract.name,
                    "school_contract_id": contract.id, "invoice_line_ids": lines,
                })
                move.action_post()
                share.invoice_id = move
                moves |= move
            contract.message_post(body=_(
                "Billed: %s.", ", ".join("%s (%s)" % (s.partner_id.name, s.invoice_id.name)
                                         for s in contract.share_ids if s.invoice_id)),
                message_type="notification", subtype_xmlid="mail.mt_note")
        if moves:
            self.env.ref("bf_school_billing.ir_cron_school_billing_send").sudo()._trigger()
        return moves

    def _sign_on_signed(self, request):
        """Signed by every client: the office checks the payers, then bills.

        🔴 Billing on the last signature emailed a year of invoices to every adult ticked
        « Pays », in equal shares, before anyone checked a custody agreement, and from the
        signing parent's session.
        """
        result = super()._sign_on_signed(request)
        for contract in self.sudo().filtered(lambda c: c.state == "signed" and not c.billed_on):
            contract._school_default_shares()
            office = contract.create_uid
            if office and office.active and not office.share:
                contract.activity_schedule(
                    "mail.mail_activity_data_todo", user_id=office.id,
                    summary=_("Check the payers, then bill"),
                    note=_("%s is signed: check who pays and in what share, then click « Bill the payers ».",
                           contract.name))
        return result

    # --- Termination ---------------------------------------------------------------------

    def action_terminate(self):
        for contract in self:
            if contract.sudo().share_ids.invoice_id.filtered(lambda m: m.state == "draft"):
                raise UserError(_("An invoice of %s is a draft: confirm or cancel it in the accounting "
                                  "before recording the termination.", contract.name))
        result = super().action_terminate()
        for contract in self.sudo().filtered(lambda c: c.state == "terminated"):
            contract._school_credit_termination()
        return result

    def _school_services_rendered(self):
        """The price of the services provided before a termination (Act s. 73), the admission
        fee and the penalty apart; the whole services when the contract was not terminated."""
        self.ensure_one()
        services = self.total_price - self.admission_fee
        if self.state != "terminated" or not self.termination_date:
            return services
        if self.termination_date < self.date_start:
            return 0.0
        elapsed = relativedelta(self.termination_date, self.date_start)
        months = self._months()
        done = min(months, elapsed.years * 12 + elapsed.months + (1 if elapsed.days > 0 else 0))
        return float_round(services * done / months, 2)

    def _school_credit_termination(self):
        """Credit the payers for what the school may not keep (Act s. 72-74), on each invoice
        in proportion, and leave the refund owed to each payer as an activity, by its deadline."""
        self.ensure_one()
        invoices = self.share_ids.filtered(lambda s: s.invoice_id.state == "posted").invoice_id
        if not invoices:
            return self.env["account.move"]
        billed = self._school_registration_billed() + sum(invoices.mapped("amount_total"))
        reduction = float_round(billed - self.termination_due, 2)
        refunds = self.env["account.move"]
        rounding = self.currency_id.rounding
        due_back, unpaid_settled = {}, {}
        for invoice, part in zip(invoices, split(max(reduction, 0.0), invoices.mapped("amount_total"))):
            # 🔴 The credit note brings the invoice and its earlier credit notes to what the payer
            # still owes, or is owed: the money they paid against their part of what the school
            # keeps. Crediting the whole reduction refunded a write-off as if paid (+936 $), and a
            # correction credit note already paid back (+500 $).
            kept = invoice.amount_total - min(part, invoice.amount_total)
            earlier = self.env["account.move"].search([
                ("reversed_entry_id", "=", invoice.id), ("move_type", "=", "out_refund"),
                ("state", "=", "posted")])
            money = (invoice | earlier)._school_paid_amount()
            balance = invoice.amount_residual - sum(earlier.mapped("amount_residual"))
            settled = invoice.amount_total - sum(earlier.mapped("amount_total")) - money - balance
            back = max(0.0, money - kept)
            owed = max(0.0, kept - money - max(settled, 0.0))
            part = float_round(balance - (owed - back), precision_rounding=rounding)
            due_back[invoice.partner_id] = due_back.get(invoice.partner_id, 0.0) + back
            if float_compare(settled, 0.0, precision_rounding=rounding) > 0:
                unpaid_settled[invoice] = settled
            if part <= 0:
                continue
            lines = invoice.invoice_line_ids.filtered(lambda l: l.display_type == "product")
            env = self.with_env(self._school_neutral_env()).with_context(
                lang=invoice.partner_id.lang or "fr_CA", mail_create_nosubscribe=True).env
            refund = env["account.move"].with_company(self.company_id).create({
                "move_type": "out_refund", "partner_id": invoice.partner_id.id,
                "invoice_user_id": False, "reversed_entry_id": invoice.id,
                "invoice_date": self.termination_date or fields.Date.context_today(self),
                "invoice_origin": self.name, "ref": env._("Termination of %s", self.name),
                "school_contract_id": self.id,
                "invoice_line_ids": [Command.create({
                    "name": line.name, "quantity": 1, "price_unit": amount,
                    "tax_ids": [Command.clear()], "school_rl24": line.school_rl24,
                }) for line, amount in zip(lines, split(part, lines.mapped("price_subtotal"))) if amount],
            })
            # Posted with `reversed_entry_id`, the credit note is reconciled with its invoice by
            # Odoo itself (`_reconcile_reversed_moves`): a second reconcile here did nothing.
            refund.action_post()
            refunds |= refund
        # 🔴 The contract said 1 090 $ to refund (net of the contract) while the credit notes owed
        # 1 870 $ to one parent and the other still owed 780 $: per payer, what they are owed.
        owed = {p: float_round(a, precision_rounding=rounding) for p, a in due_back.items()
                if float_compare(a, 0.0, precision_rounding=rounding) > 0}
        self.write({"termination_refund": sum(owed.values())})
        if owed or unpaid_settled:
            lines = [_("To refund: %s.", ", ".join(
                "%s %s" % (p.name, self.currency_id.format(a)) for p, a in owed.items()))] if owed else []
            # A settlement that is not money (a write-off, an entry the billing cannot read as a
            # payment) is told, not guessed: the office checks it before refunding.
            lines += [_("%(invoice)s was settled for %(amount)s without a payment the billing "
                        "recognises (write-off or entry): check it before refunding.",
                        invoice=i.name, amount=self.currency_id.format(a)) for i, a in unpaid_settled.items()]
            self.activity_schedule(
                "mail.mail_activity_data_todo", date_deadline=self.refund_deadline,
                summary=_("Refund by %s (Act s. 74)", self.refund_deadline),
                note=Markup("<br/>").join(lines), user_id=self.env.uid)
        if refunds:
            self.env.ref("bf_school_billing.ir_cron_school_billing_send").sudo()._trigger()
        return refunds

    @api.model
    def _cron_send_invoices(self, limit=50):
        """Send the new contract invoices and credit notes to their payers, with their PDF, once.

        🔴 A failure is tried again at the next run; a payer without an email address is told to
        the office once, instead of an invoice marked "sent" that nobody received.
        """
        since = fields.Datetime.subtract(fields.Datetime.now(), days=30)
        moves = self.env["account.move"].sudo().search([
            ("school_contract_id", "!=", False), ("move_type", "in", ("out_invoice", "out_refund")),
            ("state", "=", "posted"), ("school_billing_sent", "=", False),
            ("create_date", ">=", since)], limit=limit)
        for move in moves:
            contract = move.school_contract_id
            if move.move_type == "out_invoice" and contract._school_registration_invoice() == move:
                move.school_billing_sent = True   # the re-enrolment sent it
                continue
            if move.message_ids.filtered(lambda m: m.message_type == "comment" and m.attachment_ids
                                         and move.partner_id in m.partner_ids):
                # 🔴 Emailed to the payer from the accounting (after a failed run): it went twice.
                # Not `is_move_sent`: Odoo ticks it on a PDF downloaded without any email.
                move.school_billing_sent = True
                continue
            if not move.partner_id.email:
                move.school_billing_sent = True
                contract.message_post(body=_(
                    "%(payer)s has no email address: %(move)s was not sent. Give it to them another way.",
                    payer=move.partner_id.name, move=move.name),
                    message_type="notification", subtype_xmlid="mail.mt_note")
                office = contract.create_uid
                if office and office.active and not office.share:
                    contract.activity_schedule(
                        "mail.mail_activity_data_todo", user_id=office.id,
                        summary=_("Invoice not sent: no email address"),
                        note=_("%(move)s for %(payer)s.", move=move.name, payer=move.partner_id.name))
                continue
            try:
                with self.env.cr.savepoint():
                    self.env["account.move.send"].sudo()._generate_and_send_invoices(
                        move, sending_methods=["email"])
                    move.school_billing_sent = True
            except Exception:  # noqa: BLE001
                _logger.warning("Contract invoice %s not sent, tried again at the next run", move.name,
                                exc_info=True)
