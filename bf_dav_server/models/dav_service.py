# Part of bf_dav_server. See LICENSE file for full copyright and licensing details.
"""Protocol core: path resolution, OPTIONS, PROPFIND, PROPPATCH, REPORT.

The service runs as the authenticated user (``self.env.user``): every read
and write goes through that user's access rights and Odoo's own masking of
other people's private events. The HTTP controller only authenticates and
translates; tests call ``_handle`` directly.
"""

import email.utils
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from urllib.parse import quote, unquote, urlparse

from odoo import api, models

from ..dav import xmlutil as X
from ..dav.xmlutil import C, CR, CS, D, ICAL

_logger = logging.getLogger(__name__)

ROOT = "/dav/"
SERVER_VERSION = "1"
SYNC_PREFIX = "https://symbifox.com/ns/sync/"
DAV_HEADER = "1, 3, access-control, calendar-access, addressbook, calendar-auto-schedule, extended-mkcol"
ALLOW = "OPTIONS, GET, HEAD, PUT, DELETE, PROPFIND, PROPPATCH, REPORT, MKCALENDAR, MOVE, POST"
READ_LEVELS = ("titles", "details")
WRITE_LEVELS = ("owner", "edit")


class DavError(Exception):
    def __init__(self, status, *conditions, message="", headers=None):
        super().__init__(message or str(status))
        self.status = status
        self.conditions = conditions
        self.headers = headers or {}


@dataclass
class Node:
    kind: str
    href: str
    owner: object = None       # res.users of the path
    cal: object = None         # bf.dav.calendar
    level: str = "owner"       # owner | edit | details | titles
    book: object = None        # bf.dav.addressbook
    name: str = ""             # resource name (last segment)


def http_date(dt):
    return email.utils.format_datetime(dt.replace(tzinfo=__import__("datetime").timezone.utc), usegmt=True)


