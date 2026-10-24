# Part of bf_dav_server. See LICENSE file for full copyright and licensing details.
"""WebDAV XML helpers: namespaces, safe parsing, multistatus building."""

from lxml import etree

NS_DAV = "DAV:"
NS_CALDAV = "urn:ietf:params:xml:ns:caldav"
NS_CARDDAV = "urn:ietf:params:xml:ns:carddav"
NS_CS = "http://calendarserver.org/ns/"
NS_ICAL = "http://apple.com/ns/ical/"
NS_BF = "https://symbifox.com/ns/dav/"

NSMAP = {
    "d": NS_DAV,
    "cal": NS_CALDAV,
    "card": NS_CARDDAV,
    "cs": NS_CS,
    "ical": NS_ICAL,
}

MAX_XML_BYTES = 2 * 1024 * 1024

_PARSER = etree.XMLParser(
    resolve_entities=False,
    no_network=True,
    huge_tree=False,
    remove_comments=True,
    remove_pis=True,
    load_dtd=False,
)


class XmlError(ValueError):
    """The request body is not acceptable XML."""


def qn(ns, name):
    """Clark notation tag ``{ns}name``."""
    return "{%s}%s" % (ns, name)


def D(name):
    return qn(NS_DAV, name)


def C(name):
    return qn(NS_CALDAV, name)


def CR(name):
    return qn(NS_CARDDAV, name)


def CS(name):
    return qn(NS_CS, name)


def ICAL(name):
    return qn(NS_ICAL, name)


def parse(body):
    """Parse a request body. Empty body returns None. DTDs and entities refused."""
    if not body or not body.strip():
        return None
    if len(body) > MAX_XML_BYTES:
        raise XmlError("body too large")
    if b"<!DOCTYPE" in body[:2048] or b"<!ENTITY" in body:
        raise XmlError("DTD not allowed")
    try:
        return etree.fromstring(body, parser=_PARSER)
    except etree.XMLSyntaxError as exc:
        raise XmlError(str(exc)) from exc


def el(tag, text=None, children=None, **attrs):
    node = etree.Element(tag, nsmap=None, **attrs)
    if text is not None:
        node.text = text
    for child in children or ():
        node.append(child)
    return node


def href_el(href):
    return el(D("href"), href)


def _status_line(code):
    from http import HTTPStatus

    try:
        phrase = HTTPStatus(code).phrase
    except ValueError:
        phrase = ""
    return "HTTP/1.1 %d %s" % (code, phrase)


def status_el(code):
    return el(D("status"), _status_line(code))


def prop_value(tag, value):
    """Build a <prop child>. ``value`` may be None (empty), str, an element,
    or a list of elements."""
    node = etree.Element(tag)
    if value is None:
        return node
    if isinstance(value, str):
        node.text = value
    elif isinstance(value, etree._Element):
        node.append(value)
    else:
        for child in value:
            node.append(child)
    return node


class Multistatus:
    """Accumulates <response> elements and serializes a 207 body."""

    def __init__(self):
        self.root = etree.Element(D("multistatus"), nsmap={
            "d": NS_DAV, "cal": NS_CALDAV, "card": NS_CARDDAV,
            "cs": NS_CS, "ical": NS_ICAL,
        })
        self.sync_token = None

    def add_props(self, href, found, missing=(), forbidden=()):
        """``found``: list of (tag, value). ``missing``: tags answered 404."""
        resp = etree.SubElement(self.root, D("response"))
        resp.append(href_el(href))
        if found:
            ps = etree.SubElement(resp, D("propstat"))
            prop = etree.SubElement(ps, D("prop"))
            for tag, value in found:
                prop.append(prop_value(tag, value))
            ps.append(status_el(200))
        for tags, code in ((missing, 404), (forbidden, 403)):
            if tags:
                ps = etree.SubElement(resp, D("propstat"))
                prop = etree.SubElement(ps, D("prop"))
                for tag in tags:
                    etree.SubElement(prop, tag)
                ps.append(status_el(code))
        if not found and not missing and not forbidden:
            # A response must carry a propstat or a status.
            resp.append(status_el(200))
        return resp

    def add_status(self, href, code):
        resp = etree.SubElement(self.root, D("response"))
        resp.append(href_el(href))
        resp.append(status_el(code))
        return resp

    def to_bytes(self):
        if self.sync_token is not None:
            token = etree.SubElement(self.root, D("sync-token"))
            token.text = self.sync_token
        return etree.tostring(self.root, xml_declaration=True, encoding="utf-8")


def error_body(*conditions):
    """<d:error> body carrying precondition elements (Clark tags)."""
    root = etree.Element(D("error"), nsmap={"d": NS_DAV, "cal": NS_CALDAV, "card": NS_CARDDAV})
    for tag in conditions:
        etree.SubElement(root, tag)
    return etree.tostring(root, xml_declaration=True, encoding="utf-8")


def requested_props(root):
    """From a PROPFIND/REPORT root: ('allprop'|'propname'|'prop', [tags], prop_element)."""
    if root is None:
        return "allprop", [], None
    for child in root:
        if child.tag == D("allprop"):
            return "allprop", [], None
        if child.tag == D("propname"):
            return "propname", [], None
        if child.tag == D("prop"):
            return "prop", [c.tag for c in child if isinstance(c.tag, str)], child
    return "allprop", [], None
