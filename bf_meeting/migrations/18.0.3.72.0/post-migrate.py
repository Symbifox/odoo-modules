"""Les documents de l'onglet partent avec le courriel et le PDF.

Deux choses à rattraper sur une base existante.

1. **Les pièces d'avant.** Une pièce de l'onglet est désormais marquée
   (`ir.attachment.bf_meeting_document`) à sa création. Celles qui existent
   déjà ne le sont pas. Le même `res_model` / `res_id` porte aussi d'autres
   pièces, qui ne doivent pas partir aux participants :

   * les pièces du chatter (courriels importés, messages vocaux, PDF d'un
     envoi), reconnues à leur lien vers un `mail.message` ;
   * la copie d'échange du compte rendu (`compte-rendu-<…>.json`, régénérée à
     chaque envoi et déjà jointe au courriel) ;
   * les images collées dans un champ texte.

   Et surtout les fichiers déposés au chatter par le trombone ou le
   glisser-déposer : Odoo 18 les pose directement sur la fiche, sans
   `mail.message` ni jeton. En base, rien ne les distingue d'un fichier de
   l'onglet. On ne marque donc que les LIENS (`type = 'url'`) : le chatter ne
   crée que des fichiers. Les fichiers d'avant restent non marqués ; la case
   « Envoyé aux participants » de l'onglet les fait partir d'un geste. Le doute
   penche vers « ne pas envoyer » : une pièce partie ne se rattrape pas.

   Pas les liens d'un compte rendu DÉJÀ envoyé : son PDF est régénéré à la
   lecture par le portail, et un lien coché après coup y paraîtrait comme
   s'il était parti avec le courriel. Ce qui n'est pas parti ne doit pas
   paraître parti. Les liens d'un OdJ, eux, sont cochés : ils partiront au
   prochain envoi, et c'est le défaut que cette version corrige.

2. **Les traductions du courriel.** `mail.template.body_html` se traduit EN
   BLOC : la mise à jour du module écrit la nouvelle source (`en_US`) mais ne
   touche pas les autres clés (constaté sur une base d'essai pour 18.0.3.69.0). On insère
   la section avant le paragraphe de clôture, s'il y figure exactement une
   fois ; sinon on journalise et on laisse la traduction telle quelle.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

TEMPLATES = (
    "bf_meeting.meeting_agenda_mail_template",
    "bf_meeting.meeting_report_mail_template",
)

ANCHOR = '<p style="font-size:14px; line-height:22px; color:#6B7280; margin:28px 0 0 0;">'

# Identique dans toutes les langues : « Documents » se dit pareil en français.
BLOCK = """<t t-set="shared_documents" t-value="object._bf_shared_document_links()"/>
<t t-if="shared_documents">
<h3 t-attf-style="color:{{ brand_dark }}; font-size:16px; font-weight:600; margin:24px 0 10px 0; padding-left:10px; border-left-width:4px; border-left-style:solid; border-left-color:{{ brand_primary }};">Documents</h3>
<ul style="margin:0 0 20px 0; padding-left:20px; color:#374151; font-size:15px; line-height:24px;">
    <t t-foreach="shared_documents" t-as="document">
        <li style="margin:0 0 6px 0;"><a t-att-href="document['url']" t-attf-style="color:{{ brand_dark }}; text-decoration:underline;"><t t-out="document['name']"/></a></li>
    </t>
</ul>
</t>

"""


def mark_existing_documents(cr):
    """Marquer les pièces de l'onglet posées avant cette version."""
    cr.execute(r"""
        UPDATE ir_attachment a
           SET bf_meeting_document = TRUE
         WHERE a.res_model IN ('meeting.agenda', 'meeting.record')
           AND a.res_id IS NOT NULL
           AND a.res_field IS NULL
           AND a.bf_meeting_document IS NOT TRUE
           AND NOT EXISTS (SELECT 1 FROM message_attachment_rel r
                            WHERE r.attachment_id = a.id)
           AND a.type = 'url'
           AND NOT (a.res_model = 'meeting.record' AND EXISTS (
                    SELECT 1 FROM meeting_record mr
                     WHERE mr.id = a.res_id AND mr.report_sent_date IS NOT NULL))
    """)
    return cr.rowcount


def insert_block(body):
    """Le corps avec la section, ou None s'il faut le laisser tel quel."""
    if "_bf_shared_document_links" in body:
        return None
    if body.count(ANCHOR) != 1:
        return None
    return body.replace(ANCHOR, BLOCK + ANCHOR)


def migrate(cr, version):
    marked = mark_existing_documents(cr)
    _logger.info("bf_meeting 18.0.3.72.0 : %s pièce(s) de l'onglet Documents marquée(s)", marked)

    env = api.Environment(cr, SUPERUSER_ID, {})
    # Le chargement des données a écrit la nouvelle source par l'ORM, pas
    # encore en base : sans ce flush, les lectures SQL voient l'ancienne.
    env.flush_all()
    for xmlid in TEMPLATES:
        template = env.ref(xmlid, raise_if_not_found=False)
        if not template:
            continue
        cr.execute(
            "SELECT jsonb_object_keys(body_html) FROM mail_template WHERE id = %s",
            [template.id])
        langs = [lang for (lang,) in cr.fetchall() if lang != "en_US"]
        for lang in langs:
            cr.execute("SELECT body_html ->> %s FROM mail_template WHERE id = %s",
                       [lang, template.id])
            body = cr.fetchone()[0] or ""
            if "_bf_shared_document_links" in body:
                continue
            new_body = insert_block(body)
            if new_body is None:
                _logger.warning(
                    "bf_meeting 18.0.3.72.0 : traduction %s de %s laissée telle quelle "
                    "(le paragraphe de clôture n'y est pas exactement une fois)",
                    lang, xmlid)
                continue
            cr.execute(
                "UPDATE mail_template SET body_html = jsonb_set(body_html, %s, to_jsonb(%s::text)) "
                "WHERE id = %s",
                [[lang], new_body, template.id])
            _logger.info("bf_meeting 18.0.3.72.0 : section Documents ajoutée à %s en %s",
                         xmlid, lang)
        template.invalidate_recordset(["body_html"])
