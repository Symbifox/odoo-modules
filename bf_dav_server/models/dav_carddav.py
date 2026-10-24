# Part of bf_dav_server. See LICENSE file for full copyright and licensing details.
"""CardDAV: the company book (res.partner) and personal books (raw cards)."""

import base64
import logging

from odoo import api, models
from odoo.exceptions import AccessError, UserError, ValidationError

from ..dav import vcard as V
from ..dav.xmlutil import CR, D
from .dav_service import WRITE_LEVELS, DavError

_logger = logging.getLogger(__name__)


def _b64(data):
    if not data:
        return ""
    return data.decode() if isinstance(data, bytes) else data


class BfDavServiceCarddav(models.AbstractModel):
    _inherit = "bf.dav.service"

    # ------------------------------------------------------------------
    # Members
    # ------------------------------------------------------------------

    def _book_members(self, node, keys=None, hrefs=None):
        book = node.book
        if book.kind == "personal":
            domain = [("book_id", "=", book.id)]
            if hrefs is not None:
                domain.append(("href", "in", list(hrefs)))
            if keys is not None:
                domain.append(("id", "in", [int(k[1:]) for k in keys if k[:1] == "k" and k[1:].isdigit()]))
            cards = self.env["bf.dav.card"].sudo().search(domain)
            return [{"key": "k%d" % c.id, "href": c.href, "etag": c.etag, "modified": c.write_date}
                    for c in cards]
        domain = list(book._company_domain())
        if hrefs is not None:
            domain.append(("bf_dav_href", "in", list(hrefs)))
        if keys is not None:
            domain.append(("id", "in", [int(k[1:]) for k in keys if k[:1] == "p" and k[1:].isdigit()]))
        Partner = self.env["res.partner"].with_context(active_test=True)
        rows = Partner.search_read(domain, ["bf_dav_href", "write_date", "parent_id"])
        parents = {p["parent_id"][0] for p in rows if p["parent_id"]}
        parent_wd = {}
        if parents:
            self.env.cr.execute("SELECT id, write_date FROM res_partner WHERE id = ANY(%s)", [list(parents)])
            parent_wd = dict(self.env.cr.fetchall())
        keys_found = ["p%d" % r["id"] for r in rows]
        extra = {}
        if keys_found:
            self.env.cr.execute("SELECT resource_key, write_date FROM bf_dav_user_data "
                                "WHERE user_id = %s AND resource_key = ANY(%s)", [self.env.user.id, keys_found])
            extra = dict(self.env.cr.fetchall())
        out = []
        for r in rows:
            if not r["bf_dav_href"]:
                continue
            key = "p%d" % r["id"]
            pwd = parent_wd.get(r["parent_id"][0]) if r["parent_id"] else None
            out.append({"key": key, "href": r["bf_dav_href"],
                        "etag": self._etag("card", self.env.user.id, key, r["bf_dav_href"], r["write_date"], pwd,
                                           extra.get(key)),
                        "modified": r["write_date"]})
        return out

    def _book_bodies(self, node, members):
        todo = [m for m in members if "body" not in m]
        if not todo:
            return
        if node.book.kind == "personal":
            cards = self.env["bf.dav.card"].sudo().browse([int(m["key"][1:]) for m in todo])
            texts = {"k%d" % c.id: c.vcard for c in cards}
            for m in todo:
                m["body"] = texts.get(m["key"], "")
            return
        ids = [int(m["key"][1:]) for m in todo]
        partners = self.env["res.partner"].browse(ids).with_prefetch(ids)
        extras = {}
        from .dav_store import _loads
        for row in self.env["bf.dav.user.data"].sudo().search_read(
                [("user_id", "=", self.env.user.id), ("resource_key", "in", [m["key"] for m in todo])],
                ["resource_key", "valarms"]):
            extras[row["resource_key"]] = _loads(row["valarms"]).get("", [])
        by_id = {p.id: p for p in partners}
        for m in todo:
            p = by_id.get(int(m["key"][1:]))
            m["body"] = V.build_card(self._card_data(p), extras.get(m["key"], [])) if p else ""

    def _book_body(self, node, member):
        key = member["key"]
        if key[:1] == "k":
            return self.env["bf.dav.card"].sudo().browse(int(key[1:])).vcard
        partner = self.env["res.partner"].browse(int(key[1:]))
        extra = self.env["bf.dav.user.data"]._load(self.env.user, key)[0].get("", [])
        return V.build_card(self._card_data(partner), extra)

    def _card_data(self, p):
        return {
            "uid": p.bf_dav_uid,
            "name": p.name or p.display_name or "",
            "is_company": p.is_company,
            "org": (p.parent_id.name if p.parent_id else p.company_name) or "",
            "title": p.function or "",
            "email": (p.email or "").strip(),
            "phone": p.phone or "",
            "mobile": p.mobile or "",
            "adr": {"street": p.street or "", "street2": p.street2 or "", "city": p.city or "",
                    "state": p.state_id.name or "", "zip": p.zip or "", "country": p.country_id.name or ""},
            "url": p.website or "",
            "categories": p.category_id.mapped("name"),
            "photo": _b64(p.image_128) if p.image_128 else "",
            "rev": p.write_date,
        }

    # ------------------------------------------------------------------
    # PUT / DELETE
    # ------------------------------------------------------------------

    def _put_card(self, node, headers, body):
        if node.level not in WRITE_LEVELS:
            raise DavError(403, D("need-privileges"))
        member = self._member_for_node(node)
        self._check_preconditions(member, headers)
        try:
            card = V.parse_card(body)
        except V.VcardError as err:
            raise DavError(403, CR(err.condition)) from err
        book = node.book
        if book.kind == "personal":
            return self._put_personal_card(node, member, card, body)
        coll = self._member_node(node)
        try:
            if not member:
                clash = self.env["res.partner"].search_count(
                    list(book._company_domain()) + [("bf_dav_uid", "=", card.uid)])
                if clash:
                    raise DavError(403, CR("no-uid-conflict"))
                vals = self._partner_vals(card, None)
                vals.update(bf_dav_uid=card.uid, bf_dav_href=node.name, is_company=card.is_company)
                partner = self.env["res.partner"].create(vals)
                status = 201
            else:
                partner = self.env["res.partner"].browse(int(member["key"][1:]))
                if partner.bf_dav_uid and partner.bf_dav_uid != card.uid:
                    raise DavError(403, CR("no-uid-conflict"))
                vals = self._partner_vals(card, partner)
                if vals:
                    partner.write(vals)
                status = 204
        except AccessError as err:
            raise DavError(403, D("need-privileges")) from err
        except (UserError, ValidationError) as err:
            _logger.info("bf_dav_server: card refused: %s", err)
            raise DavError(403, CR("valid-address-data")) from err
        # What a phone keeps on a card beyond Odoo's fields (notes, birthday,
        # other numbers) is the user's own: a colleague never sees it.
        self.env["bf.dav.user.data"]._store(self.env.user, "p%d" % partner.id, {"": self._card_extra(card)}, {})
        self.env["bf.dav.change"]._record([book._bf_dav_key()], "p%d" % partner.id, partner.bf_dav_href)
        del coll
        return status, self._base_headers(), b""

    def _card_extra(self, card):
        extra = list(card.extra)
        extra += [raw for _v, _t, raw in card.emails[1:]]
        cell = next((i for i, (_v, t, _r) in enumerate(card.tels) if "CELL" in t), None)
        other = next((i for i, (_v, t, _r) in enumerate(card.tels) if "CELL" not in t), None)
        extra += [raw for i, (_v, _t, raw) in enumerate(card.tels) if i not in (cell, other)]
        return extra

    def _partner_vals(self, card, p):
        vals = {}

        def setv(field, value):
            current = p[field] if p is not None else None
            if p is None or (current or False) != (value or False):
                vals[field] = value or False

        setv("name", V.display_name(card))
        setv("email", card.emails[0][0] if card.emails else "")
        cell = next((v for v, t, _r in card.tels if "CELL" in t), "")
        other = next((v for v, t, _r in card.tels if "CELL" not in t), "")
        setv("mobile", cell)
        setv("phone", other)
        if card.title is not None or p is None:
            setv("function", card.title or "")
        if not (p.is_company if p is not None else card.is_company):
            org = (card.org or "").strip()
            parent = self.env["res.partner"]
            if org:
                parent = next((c for c in self.env["res.partner"].search(
                    [("is_company", "=", True), ("name", "ilike", org)], limit=20) if self._same(c.name, org)),
                    self.env["res.partner"])
            if p is None or (p.parent_id.name or p.company_name or "") != org:
                vals["parent_id"] = parent.id or False
                vals["company_name"] = False if parent else (org or False)
        if card.adr is not None:
            adr = card.adr
            setv("street", adr["street"])
            setv("street2", adr["street2"])
            setv("city", adr["city"])
            setv("zip", adr["zip"])
            country = self._country_for(adr["country"], p.country_id if p is not None else None)
            if p is None or p.country_id != country:
                vals["country_id"] = country.id or False
            state = self._state_for(adr["state"], country, p.state_id if p is not None else None)
            if p is None or p.state_id != state:
                vals["state_id"] = state.id or False
        elif p is not None and any((p.street, p.city, p.zip)):
            # The client removed the address.
            vals.update(street=False, street2=False, city=False, zip=False, state_id=False, country_id=False)
        if card.url is not None or p is None:
            setv("website", card.url or "")
        if card.categories is not None:
            Cat = self.env["res.partner.category"]
            ids = []
            for name in card.categories:
                cat = Cat.search([("name", "=", name)], limit=1) or next(
                    (c for c in Cat.search([("name", "ilike", name)]) if self._same(c.name, name)), None) \
                    or Cat.sudo().create({"name": name})
                ids.append(cat.id)
            if p is None or set(ids) != set(p.category_id.ids):
                vals["category_id"] = [(6, 0, ids)]
        if card.photo_present and card.photo:
            new = base64.b64encode(card.photo).decode()
            served = _b64(p.image_128) if p is not None and p.image_128 else ""
            if p is None or (new != served and not self._is_thumbnail(card.photo, p)):
                vals["image_1920"] = new
        return vals

    @staticmethod
    def _is_thumbnail(data, partner):
        """A phone re-encodes the 128 px picture it was given: that must not
        replace a larger one."""
        if not partner or not partner.image_1920:
            return False
        try:
            from PIL import Image
            import io
            with Image.open(io.BytesIO(data)) as img:
                return max(img.size) <= 256
        except Exception:  # noqa: BLE001 - unreadable: treat as a thumbnail, keep Odoo's
            return True

    @staticmethod
    def _same(a, b):
        return (a or "").strip().casefold() == (b or "").strip().casefold()

    def _country_for(self, value, current):
        """The country a card names. The name we served comes back as is:
        it must find the current country, never "the first country whose
        code starts with those letters" (code is 2 characters: Odoo cuts
        'Allemagne' to 'Al' and finds Albania)."""
        Country = self.env["res.country"]
        value = (value or "").strip()
        if not value:
            return Country
        if current and (self._same(current.name, value) or self._same(current.code, value)):
            return current
        # Only installed languages: a context in an inactive one raises.
        langs = {code for code, _name in self.env["res.lang"].get_installed()} | {"en_US"}
        for lang in sorted(langs):
            for c in Country.with_context(lang=lang).search([]):
                if self._same(c.name, value):
                    return c.with_context(lang=self.env.lang)
        if len(value) == 2:
            return Country.search([("code", "=", value.upper())], limit=1)
        return current or Country

    def _state_for(self, value, country, current):
        State = self.env["res.country.state"]
        value = (value or "").strip()
        if not value:
            return State
        if current and (self._same(current.name, value) or self._same(current.code, value)):
            return current
        domain = [("country_id", "=", country.id)] if country else []
        for st in State.search(domain):
            if self._same(st.name, value) or (len(value) <= 3 and self._same(st.code, value)):
                return st
        return current or State

    def _put_personal_card(self, node, member, card, body):
        text = body.decode("utf-8") if isinstance(body, bytes) else body
        Card = self.env["bf.dav.card"].sudo()
        if not member:
            if Card.search_count([("book_id", "=", node.book.id), ("uid", "=", card.uid)]):
                raise DavError(403, CR("no-uid-conflict"))
            rec = Card.create({"book_id": node.book.id, "uid": card.uid, "href": node.name,
                               "name": V.display_name(card), "vcard": text})
            return 201, dict(self._base_headers(), ETag=rec.etag), b""
        rec = Card.browse(int(member["key"][1:]))
        if rec.uid != card.uid:
            raise DavError(403, CR("no-uid-conflict"))
        rec.write({"vcard": text, "name": V.display_name(card)})
        # Stored as written: the ETag of the stored card is the body's.
        return 204, dict(self._base_headers(), ETag=rec.etag), b""

    def _delete_card(self, node, headers):
        if node.level not in WRITE_LEVELS:
            raise DavError(403, D("need-privileges"))
        member = self._member_for_node(node)
        if not member:
            raise DavError(404)
        self._check_preconditions(member, headers)
        if member["key"][:1] == "k":
            self.env["bf.dav.card"].sudo().browse(int(member["key"][1:])).unlink()
        else:
            partner = self.env["res.partner"].browse(int(member["key"][1:]))
            try:
                # A contact deleted on a phone is archived in Odoo, never destroyed.
                with self.env.cr.savepoint():
                    partner.write({"active": False})
            except (AccessError, UserError, ValidationError) as err:
                raise DavError(403, D("need-privileges")) from err
        self.env["bf.dav.change"]._record([node.book._bf_dav_key()], member["key"], member["href"])
        return 204, self._base_headers(), b""
