"""A company never keeps positions LONGER than the register declares for them.

🔴 Measured on a real base: the register declared 90 days while the setting was 1.
Nothing linked the two.

⚠️ The rule is `applied <= declared`, not `applied == declared`, and the difference is the
whole point. Section 23 of Quebec's Law 25 says personal information is destroyed once the
purpose it was collected for is accomplished: that is a CEILING, not a floor. Destroying
sooner is minimisation, and the register stays true because each entry names the term it
actually applied. Only keeping them longer than declared makes the declaration false.

An earlier form demanded equality. It refused a company that wanted 30 days, or 7, while
doing MORE than it had promised, and told an administrator to go and edit a retention rule
shared by five companies, which would have falsified it for the four others. Faced with a
refusal they cannot act on, the cheapest way out is to leave the whole feature off, which
is the opposite of what a conformity module should cause.

⚠️ And nothing declared means nothing is read. A missing, archived or zero-day policy used
to make this check pass in silence, which let the processing start with an empty register.
Same doctrine as the destruction entry two files away: better a feature postponed than an
attestation nobody can back.
"""
from odoo import _, api, models
from odoo.exceptions import ValidationError

PURPOSE = "bf_route_privacy.purpose_route_position"


class ResCompany(models.Model):
    _inherit = "res.company"

    @api.constrains("bf_route_position_mode", "bf_route_position_days")
    def _check_bf_route_position_days_declared(self):
        """Only companies that actually read positions, and only on write.

        ⚠️ Checked on write of these two fields alone. A company already out of step is not
        refused until someone touches one of them, on purpose: a constraint that raised on
        unrelated writes would lock an administrator out of the very form that would fix it.
        """
        reading = self.filtered(lambda company: company.bf_route_position_mode == "stops")
        if not reading:
            return
        # sudo on the lookup: a declared term is a declaration, not someone's personal
        # information, and the register's records are readable by the privacy officer only.
        # active_test off, or an archived policy reads as no policy and the refusal below
        # would name the wrong cause.
        purpose = self.env.ref(PURPOSE, raise_if_not_found=False)
        Policy = self.env["privacy.retention.policy"].sudo().with_context(active_test=False)
        policy = Policy.search([("purpose_id", "=", purpose.id)], limit=1) if purpose else None
        usable = bool(policy) and policy.retention_days and (
            policy.active if "active" in Policy._fields else True)
        if not usable:
            raise ValidationError(_(
                "Positions cannot be read: the privacy register declares no retention term "
                "for them. Its retention policy is missing, archived, or set to zero days.\n\n"
                "Declare the term first. Each erasure is sealed in the destruction register "
                "as an immutable entry citing that rule, and a processing nobody declared is "
                "one nobody can object to."))
        declared = policy.retention_days
        text = self.env["bf.route.notice.ack"]._retention_text
        for company in reading:
            applied = company.bf_route_position_days
            if applied <= declared:
                continue
            raise ValidationError(_(
                "The privacy register declares that positions read on a route are kept at "
                "most %(declared)s (purpose \"%(rule)s\", rule ROU-001), and %(company)s is "
                "set to keep them %(applied)s.\n\n"
                "Lower the setting, or declare the longer term in the register. Keeping "
                "them for LESS is fine and needs nothing: section 23 of Law 25 asks that "
                "information be destroyed once its purpose is accomplished, which is a "
                "ceiling, and each erasure names the term it actually applied.",
                declared=text(declared), applied=text(applied),
                rule=purpose.display_name or PURPOSE, company=company.display_name))
