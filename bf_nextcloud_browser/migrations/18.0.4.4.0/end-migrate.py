"""18.0.4.4.0 : les libellés passent d'une source française à une source anglaise.

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

MODULE = "bf_nextcloud_browser"
# (fichier, identifiant, champ) -> français livré avant 18.0.4.4.0
LIVRES_FR = {('security/bf_nc_browser_security.xml', 'group_nc_browser_user', 'comment'): 'Donne accès au '
                                                                              'navigateur de '
                                                                              'fichiers Nextcloud '
                                                                              'embarqué dans les '
                                                                              'projets et tâches.',
 ('security/bf_nc_browser_security.xml', 'group_nc_browser_user', 'name'): 'Navigateur Nextcloud'}
# Langues dont la clé, si l'en_US lui est égal, est la sortie d'un ancien crochet (pas une retouche).
CROCHET = []


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
        if en_us not in (francais, _forme(rec_en, champ, francais)) and not crochet:
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
                if lang.startswith("fr") or (brut.get(lang) or "").strip() not in formes:
                    continue
                # La valeur en_US telle qu'Odoo l'a RANGÉE (assainie), pas le texte brut du fichier.
                cr.execute(SQL("UPDATE %s SET %s = %s || jsonb_build_object(%s, %s::text) WHERE id = %s",
                               SQL.identifier(porteur._table), SQL.identifier(champ),
                               SQL.identifier(champ), lang, brut.get("en_US") or anglais, porteur.id))
                _logger.info("%s : %s.%s, clé %s passée à l'anglais livré", MODULE, xmlid, champ, lang)
            porteur.invalidate_recordset([champ])
