# -*- coding: utf-8 -*-
import random
from odoo import api, fields, models


class InspirationalQuote(models.Model):
    _name = "daily.digest.quote"
    _description = "Inspirational Quote for Daily Digest"

    quote = fields.Text(string="Citation", required=True)
    author = fields.Char(string="Auteur")
    active = fields.Boolean(default=True)

    @api.model
    def get_random_quote(self):
        """Return a random active quote."""
        quotes = self.search([("active", "=", True)])
        if quotes:
            quote = random.choice(quotes)
            return {
                "quote": quote.quote,
                "author": quote.author or "Anonyme",
            }
        return {
            "quote": "Chaque jour est une nouvelle opportunité de faire mieux.",
            "author": "Anonyme",
        }
