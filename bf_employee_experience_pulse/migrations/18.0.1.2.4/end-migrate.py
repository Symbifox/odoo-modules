"""18.0.1.2.4 : les libellés passent d'une source française à une source anglaise.

Odoo ne traduit jamais vers en_US, la langue source : tant que la source était
française, un usager réglé en anglais lisait ce module en français.

🔴 Une mise à jour n'écrase jamais une traduction existante : le catalogue du
module est rechargé en écrasant (le catalogue d'avant ne traduisait rien, ou
recopiait le français).

Les données `noupdate` gardent leur valeur en_US française après la mise à jour.
On bascule chaque champ SEULEMENT s'il porte encore le texte livré; l'anglais est
lu dans le fichier de données du module. Une valeur retouchée est laissée.

🔴 Une retouche qui n'a pas de clé dans une langue installée (faite dans une
interface anglaise, ou colonne qui devient traduisible) recevrait du catalogue le
français LIVRÉ, qui la masquerait : `post-migrate.py` l'y recopie AVANT le catalogue.

⚠️ En `end` : Odoo charge les traductions du module juste après le `post`.
"""

import logging

from lxml import etree

from odoo import SUPERUSER_ID, api
from odoo.tools import SQL, file_open

_logger = logging.getLogger(__name__)

MODULE = "bf_employee_experience_pulse"
# (fichier, identifiant, champ) -> français livré avant 18.0.1.2.4
LIVRES_FR = {('data/ir_cron.xml', 'cron_pulse_flush', 'name'): 'Pulse : verser le sas et recalculer les scores',
 ('data/mail_template.xml', 'mail_template_pulse_invite', 'body_html'): '<t t-set="societe" '
                                                                        't-value="object.campaign_id.company_id '
                                                                        'or user.company_id"></t>\n'
                                                                        '<t t-set="marque" '
                                                                        't-value="object.env[\'bf.ex.pulse.marque\'].couleurs(societe)"></t>\n'
                                                                        '<table '
                                                                        'role="presentation" '
                                                                        'cellpadding="0" '
                                                                        'cellspacing="0" '
                                                                        'border="0" width="100%" '
                                                                        'style="background-color:#F8FAFC;">\n'
                                                                        '  <tbody><tr><td '
                                                                        'align="center" '
                                                                        'style="padding:24px;">\n'
                                                                        '    <table '
                                                                        'role="presentation" '
                                                                        'cellpadding="0" '
                                                                        'cellspacing="0" '
                                                                        'border="0" width="600" '
                                                                        'style="width:600px; '
                                                                        'max-width:600px; margin:0 '
                                                                        'auto; '
                                                                        'background-color:#FFFFFF; '
                                                                        'border-radius:12px; '
                                                                        'border:1px solid #E5E7EB; '
                                                                        'border-collapse:collapse;">\n'
                                                                        '      <tbody>\n'
                                                                        '        <tr>\n'
                                                                        '          <td '
                                                                        't-attf-style="background-color:{{ '
                                                                        "marque['sombre'] }}; "
                                                                        'padding:16px 24px; '
                                                                        'border-radius:12px 12px 0 '
                                                                        '0;">\n'
                                                                        '            <table '
                                                                        'role="presentation" '
                                                                        'width="100%" '
                                                                        'cellpadding="0" '
                                                                        'cellspacing="0" '
                                                                        'border="0"><tbody><tr>\n'
                                                                        '              <td '
                                                                        'align="left">\n'
                                                                        '                <img '
                                                                        't-att-src="marque[\'logo\']" '
                                                                        'alt="" '
                                                                        'style="height:40px; '
                                                                        'width:auto; '
                                                                        'display:block; '
                                                                        'border:0;">\n'
                                                                        '              </td>\n'
                                                                        '              <td '
                                                                        'align="right" '
                                                                        't-attf-style="color:{{ '
                                                                        "marque['texte_sur_sombre'] "
                                                                        '}}; '
                                                                        "font-family:'Lexend','Segoe "
                                                                        "UI',Arial,sans-serif; "
                                                                        'font-size:20px; '
                                                                        'font-weight:800; '
                                                                        'letter-spacing:0.2px;">\n'
                                                                        '                Votre '
                                                                        'pouls\n'
                                                                        '              </td>\n'
                                                                        '            '
                                                                        '</tr></tbody></table>\n'
                                                                        '          </td>\n'
                                                                        '        </tr>\n'
                                                                        '        <tr><td '
                                                                        't-attf-style="height:4px; '
                                                                        'line-height:4px; '
                                                                        'background-color:{{ '
                                                                        "marque['accent'] "
                                                                        '}};">&amp;#160;</td></tr>\n'
                                                                        '        <tr>\n'
                                                                        '          <td '
                                                                        'style="padding:24px; '
                                                                        "font-family:'Lexend','Segoe "
                                                                        "UI',Arial,sans-serif; "
                                                                        'color:#2E3132;">\n'
                                                                        '            <p '
                                                                        'style="font-size:15px; '
                                                                        'margin:0 0 12px '
                                                                        '0;">Bonjour,</p>\n'
                                                                        '            <p '
                                                                        'style="font-size:15px; '
                                                                        'margin:0 0 12px 0;">\n'
                                                                        '              Voici votre '
                                                                        'lien pour la vague\n'
                                                                        '              <b '
                                                                        't-out="object.campaign_id.name '
                                                                        'or \'\'"></b>. Ça prend '
                                                                        'deux minutes.\n'
                                                                        '            </p>\n'
                                                                        '            <div '
                                                                        'style="text-align:center; '
                                                                        'margin:28px 0;">\n'
                                                                        '              <a '
                                                                        't-attf-href="{{ '
                                                                        'object.campaign_id.get_base_url() '
                                                                        '}}/pulse/{{ object.token '
                                                                        '}}" '
                                                                        't-attf-style="background-color:{{ '
                                                                        "marque['bouton'] }}; "
                                                                        'color:#FFFFFF; '
                                                                        'text-decoration:none; '
                                                                        'padding:12px 28px; '
                                                                        'border-radius:6px; '
                                                                        'font-size:15px; '
                                                                        'font-weight:600; '
                                                                        'display:inline-block;">\n'
                                                                        '                Répondre\n'
                                                                        '              </a>\n'
                                                                        '            </div>\n'
                                                                        '            <table '
                                                                        'role="presentation" '
                                                                        'width="100%" '
                                                                        'cellpadding="0" '
                                                                        'cellspacing="0" '
                                                                        'border="0" '
                                                                        't-attf-style="border-left:3px '
                                                                        "solid {{ marque['accent'] "
                                                                        '}}; '
                                                                        'background-color:#F8FAFC; '
                                                                        'border-radius:0 6px 6px '
                                                                        '0;">\n'
                                                                        '              '
                                                                        '<tbody><tr><td '
                                                                        'style="padding:12px 16px; '
                                                                        "font-family:'Lexend','Segoe "
                                                                        "UI',Arial,sans-serif; "
                                                                        'font-size:13px; '
                                                                        'color:#4B5563;">\n'
                                                                        '                Vos '
                                                                        'réponses ne portent pas '
                                                                        'votre nom. Le lien sait '
                                                                        'que vous avez\n'
                                                                        '                répondu, '
                                                                        'pour ne pas vous relancer '
                                                                        'pour rien, et il ne '
                                                                        'retient ni\n'
                                                                        "                l'heure "
                                                                        'ni le contenu de votre '
                                                                        'réponse. Un résultat ne '
                                                                        "s'affiche\n"
                                                                        '                '
                                                                        "qu'au-dessus de <t "
                                                                        't-out="object.campaign_id.score_threshold '
                                                                        'or 3"></t>\n'
                                                                        '                '
                                                                        'répondants.\n'
                                                                        '              '
                                                                        '</td></tr></tbody>\n'
                                                                        '            </table>\n'
                                                                        '            <p '
                                                                        'style="font-size:13px; '
                                                                        'color:#6B7280; '
                                                                        'margin:16px 0 0 0;">\n'
                                                                        '              Ce lien est '
                                                                        'personnel\xa0: il ne sert '
                                                                        "qu'une fois, et c'est ce "
                                                                        'qui\n'
                                                                        '              empêche une '
                                                                        'même personne de peser '
                                                                        'deux fois dans le '
                                                                        'résultat. Ne le\n'
                                                                        '              transférez '
                                                                        'pas.\n'
                                                                        '            </p>\n'
                                                                        '          </td>\n'
                                                                        '        </tr>\n'
                                                                        '        <tr>\n'
                                                                        '          <td '
                                                                        'style="padding:16px 24px '
                                                                        '20px 24px; border-top:1px '
                                                                        'solid #E5E7EB;">\n'
                                                                        '            <p '
                                                                        'style="font-family:\'Lexend\',\'Segoe '
                                                                        "UI',Arial,sans-serif; "
                                                                        'font-size:12px; '
                                                                        'color:#6B7280; '
                                                                        'margin:0;">\n'
                                                                        '              <t '
                                                                        't-if="societe.name"><b '
                                                                        't-out="societe.name"></b><br></t>\n'
                                                                        '              Message '
                                                                        'automatique, envoyé à '
                                                                        "l'ouverture d'une vague "
                                                                        'de pouls.\n'
                                                                        '            </p>\n'
                                                                        '          </td>\n'
                                                                        '        </tr>\n'
                                                                        '      </tbody>\n'
                                                                        '    </table>\n'
                                                                        '  </td></tr></tbody>\n'
                                                                        '</table>',
 ('data/mail_template.xml', 'mail_template_pulse_invite', 'name'): 'Pulse : invitation',
 ('data/mail_template.xml', 'mail_template_pulse_invite', 'subject'): 'Deux minutes pour dire '
                                                                      'comment ça va',
 ('data/pulse_metric_data.xml', 'metric_alignment', 'description'): 'Comprendre où va '
                                                                    "l'entreprise, et voir en quoi "
                                                                    'son propre travail y '
                                                                    'contribue.',
 ('data/pulse_metric_data.xml', 'metric_alignment', 'name'): 'Alignement',
 ('data/pulse_metric_data.xml', 'metric_ambassador', 'description'): 'Recommanderiez-vous cet '
                                                                     "employeur? C'est l'axe qui "
                                                                     "porte l'eNPS.",
 ('data/pulse_metric_data.xml', 'metric_ambassador', 'name'): 'Fierté et recommandation',
 ('data/pulse_metric_data.xml', 'metric_autonomy', 'description'): 'La latitude de décider comment '
                                                                   'le travail se fait, et les '
                                                                   'moyens qui vont avec.',
 ('data/pulse_metric_data.xml', 'metric_autonomy', 'name'): 'Autonomie',
 ('data/pulse_metric_data.xml', 'metric_feedback', 'description'): "Est-ce qu'on vous dit où vous "
                                                                   'en êtes, assez souvent et '
                                                                   'assez clairement pour que ça '
                                                                   'serve?',
 ('data/pulse_metric_data.xml', 'metric_feedback', 'name'): 'Rétroaction',
 ('data/pulse_metric_data.xml', 'metric_growth', 'description'): 'Apprendre, monter en compétence, '
                                                                 'voir un chemin devant soi.',
 ('data/pulse_metric_data.xml', 'metric_growth', 'name'): 'Progression',
 ('data/pulse_metric_data.xml', 'metric_manager', 'description'): 'La qualité du lien avec la '
                                                                  "personne à qui l'on se rapporte "
                                                                  ': disponibilité, confiance, '
                                                                  'franchise dans les deux sens.',
 ('data/pulse_metric_data.xml', 'metric_manager', 'name'): 'Relation avec le gestionnaire',
 ('data/pulse_metric_data.xml', 'metric_peers', 'description'): "L'entraide au quotidien et le "
                                                                'climat entre les personnes qui '
                                                                'travaillent ensemble.',
 ('data/pulse_metric_data.xml', 'metric_peers', 'name'): 'Relation avec les collègues',
 ('data/pulse_metric_data.xml', 'metric_recognition', 'description'): 'Le travail bien fait se '
                                                                      'remarque-t-il, et se '
                                                                      'remarque-t-il auprès des '
                                                                      'bonnes personnes?',
 ('data/pulse_metric_data.xml', 'metric_recognition', 'name'): 'Reconnaissance',
 ('data/pulse_metric_data.xml', 'metric_satisfaction', 'description'): 'Le contentement général, '
                                                                       'conditions comprises.',
 ('data/pulse_metric_data.xml', 'metric_satisfaction', 'name'): 'Satisfaction',
 ('data/pulse_metric_data.xml', 'metric_workload', 'description'): 'La charge de travail, le '
                                                                   "rythme et ce qu'ils laissent "
                                                                   "d'énergie. C'est l'axe qui "
                                                                   'touche aux risques '
                                                                   'psychosociaux.',
 ('data/pulse_metric_data.xml', 'metric_workload', 'name'): 'Charge et bien-être',
 ('data/pulse_question_data.xml', 'question_alignment_1', 'title'): 'Vous comprenez où '
                                                                    "l'entreprise s'en va.",
 ('data/pulse_question_data.xml', 'question_alignment_2', 'title'): 'Vous voyez en quoi votre '
                                                                    'travail y contribue.',
 ('data/pulse_question_data.xml', 'question_autonomy_1', 'title'): 'Vous décidez de la façon dont '
                                                                   'votre travail se fait.',
 ('data/pulse_question_data.xml', 'question_autonomy_2', 'title'): "Vous avez les outils qu'il "
                                                                   'faut pour bien faire ce '
                                                                   'travail.',
 ('data/pulse_question_data.xml', 'question_enps', 'hint'): '0 signifie jamais, 10 signifie sans '
                                                            'hésiter.',
 ('data/pulse_question_data.xml', 'question_enps', 'title'): 'Recommanderiez-vous cet employeur à '
                                                             "quelqu'un que vous estimez?",
 ('data/pulse_question_data.xml', 'question_feedback_1', 'title'): 'Vous savez où vous en êtes '
                                                                   'dans votre travail.',
 ('data/pulse_question_data.xml', 'question_feedback_2', 'title'): 'La rétroaction que vous '
                                                                   'recevez vous sert à quelque '
                                                                   'chose.',
 ('data/pulse_question_data.xml', 'question_growth_1', 'title'): 'Vous apprenez des choses utiles '
                                                                 'dans votre poste.',
 ('data/pulse_question_data.xml', 'question_growth_2', 'title'): 'Vous voyez un chemin possible '
                                                                 'pour la suite.',
 ('data/pulse_question_data.xml', 'question_manager_1', 'title'): 'Votre gestionnaire est '
                                                                  'disponible quand vous en avez '
                                                                  'besoin.',
 ('data/pulse_question_data.xml', 'question_manager_2', 'title'): 'Vous pouvez dire à votre '
                                                                  'gestionnaire ce qui ne va pas.',
 ('data/pulse_question_data.xml', 'question_peers_1', 'title'): 'Vos collègues vous donnent un '
                                                                'coup de main quand ça compte.',
 ('data/pulse_question_data.xml', 'question_peers_2', 'title'): 'Le climat dans votre équipe vous '
                                                                'convient.',
 ('data/pulse_question_data.xml', 'question_recognition_1', 'title'): 'Votre travail est reconnu à '
                                                                      'sa juste valeur.',
 ('data/pulse_question_data.xml', 'question_recognition_2', 'title'): "Qu'est-ce qui mériterait "
                                                                      "d'être reconnu et ne l'est "
                                                                      'pas?',
 ('data/pulse_question_data.xml', 'question_satisfaction_1', 'title'): 'Tout compte fait, vous '
                                                                       "êtes content d'y "
                                                                       'travailler.',
 ('data/pulse_question_data.xml', 'question_satisfaction_2', 'title'): 'Vos conditions de travail '
                                                                       'vous paraissent justes.',
 ('data/pulse_question_data.xml', 'question_workload_1', 'hint'): 'Pensez aux huit dernières '
                                                                  'semaines, pas à hier.',
 ('data/pulse_question_data.xml', 'question_workload_1', 'title'): 'Votre charge de travail est '
                                                                   'tenable sur la durée.',
 ('data/pulse_question_data.xml', 'question_workload_2', 'title'): 'Vous décrochez du travail en '
                                                                   'dehors des heures.',
 ('data/pulse_question_data.xml', 'question_workload_3', 'title'): "Qu'est-ce qui pèse le plus, "
                                                                   'ces temps-ci?'}
