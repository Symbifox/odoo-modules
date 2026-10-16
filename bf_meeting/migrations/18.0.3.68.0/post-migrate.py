"""Surfaces client en source anglaise, langue choisie d'après les lecteurs.

Trois gestes, après le chargement des données (la clé `en_US` porte déjà
l'anglais) et AVANT le catalogue :

1. Les autres clés des courriels (objet, corps) et des libellés « Discuter à »,
   d'après l'ancienne source relevée par pre-migrate :

   * une clé ANGLAISE (en_CA, là où elle existe) part : la source anglaise sert
     désormais tout lecteur anglophone, et l'ancienne traduction faite à la
     main en base aurait divergé d'elle au premier changement ;
   * une clé française qui n'était qu'une COPIE de l'ancienne source reste :
     c'est mot pour mot le français ;
   * une COPIE dans une autre langue part : son lecteur recevait du français
     faute de mieux, il reçoit maintenant la source anglaise ;
   * une vraie traduction (retouche) reste, quelle que soit la langue ;
   * une langue française active SANS clé (base qui n'avait que `en_US`) reçoit l'ancienne source, sans attendre le catalogue, qui ne
     couvre que fr_CA.

2. `lang_manual` (nouveau) :

   * comptes rendus : TOUS figés. Leur langue vient de la langue parlée
     (meeting-processor) ou de leur ordre du jour, et jusqu'ici elle ne
     bougeait jamais ; rien ne change pour eux ;
   * ordres du jour : figé quand la langue n'est pas celle que l'ancienne
     règle donnait (rendez-vous d'un demandeur, choix au formulaire). On ne
     peut pas le savoir autrement ; la règle d'avant se rejoue ici.

3. Les ordres du jour PAS ENCORE ENVOYÉS, à venir, et non figés, reprennent
   la langue de la nouvelle règle. L'historique envoyé ne bouge pas.
"""

import json
import logging
from datetime import timedelta

from odoo import SUPERUSER_ID, api, fields

_logger = logging.getLogger(__name__)

TABLES = {"mail_template", "ir_model_fields_selection"}
CHAMPS = {"subject", "body_html", "name"}


def _source(cr):
    cr.execute("SELECT to_regclass('pg_temp.bf_meeting_t25549_source')")
    if not cr.fetchone()[0]:
        _logger.warning(
            "bf_meeting 18.0.3.68.0 : relevé de la source introuvable (pre-migrate pas "
            "passé dans cette connexion) ; traductions des courriels laissées telles quelles")
        return []
    cr.execute("SELECT tbl, res_id, champ, source FROM bf_meeting_t25549_source")
    return cr.fetchall()


def reconcilier_traductions(env):
    cr = env.cr
    actives = [code for code, _nom in env["res.lang"].get_installed()]
    for tbl, res_id, champ, source in _source(cr):
        if tbl not in TABLES or champ not in CHAMPS or not source:
            continue
        cr.execute(f"SELECT {champ} FROM {tbl} WHERE id = %s", [res_id])
        ligne = cr.fetchone()
        valeurs = dict((ligne and ligne[0]) or {})
        if not valeurs or valeurs.get("en_US") == source:
            # Les données n'ont pas réécrit la source : rien à réaligner.
            continue
        avant = dict(valeurs)
        for lang, valeur in avant.items():
            if lang == "en_US":
                continue
            if lang.startswith("en"):
                del valeurs[lang]
                if valeur != source:
                    _logger.info("bf_meeting 18.0.3.68.0 : %s %s %s : traduction anglaise "
                                 "en base remplacée par la source", tbl, res_id, lang)
            elif valeur == source and not lang.startswith("fr"):
                del valeurs[lang]
        for lang in actives:
            if lang.startswith("fr") and lang not in valeurs:
                valeurs[lang] = source
        if valeurs != avant:
            cr.execute(f"UPDATE {tbl} SET {champ} = %s::jsonb WHERE id = %s",
                       [json.dumps(valeurs), res_id])
            _logger.info("bf_meeting 18.0.3.68.0 : %s %s %s : clés %s → %s", tbl, res_id,
                         champ, sorted(avant), sorted(valeurs))
    cr.execute("DROP TABLE IF EXISTS bf_meeting_t25549_source")


def _ancienne_regle(rec, actives):
    """La règle d'avant 18.0.3.68.0 : client, personne qui crée, société."""
    societe = rec.company_id or rec.env.company
    for code in (rec.partner_id.lang, rec.create_uid.lang, societe.partner_id.lang):
        if code and code in actives:
            return code
    return actives[0] if actives else "en_US"


def marquer_et_realigner(env):
    from odoo.addons.bf_meeting.models.meeting_record import _langue_automatique

    cr = env.cr
    cr.execute("UPDATE meeting_record SET lang_manual = TRUE WHERE lang IS NOT NULL")
    _logger.info("bf_meeting 18.0.3.68.0 : %s compte(s) rendu(s) figé(s)", cr.rowcount)
    actives = [code for code, _nom in env["res.lang"].get_installed()]
    maintenant = fields.Datetime.now()
    manuels, realignes = [], []
    for rec in env["meeting.agenda"].with_context(active_test=False).search([("lang", "!=", False)]):
        en_attente = (rec.state in ("draft", "confirmed") and not rec.email_sent_date
                      and rec.date and rec.date >= maintenant - timedelta(days=1))
        if rec.lang != _ancienne_regle(rec, actives):
            manuels.append(rec.id)
            if en_attente:
                # À relire après la pose : « figé » se juge sur le projet et le
                # client d'AUJOURD'HUI, qui ont pu changer depuis la création.
                _logger.warning(
                    "bf_meeting 18.0.3.68.0 : meeting.agenda %s en attente figé en %s "
                    "(ancienne règle %s, nouvelle règle %s)", rec.id, rec.lang,
                    _ancienne_regle(rec, actives), _langue_automatique(rec))
            continue
        if not en_attente:
            continue
        # Le pas « personne qui travaille » de la règle : celle qui a créé la
        # fiche, pas OdooBot qui fait la montée.
        createur = rec.create_uid.lang if rec.create_uid.lang in actives else None
        nouvelle = _langue_automatique(rec.with_context(lang=createur))
        if nouvelle != rec.lang:
            realignes.append((rec.id, rec.lang, nouvelle))
    if manuels:
        cr.execute("UPDATE meeting_agenda SET lang_manual = TRUE WHERE id IN %s", [tuple(manuels)])
    for rid, ancienne, nouvelle in realignes:
        cr.execute("UPDATE meeting_agenda SET lang = %s WHERE id = %s", [nouvelle, rid])
        _logger.warning("bf_meeting 18.0.3.68.0 : meeting.agenda %s : langue %s → %s",
                        rid, ancienne, nouvelle)
    _logger.info("bf_meeting 18.0.3.68.0 : meeting.agenda : %s figé(s), %s réaligné(s)",
                 len(manuels), len(realignes))


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    # La nouvelle source a été écrite par l'ORM : sans ce flush, la lecture SQL
    # verrait l'ancienne (constaté en 18.0.3.59.0).
    env.flush_all()
    reconcilier_traductions(env)
    marquer_et_realigner(env)
    env.invalidate_all()
