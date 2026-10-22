"""18.0.2.3.2: the nominative report's new name and the aggregate
report's new file name reach readers in every language.

An upgrade loads the catalogue without overwriting: a language key already in
the database wins. The name « Rapport de campagne » and the file name without
the reference, shipped earlier, would stay. Only those exact shipped values are
removed (a translation retouched by the client stays), then the catalogue fills
the empty keys.
"""
from odoo import SUPERUSER_ID, api
from odoo.tools import SQL

M = "bf_security_awareness"
ANCIENNES = {
    ("action_report_phishing_campaign", "name"): {"Campaign report", "Rapport de campagne"},
    ("action_report_phishing_campaign_aggregate", "print_report_name"): {
        "'Aggregate report - %s' % object.template_id.name",
        "'Rapport agrégé - %s' % object.template_id.name",
    },
}


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env.flush_all()  # the database, not an ORM buffer, is read below
    langues = [code for code, _nom in env["res.lang"].get_installed() if code != "en_US"]
    if not langues:
        return
    for (xmlid, champ), anciennes in ANCIENNES.items():
        action = env.ref(f"{M}.{xmlid}", raise_if_not_found=False)
        if not action:
            continue
        cr.execute(SQL("SELECT %s FROM ir_act_report_xml WHERE id = %s",
                       SQL.identifier(champ), action.id))
        valeur = (cr.fetchone() or [None])[0]
        if not isinstance(valeur, dict):
            continue
        perimees = [l for l in langues if valeur.get(l) in anciennes]
        for lang in perimees:
            cr.execute(SQL("UPDATE ir_act_report_xml SET %s = %s - %s WHERE id = %s",
                           SQL.identifier(champ), SQL.identifier(champ), lang, action.id))
    env["ir.actions.report"].invalidate_model()
    env["ir.module.module"]._load_module_terms([M], langues, overwrite=False)