# Langues dont la clé, si l'en_US lui est égal, est la sortie d'un ancien crochet (pas une retouche).
CROCHET = []
# Anciennes formes LIVRÉES connues (avant, après) : ramenées à l'actuelle avant de comparer.
ANCIENNES = [('"></t>', '"/>'), ('"></strong>', '"/>'), ('"></div>', '"/>'), ('"></a>', '"/>'), ('"></b>', '"/>'), ('"></td>', '"/>')]


def _n(texte):
    for avant, apres in ANCIENNES:
        texte = texte.replace(avant, apres)
    return texte


def _porteur(rec):
    """L'enregistrement qui stocke la valeur : un travail planifié, son action."""
    return rec.ir_actions_server_id if rec._name == "ir.cron" else rec


def _forme(rec, champ, valeur):
    """La valeur telle qu'Odoo la range. 🔴 Un champ HTML est ASSAINI à l'écriture : le corps
    d'un gabarit de courriel en base n'est jamais égal au texte brut du fichier de données, et la
    comparaison brute le prenait pour une retouche (corps laissés
    en français en en_US, puis recopiés dans les autres langues)."""
    try:
        return str(rec._fields[champ].convert_to_column(valeur, rec) or "").strip()
    except Exception:  # noqa: BLE001
        return valeur




