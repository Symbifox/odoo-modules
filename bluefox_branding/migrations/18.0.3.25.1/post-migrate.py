"""L'en-tête foncé des courriels prend le logo de marque.

Les en-têtes des courriels brandés sont sur la couleur foncée de la marque, et ils
demandaient `/brand/logo/<société>`, c'est-à-dire le logo ORDINAIRE, souvent foncé lui
aussi : un monogramme noir sur un fond presque noir ne se voit pas. Le champ prévu pour ces fonds,
`report_brand_logo` (« Logo sur fond foncé »), n'était lu par aucun en-tête. Ils
demandent désormais la variante `brand` de la route : le logo de marque s'il existe,
sinon le logo ordinaire, donc rien ne change pour une société qui n'en a pas.

La mise en page (vue) se met à jour d'elle-même. Les gabarits système surchargés ne
sont écrits que par `post_init_hook` : on ne rejoue pas le crochet, qui écraserait une
retouche faite à la main chez un locataire, on remplace seulement l'adresse du logo
dans chaque langue stockée de ces gabarits. L'« Avis de retard » sur facture, écrit par le
même crochet sans xmlid, demandait `/web/image/res.company/<id>/logo` : le logo ordinaire,
et une image grise pour le lecteur anonyme d'une société secondaire. Il passe aussi à la
route du logo de marque.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

AVANT = 't-attf-src="/brand/logo/{{ company.id }}"'
APRES = 't-attf-src="/brand/logo/{{ company.id }}/brand"'
AVANT_RETARD = 't-attf-src="/web/image/res.company/{{ company.id }}/logo"'

REMPLACER = """
    UPDATE mail_template t
       SET body_html = (SELECT jsonb_object_agg(j.k, replace(j.v, %s, %s))
                          FROM jsonb_each_text(t.body_html) AS j(k, v))
     WHERE t.id = ANY(%s)
       AND t.body_html IS NOT NULL
       AND EXISTS (SELECT 1 FROM jsonb_each_text(t.body_html) AS j(k, v)
                    WHERE strpos(j.v, %s) > 0)
 RETURNING t.id"""


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    from odoo.addons.bluefox_branding.hooks import _extract_templates_from_xml
    xml_ids = set(_extract_templates_from_xml()) | set(
        _extract_templates_from_xml("mail_template_overrides_en.xml"))
    ids = [t.id for t in (env.ref(x, raise_if_not_found=False) for x in sorted(xml_ids)) if t]
    retard = env["mail.template"].with_context(active_test=False).search([
        ("model", "=", "account.move"), ("name", "ilike", "Avis de retard")]).ids
    modifies = []
    if ids:
        cr.execute(REMPLACER, [AVANT, APRES, ids, AVANT])
        modifies += [r[0] for r in cr.fetchall()]
    if retard:
        cr.execute(REMPLACER, [AVANT_RETARD, APRES, retard, AVANT_RETARD])
        modifies += [r[0] for r in cr.fetchall()]
    env.invalidate_all()
    _logger.info("bluefox_branding 18.0.3.25.1 : logo de marque dans l'en-tête de %d gabarit(s) "
                 "sur %d", len(modifies), len(ids) + len(retard))
