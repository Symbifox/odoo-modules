"""Porter la section « Tâches existantes discutées » dans les traductions
anglaises du compte rendu.

Le gabarit de courriel et le rapport PDF sont écrits en français : la clé
`en_US` de leurs champs traduisibles porte le texte FRANÇAIS, et les locataires
qui écrivent en anglais leur ont une traduction `en_CA` posée en base, pas
dans un `.po`. Les deux champs ne se
traduisent pas de la même façon, donc la mise à jour ne les traite pas pareil :

* le PDF (`ir.ui.view.arch_db`) se traduit TERME PAR TERME : Odoo garde les
  termes déjà traduits et recopie tel quel chaque terme neuf. Le titre neuf y
  arriverait en français ; on lui donne sa traduction ;
* le courriel (`mail.template.body_html`) se traduit EN BLOC : chaque clé autre
  que `en_US` est un gabarit complet, que la mise à jour du module ne touche PAS.
  C'est vrai de `en_CA` (vraie traduction) comme de `fr_CA`, une simple copie de
  la source sur les bases réelles, et c'est `fr_CA` que lisent presque tous les
  comptes rendus. Rejoué sur une base réelle : après le `-u`, `en_US` avait la
  section, `fr_CA` et `en_CA` non. On l'insère donc dans
  chaque clé qui ne l'a pas, juste après les éléments d'action : en anglais pour
  `en_*`, telle que la source l'écrit pour les autres.

On ne touche qu'aux langues qui ont DÉJÀ une valeur. En créer une ailleurs
fabriquerait un gabarit français dont un seul titre serait en anglais. Et si
l'ancre des éléments d'action est introuvable dans une traduction, on la laisse
telle quelle en le journalisant : mieux vaut une section absente qu'un gabarit
cassé qui n'envoie plus rien.
"""

import logging
import re

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

HEADING_FR = "Tâches existantes discutées"
HEADING_EN = "Existing tasks discussed"

# La section du courriel, en anglais, calquée sur le bloc des éléments d'action
# de la traduction existante (« Action items », « due »).
MAIL_BLOCK_EN = """

                                <t t-set="discussed_tasks" t-value="object._discussed_tasks_for_report()"/>
                                <t t-if="discussed_tasks">
                                <h3 t-attf-style="color:{{ brand_dark }}; font-size:16px; font-weight:600; margin:24px 0 10px 0; padding-left:10px; border-left:4px solid {{ brand_primary }};">Existing tasks discussed</h3>
                                <ul style="margin:0 0 20px 0; padding-left:24px; color:#374151;">
                                    <t t-foreach="discussed_tasks" t-as="task">
                                        <li style="margin-bottom:6px;">
                                            <t t-out="task.name"/>
                                            <t t-if="task.user_ids">
                                                <span style="color:#6B7280; font-size:13px;"> — <t t-foreach="task.user_ids" t-as="u"><t t-if="not u_first" t-out="', '"/><t t-out="u.name"/></t></span>
                                            </t>
                                            <t t-if="task.date_deadline">
                                                <span style="color:#6B7280; font-size:13px;"> (due <t t-out="task.date_deadline.strftime('%Y-%m-%d')"/>)</span>
                                            </t>
                                        </li>
                                    </t>
                                </ul>
                                </t>"""

# Le bloc des éléments d'action : de son `t-if` jusqu'à la fermeture qui suit sa
# liste. Non gourmand, pour ne jamais enjamber la section suivante.
ACTIONS_BLOCK = re.compile(r'<t t-if="object\.task_ids">.*?</ul>\s*</t>', re.S)
# La section telle que la source (`en_US`, en français) l'écrit après le `-u`.
SOURCE_BLOCK = re.compile(r'\s*<t t-set="discussed_tasks".*?</ul>\s*</t>', re.S)


def _keys(cr, table, column, record_id):
    cr.execute(f'SELECT jsonb_object_keys("{column}") FROM "{table}" WHERE id = %s',
               [record_id])
    return [lang for (lang,) in cr.fetchall() if lang != "en_US"]


def add_section_to_mail_translations(env):
    template = env.ref("bf_meeting.meeting_report_mail_template", raise_if_not_found=False)
    if not template:
        return
    cr = env.cr
    cr.execute("SELECT body_html ->> 'en_US' FROM mail_template WHERE id = %s", [template.id])
    source_match = SOURCE_BLOCK.search(cr.fetchone()[0] or "")
    for lang in _keys(cr, "mail_template", "body_html", template.id):
        english = lang.startswith("en_")
        if english:
            block = MAIL_BLOCK_EN
        elif source_match:
            block = source_match.group(0)
        else:
            _logger.warning(
                "bf_meeting 18.0.3.59.0 : la source du courriel n'a pas la section, "
                "traduction %s laissée telle quelle", lang)
            continue
        cr.execute("SELECT body_html ->> %s FROM mail_template WHERE id = %s",
                   [lang, template.id])
        body = cr.fetchone()[0] or ""
        if "_discussed_tasks_for_report" in body:
            # Déjà là (passe précédente, ou valeur recopiée de la source) : seul
            # le titre d'une traduction anglaise peut rester à traduire.
            new_body = body.replace(HEADING_FR, HEADING_EN) if english else body
        else:
            match = ACTIONS_BLOCK.search(body)
            if not match:
                _logger.warning(
                    "bf_meeting 18.0.3.59.0 : ancre des éléments d'action introuvable "
                    "dans la traduction %s du courriel, section non ajoutée", lang)
                continue
            new_body = body[:match.end()] + block + body[match.end():]
        if new_body != body:
            cr.execute(
                "UPDATE mail_template SET body_html = jsonb_set(body_html, %s, to_jsonb(%s::text)) "
                "WHERE id = %s",
                [[lang], new_body, template.id])
            _logger.info("bf_meeting 18.0.3.59.0 : section ajoutée au courriel en %s", lang)
    template.invalidate_recordset(["body_html"])


def translate_report_heading(env):
    view = env.ref("bf_meeting.report_meeting_record", raise_if_not_found=False)
    if not view:
        return
    langs = [lang for lang in _keys(env.cr, "ir_ui_view", "arch_db", view.id)
             if lang.startswith("en_")]
    if langs:
        view.update_field_translations(
            "arch_db", {lang: {HEADING_FR: HEADING_EN} for lang in langs})
        _logger.info("bf_meeting 18.0.3.59.0 : titre du PDF traduit en %s", ", ".join(langs))


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    # Le chargement des données a écrit la nouvelle source par l'ORM, pas
    # encore en base : sans ce flush, les lectures SQL ci-dessous voient
    # l'ancienne source, sans la section (constaté en rejouant la montée).
    env.flush_all()
    translate_report_heading(env)
    env.flush_all()
    add_section_to_mail_translations(env)
    env.invalidate_all()
