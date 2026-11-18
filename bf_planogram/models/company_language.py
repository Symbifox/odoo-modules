# -*- coding: utf-8 -*-
"""The language a stored chatter body comes out in.

A chatter body is KEPT and read later by someone else than whoever wrote it: the manager
opens in the morning what the evening shift froze. Both translation functions of Odoo 18
read the language off the context and nowhere else, and outside a web request the context
carries no language at all, so a line written by a scheduled action would freeze an English
sentence in the thread of a store that reads French, for ever.

So the body comes out in the language of the COMPANY the fixture belongs to, never in the
language of whoever set the gesture off. A `UserError` does not come through here, on
purpose: it is transient, it is shown to the person who just made the gesture, and it stays
in the language of its reader.

⚠️ This is the same decision, and nearly the same twelve lines, as `bf_retail` carries in
`res_company.py`. It is copied rather than imported because this module does not depend on
the retail suite: a pharmacy, a hardware store or a library installs it for its shelves. The
16th finding of the conception (two translation helpers living in one suite) now has a third
copy, and the honest home for it is a base module neither of them owns yet.
"""


def in_company_language(record, company):
    """`record` read back in the language of `company`, for a body the chatter keeps."""
    lang = company.partner_id.lang if company else False
    if not lang or lang == record.env.context.get("lang"):
        return record
    # `env.lang` RAISES on a language code that is not active, and freezing a planogram must
    # not die over the language of a thread body.
    if not record.env["res.lang"]._get_code(lang):
        return record
    return record.with_context(lang=lang)
