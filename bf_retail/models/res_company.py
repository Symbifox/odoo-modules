# -*- coding: utf-8 -*-
import math

from odoo import api, fields, models

# The endings a store prices on. Each one is reached by going UP: a price that must hold a
# margin is never rounded down to a nicer number, that would eat the margin it just computed.
ROUNDINGS = [
    ("none", "No rounding"),
    ("cent", "To the cent"),
    ("0.05", "Up to the next 0.05"),
    ("0.09", "Up to the next ending in 0.09"),
    ("0.49", "Up to the next ending in 0.49 or 0.99"),
    ("0.95", "Up to the next ending in 0.95"),
    ("0.99", "Up to the next ending in 0.99"),
]


class ResCompany(models.Model):
    _inherit = "res.company"

    bf_retail_margin_target = fields.Float(
        "Target margin (%)", digits=(16, 1), default=30.0,
        help="Default target for a retail category that sets none: the share of the price, "
             "excluding tax, left after the cost.")
    bf_retail_price_rounding = fields.Selection(
        ROUNDINGS, "Suggested price rounding", default="0.99", required=True,
        help="Applied to the price as it is shown on the shelf, which is the price written on "
             "the product: tax included where the taxes are, tax excluded elsewhere.")
    bf_retail_cost_max_age = fields.Integer(
        "A cost gets old after (days)", default=90,
        help="Past that age the cost is flagged on the product: the margin computed from it is "
             "no longer trustworthy.")
    bf_retail_price_gap_min = fields.Float(
        "Smallest gap worth a change", digits=(16, 2), default=0.02,
        help="A price change batch leaves out a product whose suggested price differs from its "
             "price by less than this: a reprinted label costs more than the cent it recovers.")

    # A retail cost, or a price batch that was APPLIED. 🔴 Any batch used to count, cancelled
    # or still a draft: a company that had opened one and given up blocked every shared product
    # for ever. Still no checkbox of its own.
    BF_RETAIL_STORE_PROOFS = (("bf.retail.cost", []),
                              ("bf.retail.price.change", [("state", "=", "applied")]))

    @api.model
    def _bf_retail_stores(self):
        """The companies that follow the retail: those with a cost series or a batch applied.

        Not one more flag to tick: a company that has a cost row or a price it applied IS
        following the retail, and a flag would say it a second time, out of step with the
        rows. Read under sudo on purpose -- the record rules of both models stop at the
        reader's own companies, and the question here is precisely what the OTHER stores do.
        """
        stores = self.browse()
        for model, domain in self.BF_RETAIL_STORE_PROOFS:
            for (company,) in self.env[model].sudo()._read_group(
                    domain, groupby=["company_id"]):
                stores |= company
        return stores


def arrondir(price, rule):
    """A price taken UP to the ending the store prices on.

    Never down. The price it is given already holds the target margin, and rounding it down
    would spend that margin on a nicer number. A price already on the ending stays where it
    is, so applying the rule twice changes nothing.

    The crossing of a dollar is deliberate and visible: 0.995 with the 0.99 ending becomes
    1.99, because the next price ending in 0.99 above 0.995 is 1.99. A store that does not
    want that jump prices that category to the cent, or to 0.05.
    """
    if price <= 0:
        return 0.0
    if not rule or rule == "none":
        return price
    if rule == "cent":
        return round(math.ceil(price * 100.0 - 1e-9) / 100.0, 2)
    if rule == "0.05":
        return round(math.ceil(price / 0.05 - 1e-9) * 0.05, 2)
    # An ending: go up to the next price that carries it. `pas` is the distance between two
    # prices with that ending, `fin` what the price ends on.
    pas, fin = {"0.09": (0.10, 0.01), "0.49": (0.50, 0.01),
                "0.95": (1.0, 0.05), "0.99": (1.0, 0.01)}[rule]
    return round(math.ceil((price + fin) / pas - 1e-9) * pas - fin, 2)


def _bf_retail_dans_la_langue_du_commerce(record, company):
    """`record` read back in the language of the STORE, for a body the chatter KEEPS.

    Why this exists. A chatter body is STORED, and read later by someone else than whoever
    wrote it: the manager opens in the morning the bill a cron validated in the night. Both
    translation functions of Odoo 18 read the language off the CONTEXT and nowhere else --
    `env.lang` is nothing but `context.get("lang")` (`odoo/api.py`, l. 765), `env._` falls
    back to `en_US` when it is empty (l. 788), and the historic `_` falls back to the language
    of whichever user happens to run the transaction. Outside a web request the context
    carries no language at all, so a cron posting a validated bill froze an ENGLISH sentence
    in the thread of a store that reads French, for ever.

    A `UserError` does NOT come through here, on purpose: it is transient, it is shown to the
    person who just made the gesture, and it stays in the language of its READER.

    The language of a store is the one of its contact -- `res.company` carries no `lang` field
    of its own. Empty: nothing changes, the current context stands, which is the behaviour of
    today. No longer an active language: nothing changes either, because `env.lang` RAISES on
    a code that is not active, and a cron writing the cost series must not die over the
    language of a thread body.
    """
    lang = company.partner_id.lang
    if not lang or lang == record.env.context.get("lang"):
        return record
    if not record.env["res.lang"]._get_code(lang):
        return record
    return record.with_context(lang=lang)
