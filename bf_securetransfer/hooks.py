"""Install/upgrade hooks — email-template i18n.

The two mail templates that recipients/senders receive (link + receipt) and the
download notice are ``noupdate="1"`` so an operator can tweak them without an
upgrade clobbering their edits. But that same flag means Odoo's translation
import skips them, so their en_CA slot never gets populated from the .po, and
recipients whose Odoo contact is English would still receive French.

This hook fixes that deterministically: it clears the templates' forced ``lang``
(so send-time context language — the recipient's contact language — wins) and
writes each template's en_CA body_html/subject by applying the .po term map to
the source. Called on install (post_init_hook) and on each version bump
(migrations/.../post-migrate.py), so it is self-healing without touching
noupdate.
"""
import logging
import os
import re

_logger = logging.getLogger(__name__)

_TEMPLATE_XIDS = (
    "mail_template_transfer_link",
    "mail_template_secure_message",
    "mail_template_transfer_receipt",
    "mail_template_download_notice",
)

# Full-string English subjects (subject is a whole-value translated field).
_EN_SUBJECTS = {
    "mail_template_transfer_link":
        "{{ object.sender_name or object.sender_email }} sent you files "
        "— {{ object.name }}",
    "mail_template_secure_message":
        "{{ object.sender_name or object.sender_email }} sent you a secure "
        "message — {{ object.name }}",
    "mail_template_transfer_receipt":
        "Your transfer is online — {{ object.name }}",
    "mail_template_download_notice":
        "Your files were downloaded — {{ object.name }}",
}


def _po_template_terms():
    """FR→EN term map for mail-template body_html, parsed from en_CA.po."""
    path = os.path.join(os.path.dirname(__file__), "i18n", "en_CA.po")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        po = fh.read()

    def unquote(chunk):
        return "".join(
            p.replace('\\"', '"').replace("\\\\", "\\")
            for p in re.findall(r'"((?:[^"\\]|\\.)*)"', chunk))

    terms = {}
    for block in re.split(r"\n\n+", po):
        if "model_terms:mail.template,body_html" not in block:
            continue
        mid = re.search(r'(?s)\nmsgid ((?:"(?:[^"\\]|\\.)*"\s*)+)\nmsgstr', "\n" + block)
        mst = re.search(r'(?s)\nmsgstr ((?:"(?:[^"\\]|\\.)*"\s*)+)', "\n" + block)
        if mid and mst:
            src, tgt = unquote(mid.group(1)), unquote(mst.group(1))
            if src and tgt and src != tgt:
                terms[src] = tgt
    return terms


def apply_email_translations(env):
    """Clear the forced lang and populate the en_CA slot of the three
    contact-facing templates. Idempotent."""
    terms = _po_template_terms()
    terms_sorted = sorted(terms.items(), key=lambda kv: -len(kv[0]))
    for xid in _TEMPLATE_XIDS:
        tmpl = env.ref("bf_securetransfer.%s" % xid, raise_if_not_found=False)
        if not tmpl:
            continue
        tmpl.sudo().lang = False
        src = tmpl.sudo().with_context(lang="en_US").body_html or tmpl.sudo().body_html or ""
        # str(): body_html is a markupsafe.Markup, whose .replace() ESCAPES its
        # arguments — a term containing inline markup (e.g. "<strong>…</strong>")
        # would be escaped to "&lt;strong&gt;…" and never match the raw HTML. A
        # plain str .replace() matches the markup verbatim (no-op difference for
        # tag-free terms, so existing translations are unaffected).
        en_body = str(src)
        for fr, en in terms_sorted:
            en_body = en_body.replace(fr, en)
        env.cr.execute(
            "UPDATE mail_template SET "
            "body_html = jsonb_set(COALESCE(body_html, '{}'::jsonb), '{en_CA}', to_jsonb(%s::text)), "
            "subject = jsonb_set(COALESCE(subject, '{}'::jsonb), '{en_CA}', to_jsonb(%s::text)) "
            "WHERE id = %s",
            (en_body, _EN_SUBJECTS.get(xid, ""), tmpl.id),
        )
    env.registry.clear_cache()
    _logger.info("bf_securetransfer: en_CA email templates applied (%d terms).",
                 len(terms))


def post_init_hook(env):
    apply_email_translations(env)
