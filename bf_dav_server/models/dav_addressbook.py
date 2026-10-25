# Part of bf_dav_server. See LICENSE file for full copyright and licensing details.
import hashlib

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from .calendar_event import href_for_uid, new_uid
from .dav_calendar import SLUG_RE, slugify

# res.partner fields shown in the company address book. A change to any of them
# changes the card, so the sync token moves.
CARD_FIELDS = {"name", "email", "phone", "mobile", "function", "parent_id", "is_company",
               "street", "street2", "city", "zip", "state_id", "country_id", "website",
               "category_id", "image_128", "image_1920", "active", "company_name", "bf_dav_uid", "bf_dav_href",
               "type"}


class BfDavAddressbook(models.Model):
    """A CardDAV address book.

    ``company``: the Odoo contacts of the company, shared by internal users,
    read and written as res.partner (sensitive fields left out).
    ``personal``: a user's private book, cards kept as written by the client,
    outside the CRM.
    """

    _name = "bf.dav.addressbook"
    _description = "CardDAV address book"
    _order = "kind, owner_id, id"

    name = fields.Char(required=True)
    kind = fields.Selection([("company", "Company contacts"), ("personal", "Personal")],
                            required=True, default="personal")
    owner_id = fields.Many2one("res.users", ondelete="cascade", index=True,
                               domain=[("share", "=", False)])
    slug = fields.Char("Short address", required=True, copy=False)
    exclude_tag_ids = fields.Many2many(
        "res.partner.category", string="Excluded tags",
        help="Company book: contacts carrying one of these tags are not served.")
    active = fields.Boolean(default=True)
    card_ids = fields.One2many("bf.dav.card", "book_id")
    card_count = fields.Integer(compute="_compute_card_count")

    _sql_constraints = [
        ("owner_slug_uniq", "unique (owner_id, slug)", "Each address book address must be unique."),
    ]

    @api.constrains("slug", "kind", "owner_id")
    def _check_slug(self):
        for book in self:
            if not SLUG_RE.match(book.slug or ""):
                raise ValidationError(_("Invalid address book address: %s", book.slug))
            if book.kind == "personal" and not book.owner_id:
                raise ValidationError(_("A personal address book needs an owner."))
            if book.kind == "company" and self.search_count([("kind", "=", "company"), ("id", "!=", book.id)]):
                raise ValidationError(_("There is only one company address book."))

    def _compute_card_count(self):
        for book in self:
            if book.kind == "company":
                book.card_count = self.env["res.partner"].search_count(book._company_domain())
            else:
                book.card_count = len(book.card_ids)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("slug"):
                vals["slug"] = slugify(vals.get("name") or "contacts")
        books = super().create(vals_list)
        for book in books:
            self.env["bf.dav.change"]._record([book._bf_dav_key()], "", "")
        return books

    def write(self, vals):
        res = super().write(vals)
        for book in self:
            self.env["bf.dav.change"]._record([book._bf_dav_key()], "", "")
        return res

    def _bf_dav_key(self):
        self.ensure_one()
        return "ab:company" if self.kind == "company" else "ab:%d" % self.id

    def _company_domain(self):
        self.ensure_one()
        domain = [("active", "=", True), ("type", "in", ("contact", False)),
                  ("bf_dav_uid", "!=", False)]
        if self.exclude_tag_ids:
            domain.append(("category_id", "not in", self.exclude_tag_ids.ids))
        # The partners of portal and public accounts are not contacts.
        domain.append(("user_ids", "not any", [("share", "=", True)]))
        return domain

    @api.model
    def _bf_dav_books_for(self, user):
        books = self.sudo().search([("kind", "=", "company")], limit=1)
        personal = self.sudo().search([("kind", "=", "personal"), ("owner_id", "=", user.id)])
        if not personal:
            personal = self.sudo().create({
                "name": self.with_context(lang=user.lang or "en_US").env._("Personal contacts"),
                "kind": "personal", "owner_id": user.id, "slug": "personal",
            })
        return books | personal


class BfDavCard(models.Model):
    """A card of a personal address book, kept as the client wrote it."""

    _name = "bf.dav.card"
    _description = "CardDAV card"
    _order = "id"

    book_id = fields.Many2one("bf.dav.addressbook", required=True, ondelete="cascade", index=True)
    uid = fields.Char(required=True, index=True)
    href = fields.Char(required=True, index=True)
    name = fields.Char(help="Formatted name, for display in Odoo only.")
    vcard = fields.Text(required=True)
    etag = fields.Char(compute="_compute_etag", store=True)

    _sql_constraints = [
        ("book_href_uniq", "unique (book_id, href)", "A card address is unique in its book."),
        ("book_uid_uniq", "unique (book_id, uid)", "A card UID is unique in its book."),
    ]

    @api.depends("vcard")
    def _compute_etag(self):
        for card in self:
            card.etag = '"%s"' % hashlib.sha1((card.vcard or "").encode("utf-8")).hexdigest()

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            vals.setdefault("uid", new_uid())
            vals.setdefault("href", href_for_uid(vals["uid"], ".vcf"))
        cards = super().create(vals_list)
        cards._bf_dav_journal()
        return cards

    def write(self, vals):
        before = [(c.book_id._bf_dav_key(), "k%d" % c.id, c.href) for c in self]
        res = super().write(vals)
        self.env["bf.dav.change"]._record_many(before)
        self._bf_dav_journal()
        return res

    def unlink(self):
        rows = [(c.book_id._bf_dav_key(), "k%d" % c.id, c.href) for c in self]
        res = super().unlink()
        self.env["bf.dav.change"]._record_many(rows)
        return res

    def _bf_dav_journal(self):
        self.env["bf.dav.change"]._record_many(
            [(c.book_id._bf_dav_key(), "k%d" % c.id, c.href) for c in self])


class ResPartner(models.Model):
    _inherit = "res.partner"

    bf_dav_uid = fields.Char("vCard UID", copy=False, index=True)
    bf_dav_href = fields.Char("CardDAV resource name", copy=False)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("bf_dav_uid"):
                vals["bf_dav_uid"] = new_uid()
            if not vals.get("bf_dav_href"):
                vals["bf_dav_href"] = href_for_uid(vals["bf_dav_uid"], ".vcf")
        partners = super().create(vals_list)
        partners._bf_dav_journal()
        return partners

    def write(self, vals):
        touched = bool(CARD_FIELDS & set(vals))
        before = self._bf_dav_rows() if touched else []
        res = super().write(vals)
        if touched:
            self.env["bf.dav.change"]._record_many(before)
            self._bf_dav_journal()
            # The company name is printed on its people's cards.
            if {"name", "is_company"} & set(vals):
                self.child_ids._bf_dav_journal()
        return res

    def unlink(self):
        rows = self._bf_dav_rows()
        res = super().unlink()
        self.env["bf.dav.change"]._record_many(rows)
        return res

    def _bf_dav_rows(self):
        return [("ab:company", "p%d" % p.id, p.bf_dav_href) for p in self if p.bf_dav_href]

    def _bf_dav_journal(self):
        if self.env.context.get("bf_dav_no_journal"):
            return
        self.env["bf.dav.change"]._record_many(self._bf_dav_rows())