class BfDavService(models.AbstractModel):
    _name = "bf.dav.service"
    _description = "CalDAV/CardDAV server"

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    @api.model
    def _handle(self, method, path, headers, body):
        """Return (status, headers, body bytes)."""
        method = method.upper()
        headers = {k.lower(): v for k, v in (headers or {}).items()}
        try:
            if method == "OPTIONS":
                return 200, self._base_headers(), b""
            handler = getattr(self, "_do_%s" % method.lower(), None)
            if handler is None:
                raise DavError(405)
            # A refused request leaves nothing behind: everything it wrote
            # before the refusal is rolled back with the savepoint.
            # Writes made by an app never feed the Nextcloud mirror: a pushed
            # PUT cannot be rolled back, and Nextcloud mails every guest on
            # any write.
            self = self.with_context(skip_nc_sync=True, skip_nc_contact_sync=True, bf_dav_request=True)
            handler = getattr(self, "_do_%s" % method.lower())
            with self.env.cr.savepoint():
                node = self._resolve(path)
                result = handler(node, headers, body or b"")
                self.env["bf.dav.change"]._flush_pending()
                return result
        except DavError as err:
            self.env.invalidate_all()
            body = X.error_body(*err.conditions) if err.conditions else b""
            hdrs = self._base_headers()
            if body:
                hdrs["Content-Type"] = "application/xml; charset=utf-8"
            if err.status == 405:
                hdrs["Allow"] = ALLOW
            hdrs.update(err.headers)
            return err.status, hdrs, body
        except X.XmlError as err:
            _logger.info("bf_dav_server: bad XML on %s %s: %s", method, path, err)
            return 400, self._base_headers(), b""

    def _base_headers(self):
        return {"DAV": DAV_HEADER, "Allow": ALLOW, "MS-Author-Via": "DAV"}

    # ------------------------------------------------------------------
    # Paths
    # ------------------------------------------------------------------

    def _href_root(self):
        return ROOT

    def _href_principal(self, user):
        return "%sprincipals/%d/" % (ROOT, user.id)

    def _href_cal_home(self, user):
        return "%scalendars/%d/" % (ROOT, user.id)

    def _href_ab_home(self, user):
        return "%saddressbooks/%d/" % (ROOT, user.id)

    def _href_cal(self, viewer, cal):
        slug = cal.slug if cal.owner_id == viewer else "s-%d" % cal.id
        return "%s%s/" % (self._href_cal_home(viewer), slug)

    def _href_book(self, viewer, book):
        return "%s%s/" % (self._href_ab_home(viewer), book.slug)

    @api.model
    def _split(self, path):
        path = urlparse(path).path if "://" in path else path
        path = unquote(path or "/")
        if not path.startswith(ROOT.rstrip("/")):
            raise DavError(404)
        rest = path[len(ROOT.rstrip("/")):]
        segs = [s for s in rest.split("/") if s]
        if any(s in (".", "..") for s in segs):
            raise DavError(404)
        return segs

    @api.model
    def _resolve(self, path):
        viewer = self.env.user
        segs = self._split(path)
        if not segs:
            return Node("root", ROOT, owner=viewer)
        top = segs[0]
        if top not in ("principals", "calendars", "addressbooks") or len(segs) < 2:
            raise DavError(404)
        if not segs[1].isdigit() or int(segs[1]) != viewer.id:
            # Other people's homes are not browsable.
            raise DavError(404 if top == "principals" else 403)
        if top == "principals":
            if len(segs) != 2:
                raise DavError(404)
            return Node("principal", self._href_principal(viewer), owner=viewer)
        if top == "calendars":
            home = self._href_cal_home(viewer)
            if len(segs) == 2:
                return Node("cal-home", home, owner=viewer)
            slug = segs[2]
            if slug in ("inbox", "outbox"):
                if len(segs) != 3:
                    raise DavError(404)
                return Node(slug, "%s%s/" % (home, slug), owner=viewer)
            cal, level = self._calendar_by_slug(viewer, slug)
            if len(segs) == 3:
                if not cal:
                    return Node("cal-missing", "%s%s/" % (home, slug), owner=viewer, name=slug)
                return Node("calendar", self._href_cal(viewer, cal), owner=viewer, cal=cal, level=level)
            if not cal or len(segs) != 4:
                raise DavError(404 if not cal else 404)
            return Node("cal-object", self._href_cal(viewer, cal) + quote(segs[3]), owner=viewer,
                        cal=cal, level=level, name=segs[3])
        # addressbooks
        home = self._href_ab_home(viewer)
        if len(segs) == 2:
            return Node("ab-home", home, owner=viewer)
        book = self._book_by_slug(viewer, segs[2])
        if not book:
            raise DavError(404)
        level = "owner" if book.kind == "personal" else "edit"
        if len(segs) == 3:
            return Node("addressbook", self._href_book(viewer, book), owner=viewer, book=book, level=level)
        if len(segs) != 4:
            raise DavError(404)
        return Node("card", self._href_book(viewer, book) + quote(segs[3]), owner=viewer, book=book,
                    level=level, name=segs[3])

    def _calendar_by_slug(self, viewer, slug):
        Cal = self.env["bf.dav.calendar"].sudo()
        if slug.startswith("s-") and slug[2:].isdigit():
            cal = Cal.browse(int(slug[2:])).exists()
            level = self._shared_level(viewer, cal) if cal and cal.active else None
            return (cal, level) if level else (Cal, None)
        cal = Cal.search([("owner_id", "=", viewer.id), ("slug", "=", slug), ("active", "=", True)], limit=1)
        return cal, "owner"

    def _shared_level(self, viewer, cal):
        """Level granted to ``viewer`` on someone else's calendar (bf_calendar_share)."""
        if "bf.calendar.share" not in self.env or cal.owner_id == viewer:
            return None
        share = self.env["bf.calendar.share"].sudo().search([
            ("owner_id", "=", cal.owner_id.id), ("grantee_id", "=", viewer.id), ("active", "=", True)], limit=1)
        level = share.level if share else None
        return {"titles": "titles", "details": "details", "edit": "edit"}.get(level)

    def _visible_calendars(self, viewer):
        Cal = self.env["bf.dav.calendar"]
        Cal._bf_dav_default_for(viewer)
        own = Cal.sudo().search([("owner_id", "=", viewer.id), ("active", "=", True)])
        shared = Cal.sudo()
        if "bf.calendar.share" in self.env:
            shares = self.env["bf.calendar.share"].sudo().search(
                [("grantee_id", "=", viewer.id), ("active", "=", True), ("level", "in", ("titles", "details", "edit"))])
            if shares:
                shared = Cal.sudo().search([("owner_id", "in", shares.owner_id.ids), ("active", "=", True)])
        return [(cal, "owner") for cal in own] + [(cal, self._shared_level(viewer, cal)) for cal in shared]

    def _book_by_slug(self, viewer, slug):
        books = self.env["bf.dav.addressbook"]._bf_dav_books_for(viewer)
        return books.filtered(lambda b: b.slug == slug and b.active)[:1]

    # ------------------------------------------------------------------
    # Tokens, tags
    # ------------------------------------------------------------------

    def _collection_key(self, node):
        if node.kind == "calendar":
            return node.cal._bf_dav_key()
        if node.kind == "addressbook":
            return node.book._bf_dav_key()
        return None

    def _sync_token(self, node):
        return "%s%d" % (SYNC_PREFIX, self.env["bf.dav.change"]._token(self._collection_key(node)))

    def _parse_token(self, text):
        text = (text or "").strip()
        if not text:
            return None
        if not text.startswith(SYNC_PREFIX) or not text[len(SYNC_PREFIX):].isdigit():
            raise DavError(403, D("valid-sync-token"))
        token = int(text[len(SYNC_PREFIX):])
        if token < self.env["bf.dav.change"]._floor():
            raise DavError(403, D("valid-sync-token"))
        return token

    @staticmethod
    def _etag(*parts):
        raw = json.dumps([SERVER_VERSION] + [str(p) for p in parts], separators=(",", ":"))
        return '"%s"' % hashlib.sha1(raw.encode("utf-8")).hexdigest()

    # ------------------------------------------------------------------
    # Members (implemented by caldav / carddav)
    # ------------------------------------------------------------------

    def _members(self, node, keys=None, hrefs=None, time_range=None):
        """List of dicts: key, href (name), etag, modified. Implemented by subclasses."""
        if node.kind == "calendar":
            return self._cal_members(node, keys=keys, hrefs=hrefs, time_range=time_range)
        if node.kind == "addressbook":
            return self._book_members(node, keys=keys, hrefs=hrefs)
        return []

    def _member_props(self, node, member, tags, data_tag=None, data_filter=None):
        """(found, missing) for one member resource."""
        found, missing = [], []
        for tag in tags:
            if tag == D("getetag"):
                found.append((tag, member["etag"]))
            elif tag == D("getcontenttype"):
                found.append((tag, "text/calendar; charset=utf-8; component=vevent"
                              if node.kind == "calendar" else "text/vcard; charset=utf-8"))
            elif tag == D("resourcetype"):
                found.append((tag, None))
            elif tag == D("getlastmodified") and member.get("modified"):
                found.append((tag, http_date(member["modified"])))
            elif tag in (C("calendar-data"), CR("address-data")):
                body = self._member_body(node, member)
                found.append((tag, body.decode("utf-8") if isinstance(body, bytes) else body))
            elif tag == D("getcontentlength"):
                found.append((tag, str(len(self._member_body(node, member)))))
            else:
                missing.append(tag)
        return found, missing

    def _ensure_bodies(self, node, members, tags):
        need = {C("calendar-data"), CR("address-data"), D("getcontentlength")}
        if not need & set(tags):
            return
        if node.kind == "calendar":
            self._cal_bodies(node, members)
        elif node.kind == "addressbook":
            self._book_bodies(node, members)

    def _member_body(self, node, member):
        if "body" not in member:
            member["body"] = (self._cal_body(node, member) if node.kind == "calendar"
                              else self._book_body(node, member))
        return member["body"]

    # ------------------------------------------------------------------
    # Collection-level properties
    # ------------------------------------------------------------------

    def _privileges(self, level):
        names = ["read", "read-current-user-privilege-set"]
        if level in WRITE_LEVELS:
            names += ["write", "write-content", "write-properties", "bind", "unbind"]
        privs = []
        for n in names:
            p = X.el(D("privilege"))
            p.append(X.el(D(n)))
            privs.append(p)
        p = X.el(D("privilege"))
        p.append(X.el(C("read-free-busy")))
        privs.append(p)
        return privs

    def _reports(self, *tags):
        out = []
        for tag in tags:
            sr = X.el(D("supported-report"))
            rep = X.el(D("report"))
            rep.append(X.el(tag))
            sr.append(rep)
            out.append(sr)
        return out

    def _node_props(self, node, tags, allprop=False):
        """(found, missing) for a non-member node."""
        viewer = self.env.user
        principal = self._href_principal(viewer)
        values = {
            D("current-user-principal"): [X.href_el(principal)],
            D("principal-URL"): [X.href_el(principal)],
            D("owner"): [X.href_el(principal)],
            D("principal-collection-set"): [X.href_el(ROOT + "principals/")],
        }
        rt = [X.el(D("collection"))]
        if node.kind == "root":
            values[D("displayname")] = "Symbifox"
        elif node.kind == "principal":
            rt = [X.el(D("principal"))]
            values.update({
                D("displayname"): viewer.name,
                C("calendar-home-set"): [X.href_el(self._href_cal_home(viewer))],
                CR("addressbook-home-set"): [X.href_el(self._href_ab_home(viewer))],
                C("calendar-user-address-set"): [X.href_el("mailto:%s" % e) for e in self._viewer_emails()]
                + [X.href_el(principal)],
                C("schedule-inbox-URL"): [X.href_el(self._href_cal_home(viewer) + "inbox/")],
                C("schedule-outbox-URL"): [X.href_el(self._href_cal_home(viewer) + "outbox/")],
                C("calendar-user-type"): "INDIVIDUAL",
                CS("email-address-set"): [X.el(CS("email-address"), e) for e in self._viewer_emails()],
                D("group-membership"): None,
                D("group-member-set"): None,
                D("supported-report-set"): self._reports(D("principal-property-search")),
            })
        elif node.kind in ("cal-home", "ab-home"):
            values[D("displayname")] = viewer.name
            values[D("current-user-privilege-set")] = self._privileges("owner")
        elif node.kind in ("inbox", "outbox"):
            rt.append(X.el(C("schedule-%s" % node.kind)))
            values[D("displayname")] = node.kind.capitalize()
            values[D("current-user-privilege-set")] = self._privileges("owner") + [
                self._priv(C("schedule-deliver")), self._priv(C("schedule-send"))]
        elif node.kind == "calendar":
            cal = node.cal
            rt.append(X.el(C("calendar")))
            name = cal.name if cal.owner_id == viewer else "%s (%s)" % (cal.name, cal.owner_id.name)
            values.update({
                D("displayname"): name,
                C("calendar-description"): cal.description or "",
                ICAL("calendar-color"): cal.color or "#29ABE2",
                ICAL("calendar-order"): str(cal.sequence),
                CS("getctag"): self._sync_token(node),
                D("sync-token"): self._sync_token(node),
                C("supported-calendar-component-set"): [X.el(C("comp"), name="VEVENT")],
                C("supported-calendar-data"): [X.el(C("calendar-data"), **{"content-type": "text/calendar",
                                                                          "version": "2.0"})],
                C("max-resource-size"): str(1024 * 1024),
                C("schedule-calendar-transp"): [X.el(C("opaque"))],
                D("current-user-privilege-set"): self._privileges(node.level),
                D("supported-report-set"): self._reports(C("calendar-multiget"), C("calendar-query"),
                                                         D("sync-collection")),
                D("owner"): [X.href_el(self._href_principal(cal.owner_id))]
                if cal.owner_id != viewer else [X.href_el(principal)],
            })
            for tag, value in cal._bf_dav_dead_props().items():
                values.setdefault(tag, value)
        elif node.kind == "addressbook":
            book = node.book
            rt.append(X.el(CR("addressbook")))
            values.update({
                D("displayname"): book.name,
                CS("getctag"): self._sync_token(node),
                D("sync-token"): self._sync_token(node),
                CR("supported-address-data"): [
                    X.el(CR("address-data-type"), **{"content-type": "text/vcard", "version": "3.0"}),
                    X.el(CR("address-data-type"), **{"content-type": "text/vcard", "version": "4.0"})],
                CR("max-resource-size"): str(2 * 1024 * 1024),
                D("current-user-privilege-set"): self._privileges(node.level),
                D("supported-report-set"): self._reports(CR("addressbook-multiget"), CR("addressbook-query"),
                                                         D("sync-collection")),
            })
        values[D("resourcetype")] = rt
        if allprop:
            tags = [t for t in values if t not in (D("group-member-set"),)]
        found, missing = [], []
        for tag in tags:
            if tag in values:
                value = values[tag]
                if isinstance(value, str) and value.startswith("<"):
                    # Dead property stored as XML text.
                    try:
                        frag = X.parse(("<x>%s</x>" % value).encode("utf-8"))
                        found.append((tag, list(frag)))
                        continue
                    except X.XmlError:
                        pass
                found.append((tag, value))
            else:
                missing.append(tag)
        return found, missing

    def _priv(self, tag):
        p = X.el(D("privilege"))
        p.append(X.el(tag))
        return p

    def _viewer_emails(self):
        viewer = self.env.user
        emails = []
        for e in (viewer.partner_id.email, viewer.login):
            e = (e or "").strip().lower()
            if e and "@" in e and e not in emails:
                emails.append(e)
        return emails

    def _children(self, node):
        viewer = self.env.user
        if node.kind == "root":
            return [Node("principal", self._href_principal(viewer), owner=viewer)]
        if node.kind == "cal-home":
            kids = [Node("calendar", self._href_cal(viewer, cal), owner=viewer, cal=cal, level=level)
                    for cal, level in self._visible_calendars(viewer)]
            kids += [Node(k, "%s%s/" % (node.href, k), owner=viewer) for k in ("inbox", "outbox")]
            return kids
        if node.kind == "ab-home":
            return [Node("addressbook", self._href_book(viewer, b), owner=viewer, book=b,
                         level="owner" if b.kind == "personal" else "edit")
                    for b in self.env["bf.dav.addressbook"]._bf_dav_books_for(viewer) if b.active]
        return []

    # ------------------------------------------------------------------
    # PROPFIND
    # ------------------------------------------------------------------

    def _do_propfind(self, node, headers, body):
        if node.kind == "cal-missing":
            raise DavError(404)
        mode, tags, _prop = X.requested_props(X.parse(body))
        depth = (headers.get("depth") or "1").strip().lower()
        if depth not in ("0", "1"):
            depth = "1"
        ms = X.Multistatus()
        default_member_tags = [D("getetag"), D("getcontenttype"), D("resourcetype"), D("getlastmodified")]
        if node.kind in ("cal-object", "card"):
            member = self._member_for_node(node)
            if not member:
                raise DavError(404)
            found, missing = self._member_props(
                self._member_node(node), member, tags if mode == "prop" else default_member_tags)
            self._add(ms, node.href, found, missing, mode)
            return self._multistatus(ms)
        found, missing = self._node_props(node, tags, allprop=mode != "prop")
        self._add(ms, node.href, found, missing, mode)
        if depth == "1":
            if node.kind in ("calendar", "addressbook"):
                members = self._members(node)
                self._ensure_bodies(node, members, tags if mode == "prop" else [])
                for member in members:
                    href = node.href + quote(member["href"])
                    f, m = self._member_props(node, member, tags if mode == "prop" else default_member_tags)
                    self._add(ms, href, f, m, mode)
            else:
                for child in self._children(node):
                    f, m = self._node_props(child, tags, allprop=mode != "prop")
                    self._add(ms, child.href, f, m, mode)
        return self._multistatus(ms)

    def _add(self, ms, href, found, missing, mode):
        if mode == "propname":
            ms.add_props(href, [(t, None) for t, _v in found])
        else:
            ms.add_props(href, found, missing)

    def _multistatus(self, ms):
        hdrs = self._base_headers()
        hdrs["Content-Type"] = "application/xml; charset=utf-8"
        return 207, hdrs, ms.to_bytes()

    def _member_for_node(self, node):
        found = self._members(self._member_node(node), hrefs=[node.name])
        return found[0] if found else None

    # ------------------------------------------------------------------
    # PROPPATCH, MKCALENDAR
    # ------------------------------------------------------------------

    def _do_proppatch(self, node, headers, body):
        root = X.parse(body)
        if root is None or root.tag != D("propertyupdate"):
            raise DavError(400)
        if node.kind != "calendar" or node.cal.owner_id != self.env.user:
            if node.kind in ("addressbook", "cal-home", "ab-home", "principal"):
                return self._proppatch_ignore(node, root)
            raise DavError(403)
        cal = node.cal.with_user(self.env.user).sudo()
        dead = cal._bf_dav_dead_props()
        vals, answered = {}, []
        for action in root:
            if action.tag not in (D("set"), D("remove")):
                continue
            for prop in action.iter(D("prop")):
                for p in prop:
                    if not isinstance(p.tag, str):
                        continue
                    answered.append(p.tag)
                    remove = action.tag == D("remove")
                    text = (p.text or "").strip()
                    if p.tag == D("displayname") and not remove and text:
                        vals["name"] = text[:200]
                    elif p.tag == ICAL("calendar-color"):
                        vals["color"] = False if remove else text[:9]
                    elif p.tag == ICAL("calendar-order") and text.lstrip("-").isdigit():
                        vals["sequence"] = int(text)
                    elif p.tag == C("calendar-description"):
                        vals["description"] = False if remove else text
                    elif remove:
                        dead.pop(p.tag, None)
                    else:
                        from lxml import etree
                        dead[p.tag] = (p.text or "") + "".join(
                            etree.tostring(c, encoding="unicode") for c in p)
        vals["dead_props"] = json.dumps(dead)
        cal.write(vals)
        ms = X.Multistatus()
        ms.add_props(node.href, [(t, None) for t in answered])
        return self._multistatus(ms)

    def _proppatch_ignore(self, node, root):
        """Clients set cosmetic properties on homes and address books: accept, keep nothing."""
        answered = [p.tag for prop in root.iter(D("prop")) for p in prop if isinstance(p.tag, str)]
        ms = X.Multistatus()
        if node.kind == "addressbook" and node.book.kind == "personal":
            for p in root.iter(D("displayname")):
                if (p.text or "").strip():
                    node.book.sudo().write({"name": p.text.strip()[:200]})
        ms.add_props(node.href, [(t, None) for t in answered])
        return self._multistatus(ms)

    def _do_mkcalendar(self, node, headers, body):
        if node.kind != "cal-missing":
            raise DavError(405)
        slug = node.name
        from .dav_calendar import SLUG_RE
        if not SLUG_RE.match(slug) or slug.startswith("s-"):
            raise DavError(403)
        name = slug
        root = X.parse(body)
        if root is not None:
            for p in root.iter(D("displayname")):
                if (p.text or "").strip():
                    name = p.text.strip()[:200]
        color = False
        if root is not None:
            for p in root.iter(ICAL("calendar-color")):
                color = (p.text or "").strip()[:9] or False
        Cal = self.env["bf.dav.calendar"].sudo().with_context(active_test=False)
        old = Cal.search([("owner_id", "=", self.env.user.id), ("slug", "=", slug)], limit=1)
        if old:
            old.write({"active": True, "name": name, "color": color or old.color})
        else:
            Cal.create({"name": name, "slug": slug, "owner_id": self.env.user.id, "color": color or "#29ABE2"})
        return 201, self._base_headers(), b""

    def _do_mkcol(self, node, headers, body):
        raise DavError(403)

    # ------------------------------------------------------------------
    # REPORT
    # ------------------------------------------------------------------

    def _do_report(self, node, headers, body):
        root = X.parse(body)
        if root is None:
            raise DavError(400)
        tag = root.tag
        if tag == D("sync-collection"):
            return self._report_sync(node, root)
        if tag in (C("calendar-multiget"), CR("addressbook-multiget")):
            return self._report_multiget(node, root)
        if tag == C("calendar-query"):
            return self._report_calendar_query(node, root)
        if tag == CR("addressbook-query"):
            return self._report_query_all(node, root)
        if tag == D("expand-property"):
            return self._report_expand(node, root)
        if tag == D("principal-property-search"):
            return self._report_principal_search(node, root)
        raise DavError(403, D("supported-report"))

    def _report_tags(self, root):
        mode, tags, _prop = X.requested_props(root)
        if mode != "prop":
            tags = [D("getetag")]
        return tags

    def _member_node(self, node):
        if node.kind == "cal-object":
            return Node("calendar", node.href.rsplit("/", 1)[0] + "/", owner=node.owner, cal=node.cal,
                        level=node.level)
        if node.kind == "card":
            return Node("addressbook", node.href.rsplit("/", 1)[0] + "/", owner=node.owner, book=node.book,
                        level=node.level)
        return node

    def _report_multiget(self, node, root):
        node = self._member_node(node)
        if node.kind not in ("calendar", "addressbook"):
            raise DavError(403)
        tags = self._report_tags(root)
        names = []
        for h in root.iter(D("href")):
            href = unquote((h.text or "").strip())
            href = urlparse(href).path if "://" in href else href
            if not href.startswith(unquote(node.href)):
                names.append((h.text, None))
                continue
            names.append((h.text, href[len(unquote(node.href)):].strip("/")))
        members = {m["href"]: m for m in self._members(node, hrefs=[n for _h, n in names if n])}
        self._ensure_bodies(node, list(members.values()), tags)
        ms = X.Multistatus()
        for raw_href, name in names:
            member = members.get(name) if name else None
            if not member:
                ms.add_status(raw_href, 404)
                continue
            found, missing = self._member_props(node, member, tags)
            ms.add_props(node.href + quote(name), found, missing)
        return self._multistatus(ms)

    def _report_query_all(self, node, root):
        node = self._member_node(node)
        if node.kind not in ("calendar", "addressbook"):
            raise DavError(403)
        tags = self._report_tags(root)
        ms = X.Multistatus()
        members = self._members(node)
        self._ensure_bodies(node, members, tags)
        for member in members:
            found, missing = self._member_props(node, member, tags)
            ms.add_props(node.href + quote(member["href"]), found, missing)
        return self._multistatus(ms)

    def _report_sync(self, node, root):
        if node.kind not in ("calendar", "addressbook"):
            raise DavError(403)
        token_el = root.find(D("sync-token"))
        token = self._parse_token(token_el.text if token_el is not None else "")
        tags = self._report_tags(root)
        ms = X.Multistatus()
        new_token = self._sync_token(node)
        if token is None:
            members = self._members(node)
            self._ensure_bodies(node, members, tags)
            for member in members:
                found, missing = self._member_props(node, member, tags)
                ms.add_props(node.href + quote(member["href"]), found, missing)
        else:
            changes = self.env["bf.dav.change"]._changes_since(self._collection_key(node), token)
            keys = {k for k, _h in changes}
            present = {m["key"]: m for m in self._members(node, keys=list(keys))} if keys else {}
            self._ensure_bodies(node, list(present.values()), tags)
            # Two passes: every resource still here first, then a 404 for the
            # addresses no longer served. A rebuilt series keeps its address
            # under a new key: its old key must not report it gone.
            served = set()
            for member in present.values():
                if member["href"] in served:
                    continue
                served.add(member["href"])
                found, missing = self._member_props(node, member, tags)
                ms.add_props(node.href + quote(member["href"]), found, missing)
            for key, href in changes:
                if href and href not in served:
                    served.add(href)
                    ms.add_status(node.href + quote(href), 404)
        ms.sync_token = new_token
        return self._multistatus(ms)

    def _report_expand(self, node, root):
        """Minimal expand-property: answer the requested properties of the node itself."""
        tags = [p.get("name") and X.qn(p.get("namespace") or X.NS_DAV, p.get("name"))
                for p in root.iter(D("property"))]
        found, missing = self._node_props(node, [t for t in tags if t])
        ms = X.Multistatus()
        ms.add_props(node.href, found, missing)
        return self._multistatus(ms)

    def _report_principal_search(self, node, root):
        ms = X.Multistatus()
        principal = Node("principal", self._href_principal(self.env.user), owner=self.env.user)
        tags = self._report_tags(root)
        found, missing = self._node_props(principal, tags)
        ms.add_props(principal.href, found, missing)
        return self._multistatus(ms)

    # ------------------------------------------------------------------
    # GET / HEAD
    # ------------------------------------------------------------------

    def _do_get(self, node, headers, body, head=False):
        if node.kind not in ("cal-object", "card"):
            if node.kind in ("root", "principal", "cal-home", "ab-home", "calendar", "addressbook"):
                text = b"Symbifox CalDAV/CardDAV server. Point your calendar or contacts app here."
                return 200, dict(self._base_headers(), **{"Content-Type": "text/plain; charset=utf-8"}), \
                    (b"" if head else text)
            raise DavError(404)
        member = self._member_for_node(node)
        if not member:
            raise DavError(404)
        coll = self._member_node(node)
        data = self._member_body(coll, member)
        data = data.encode("utf-8") if isinstance(data, str) else data
        hdrs = self._base_headers()
        hdrs["Content-Type"] = ("text/calendar; charset=utf-8" if node.kind == "cal-object"
                                else "text/vcard; charset=utf-8")
        hdrs["ETag"] = member["etag"]
        if member.get("modified"):
            hdrs["Last-Modified"] = http_date(member["modified"])
        return 200, hdrs, (b"" if head else data)

    def _do_head(self, node, headers, body):
        return self._do_get(node, headers, body, head=True)

    # ------------------------------------------------------------------
    # PUT / DELETE / MOVE dispatch
    # ------------------------------------------------------------------

    def _check_preconditions(self, member, headers):
        if_match = headers.get("if-match")
        if_none = headers.get("if-none-match")
        if if_none and if_none.strip() == "*" and member:
            raise DavError(412)
        if if_match:
            tags = [t.strip() for t in if_match.split(",")]
            if not member or ("*" not in tags and member["etag"] not in tags
                              and member["etag"].strip('"') not in [t.strip('"') for t in tags]):
                raise DavError(412)

    def _do_put(self, node, headers, body):
        if node.kind == "cal-object":
            return self._put_calendar_object(node, headers, body)
        if node.kind == "card":
            return self._put_card(node, headers, body)
        raise DavError(405)

    def _do_delete(self, node, headers, body):
        if node.kind == "cal-object":
            return self._delete_calendar_object(node, headers)
        if node.kind == "card":
            return self._delete_card(node, headers)
        if node.kind == "calendar" and node.cal.owner_id == self.env.user and not node.cal.is_default:
            # Its events are not deleted: they go back to the default calendar,
            # where every device still sees them.
            default = self.env["bf.dav.calendar"]._bf_dav_default_for(self.env.user)
            self.env["calendar.event"].sudo().with_context(active_test=False).search(
                [("bf_dav_calendar_id", "=", node.cal.id)]).write({"bf_dav_calendar_id": default.id})
            node.cal.sudo().write({"active": False})
            return 204, self._base_headers(), b""
        raise DavError(403)

    def _do_move(self, node, headers, body):
        if node.kind != "cal-object":
            raise DavError(403)
        dest = headers.get("destination") or ""
        try:
            target = self._resolve(dest)
        except DavError:
            raise DavError(502)
        if target.kind != "cal-object" or target.level not in WRITE_LEVELS:
            raise DavError(403)
        return self._move_calendar_object(node, target, headers)

    def _do_post(self, node, headers, body):
        if node.kind == "outbox":
            return self._outbox_post(node, headers, body)
        raise DavError(405)

    def _lock_rows(self, table, ids):
        if ids:
            self.env.cr.execute("SELECT id FROM %s WHERE id = ANY(%%s) FOR UPDATE" % table, [list(ids)])