def _contenu(noeud):
    """Le contenu d'un <field> comme Odoo le charge : un champ `type="html"` est la sérialisation
    HTML de ses enfants, sinon le texte."""
    if noeud is None:
        return ""
    if noeud.get("type") == "html":
        return "".join(etree.tostring(n, method="html", encoding="unicode") for n in noeud)
    return noeud.text or ""


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    langues = [code for code, _nom in env["res.lang"].get_installed() if code != "en_US"]
    # AVANT le rechargement du catalogue : après lui, fr_CA porte déjà le NOUVEAU
    # français, et une clé copiée de l'ancien fr_CA ne serait plus reconnue.
    # Gabarits de courriel du module : une langue NON française (en_CA par exemple) dont la valeur est
    # la copie exacte de fr_CA est un reste de l'ancienne source française ; on retire sa clé, et
    # le lecteur retombe sur la source anglaise. Une valeur propre à cette langue reste.
    autres = [l for l in langues if not l.startswith("fr")]
    if autres and "fr_CA" in langues:
        gabarits = env["ir.model.data"].search([("module", "=", MODULE), ("model", "=", "mail.template")])
        for champ in ("name", "subject", "body_html"):
            for lang in autres:
                cr.execute(SQL(
                    "UPDATE mail_template SET %s = %s - %s WHERE id = ANY(%s) AND %s ? %s"
                    " AND %s->>%s = %s->>'fr_CA' RETURNING id",
                    SQL.identifier(champ), SQL.identifier(champ), lang, gabarits.mapped("res_id"),
                    SQL.identifier(champ), lang, SQL.identifier(champ), lang, SQL.identifier(champ)))
                ids = [r[0] for r in cr.fetchall()]
                if ids:
                    _logger.info("%s : %s, clé %s copiée de fr_CA retirée (%s gabarit(s))", MODULE, champ, lang, len(ids))
        env["mail.template"].invalidate_model()
    if langues:
        env["ir.module.module"]._load_module_terms([MODULE], langues, overwrite=True)
    arbres = {}
    for (fichier, xmlid, champ), francais in LIVRES_FR.items():
        if fichier not in arbres:
            with file_open(f"{MODULE}/{fichier}", "rb") as f:
                arbres[fichier] = etree.parse(f)
        rec = env.ref(f"{MODULE}.{xmlid}", raise_if_not_found=False)
        noeud = arbres[fichier].find(f".//record[@id='{xmlid}']/field[@name='{champ}']")
        anglais = _contenu(noeud).strip()
        if not rec or not anglais:
            continue
        rec_en = rec.with_context(lang="en_US")
        porteur = _porteur(rec_en)
        porteur.flush_recordset([champ])  # la base, pas un tampon de l'ORM
        cr.execute(SQL("SELECT %s FROM %s WHERE id = %s", SQL.identifier(champ),
                       SQL.identifier(porteur._table), porteur.id))
        avant = (cr.fetchone() or [None])[0]
        avant = avant if isinstance(avant, dict) else {"en_US": avant or ""}
        en_us = (avant.get("en_US") or "").strip()
        if en_us in (anglais, _forme(rec_en, champ, anglais)):
            continue  # déjà l'anglais livré : rien à faire, rien à signaler
        crochet = any(en_us and (avant.get(l) or "").strip() == en_us for l in CROCHET)
        livre_fr = (francais, _forme(rec_en, champ, francais))
        if _n(en_us) not in {_n(x) for x in livre_fr} and not crochet:
            # Ses langues manquantes ont reçu la retouche à l'étape `post`.
            _logger.warning("%s : %s.%s retouché à la main, laissé tel quel", MODULE, xmlid, champ)
            continue
        # ⚠️ Un travail planifié s'écrit par son action : ir.cron.write prend un verrou
        # NOWAIT qui ferait ÉCHOUER la montée si le travail tourne à ce moment (le recalcul
        # de `cron_name` réécrit quand même sa ligne : la montée ATTEND le travail).
        if callable(porteur._fields[champ].translate):
            # 🔴 Champ traduit TERME À TERME (html_translate) : un write en en_US réassocie les
            # termes des autres langues à la nouvelle source anglaise, et le français livré, sans
            # terme commun, y serait remplacé par l'anglais. On pose la seule clé en_US.
            cr.execute(SQL("UPDATE %s SET %s = COALESCE(%s, '{}'::jsonb) || jsonb_build_object('en_US', %s::text) WHERE id = %s",
                           SQL.identifier(porteur._table), SQL.identifier(champ), SQL.identifier(champ),
                           anglais, porteur.id))
            porteur.invalidate_recordset([champ])
        else:
            porteur.write({champ: anglais})
            # 🔴 L'ORM diffère l'écriture : sans ce vidage, la relecture SQL ci-dessous lit l'ANCIEN
            # en_US, et la clé en_CA d'une sortie de crochet reçoit l'ancienne valeur.
            porteur.flush_recordset([champ])
        # Les autres langues NON françaises installées (en_CA par exemple) qui portent encore le français
        # LIVRÉ reçoivent aussi l'anglais : sinon un lecteur en_CA garde le français.
        cr.execute(SQL("SELECT %s FROM %s WHERE id = %s", SQL.identifier(champ),
                       SQL.identifier(porteur._table), porteur.id))
        brut = (cr.fetchone() or [None])[0]
        if isinstance(brut, dict):
            formes = (francais, _forme(rec_en, champ, francais), en_us)
            for lang in langues:
                # Langue non française qui portait le français livré, ou la copie de l'ancien en_US
                # (sortie du crochet) : l'anglais livré.
                if lang.startswith("fr") or _n((brut.get(lang) or "").strip()) not in {_n(x) for x in formes}:
                    continue
                # La valeur en_US telle qu'Odoo l'a RANGÉE (assainie), pas le texte brut du fichier.
                cr.execute(SQL("UPDATE %s SET %s = %s || jsonb_build_object(%s, %s::text) WHERE id = %s",
                               SQL.identifier(porteur._table), SQL.identifier(champ),
                               SQL.identifier(champ), lang, brut.get("en_US") or anglais, porteur.id))
                _logger.info("%s : %s.%s, clé %s passée à l'anglais livré", MODULE, xmlid, champ, lang)
            porteur.invalidate_recordset([champ])
