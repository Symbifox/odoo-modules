# -*- coding: utf-8 -*-
import random
from odoo import api, fields, models


class InspirationalQuote(models.Model):
    _name = "daily.digest.quote"
    _description = "Inspirational Quote for Daily Digest"

    # Translatable: the digest is written in each recipient's language, and a
    # quote in the other language would be the only sentence that is not.
    quote = fields.Text(string="Quote", required=True, translate=True)
    author = fields.Char(string="Author", translate=True)
    active = fields.Boolean(default=True)

    @api.model
    def get_random_quote(self):
        """Return a random active quote, in the language of the context."""
        quotes = self.search([("active", "=", True)])
        if quotes:
            quote = random.choice(quotes)
            return {
                "quote": quote.quote,
                "author": quote.author or self.env._("Anonymous"),
            }
        return {
            "quote": self.env._("Every day is a new opportunity to do better."),
            "author": self.env._("Anonymous"),
        }
