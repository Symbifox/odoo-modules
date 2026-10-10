"""bf_health 18.0.2.5.0 : reprise de ce que la 2.4.1 laissait derrière elle.

Mesures de vie privée :

1. Rappels : les crons de la 2.4.1 tournaient en superutilisateur. Leurs activités
   étaient posées par le compte système et, sauf l'étape de réduction,
   assignées à OdooBot ; les prises du jour étaient créées par lui, donc
   invisibles à la personne (règles ``create_uid``). Elles passent à la
   personne qui possède la fiche, en SQL : une écriture ORM de ``user_id``
   enverrait « une activité vous est assignée » par courriel. Leur résumé
   devient neutre et leur note, qui recopiait dosage, pharmacie, médecin ou
   clinique, se réduit au nom, si la personne ne les a pas modifiés.
2. GPX : les fichiers GPX bruts sont jetés. Les valeurs manquantes sont
   d'abord calculées depuis le fichier ; le nom du fichier est effacé.
3. Gen : voir `end-migrate.py` (conversations déjà rattachées à une fiche).
"""
import logging
import re

from markupsafe import Markup

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

#: Fiches qui reçoivent des activités des crons de la 2.4.1.
MODELES_ACTIVITES = {
    "health.medication": "health_medication",
    "health.lab.test": "health_lab_test",
    "health.screening": "health_screening",
    "health.reduction.step": "health_reduction_step",
}

#: (`\u2014` : le tiret cadratin que la 2.4.1 mettait après le nom.)
#: Ce que les crons de la 2.4.1 écrivaient, par modèle : (résumé, note en texte
#: une fois les balises retirées, `None` si le cron n'en posait pas), et le
#: résumé neutre de la 2.5.0. 🔴 Relecture adverse du 2026-10-08 : on ne
#: réécrit QUE ce qui a encore exactement cette forme. Une note complétée ou
#: remplacée par la personne ne la garde pas, et reste telle quelle.
FORMES_241 = {
    "health.medication": (r"Renouveler : .*",
                          r"[^\n]* \u2014 [^\n]*\nPharmacie : [^\n]*\nRenouvellements restants : \d+",
                          "Healthy Fox : renouvellement à prévoir"),
    "health.lab.test": (r"Analyse à prévoir : .*",
                        r"[^\n]*\nMédecin : [^\n]*\nLabo : [^\n]*",
                        "Healthy Fox : analyse à prévoir"),
    "health.screening": (r"Examen à planifier : .*",
                         r"[^\n]*\nClinique : [^\n]*\nDernier : [^\n]*",
                         "Healthy Fox : examen à planifier"),
    "health.reduction.step": (r"[^\n]+ : .*", None, "Healthy Fox : étape à venir"),
}


def _texte(html):
    texte = re.sub(r"<br\s*/?>", "\n", html or "")
    return re.sub(r"<[^>]+>", "", texte).strip()


def _proprietaires(env):
    """Personnes actives du groupe Healthy Fox, hors compte système : les seules
    à qui une fiche peut revenir (même règle que ``au_nom_du_proprietaire``)."""
    groupe = env.ref("bf_health.group_health_user", raise_if_not_found=False)
    if not groupe:
        return ()
    users = env["res.users"].with_context(active_test=True).search(
        [("groups_id", "in", groupe.id)])
    return tuple(u.id for u in users if not u._is_superuser())


def _notes(cr):
    """Une activité posée par un cron de la 2.4.1 (compte système) reçoit
    le résumé neutre de la 2.5.0, et sa note se réduit au nom de la fiche (la
    2.4.1 y recopiait dosage, pharmacie, médecin, clinique). Chacun seulement
    s'il a encore la forme exacte du cron ; une activité posée à la main par la
    personne (autre `create_uid`) n'est pas lue."""
    resumes = notes = 0
    for modele, table in MODELES_ACTIVITES.items():
        forme_resume, forme_note, neutre = FORMES_241[modele]
        nom_sql = "r.code || ' : ' || r.name" if modele == "health.reduction.step" else "r.name"
        cr.execute(f"""
            SELECT a.id, a.summary, a.note, {nom_sql} FROM mail_activity a
              JOIN {table} r ON r.id = a.res_id
             WHERE a.res_model = %s AND a.create_uid = %s
        """, (modele, SUPERUSER_ID))
        for act_id, resume, note, nom in cr.fetchall():
            if resume and re.fullmatch(forme_resume, resume):
                cr.execute("UPDATE mail_activity SET summary = %s WHERE id = %s", (neutre, act_id))
                resumes += 1
            if forme_note is None:
                # L'étape de réduction n'avait pas de note : son nom y passe.
                if not _texte(note):
                    cr.execute("UPDATE mail_activity SET note = %s WHERE id = %s",
                               (str(Markup("<strong>%s</strong>") % (nom or "")), act_id))
                continue
            if re.fullmatch(forme_note, _texte(note)):
                cr.execute("UPDATE mail_activity SET note = %s WHERE id = %s",
                           (str(Markup("<strong>%s</strong>") % (nom or "")), act_id))
                notes += 1
    return resumes, notes


def _activites(cr, proprietaires):
    total = 0
    for modele, table in MODELES_ACTIVITES.items():
        cr.execute(f"""
            UPDATE mail_activity a
               SET user_id = CASE WHEN a.user_id = %(sys)s THEN r.create_uid ELSE a.user_id END,
                   create_uid = r.create_uid
              FROM {table} r
             WHERE a.res_model = %(modele)s AND a.res_id = r.id
               AND (a.user_id = %(sys)s OR a.create_uid = %(sys)s)
               AND r.create_uid IN %(proprio)s
        """, {"sys": SUPERUSER_ID, "modele": modele, "proprio": proprietaires})
        total += cr.rowcount
    return total


def _prises(cr, proprietaires):
    cr.execute("""
        UPDATE health_medication_log l
           SET create_uid = m.create_uid
          FROM health_medication m
         WHERE l.medication_id = m.id
           AND l.create_uid = %(sys)s
           AND m.create_uid IN %(proprio)s
    """, {"sys": SUPERUSER_ID, "proprio": proprietaires})
    return cr.rowcount


def _gpx(env):
    cr = env.cr
    cr.execute("""
        SELECT id, res_id FROM ir_attachment
         WHERE res_model = 'health.workout' AND res_field = 'gpx_file'
    """)
    lignes = cr.fetchall()
    Workout = env["health.workout"].with_context(tracking_disable=True)
    calcules = 0
    for att_id, res_id in lignes:
        seance = Workout.browse(res_id).exists()
        # 🔴 Relecture adverse du 2026-10-08 : une distance saisie à la main ne
        # dispense pas du dénivelé ni de la durée. On ne remplit que le vide.
        manquants = seance and [c for c in ("distance_km", "elevation_m", "duration_min")
                                if not seance[c]]
        if not manquants:
            continue
        att = env["ir.attachment"].browse(att_id)
        try:
            vals = seance._gpx_valeurs(att.datas)
        except Exception:  # noqa: BLE001 - un fichier illisible est jeté quand même
            _logger.info("bf_health 2.5.0 : GPX illisible sur une séance, jeté")
            continue
        vals = {c: v for c, v in vals.items() if c in manquants}
        if vals:
            seance.write(vals)
            calcules += 1
    if lignes:
        env["ir.attachment"].browse([att_id for att_id, _res in lignes]).unlink()
    cr.execute("""
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'health_workout' AND column_name = 'gpx_filename'
    """)
    if cr.fetchone():
        cr.execute("UPDATE health_workout SET gpx_filename = NULL WHERE gpx_filename IS NOT NULL")
    return len(lignes), calcules


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    proprietaires = _proprietaires(env)
    activites = prises = 0
    # Avant `_activites`, qui change le `create_uid` par lequel on les reconnaît.
    resumes, notes = _notes(cr)
    if proprietaires:
        activites = _activites(cr, proprietaires)
        prises = _prises(cr, proprietaires)
    jetes, calcules = _gpx(env)
    # Les totaux seulement : le journal du serveur ne nomme ni fiche ni personne.
    _logger.info(
        "bf_health 2.5.0 : %s résumé(s) neutre(s) et %s note(s) réduite(s) au nom ; "
        "%s activité(s) et %s prise(s) rendues à leur propriétaire ; %s fichier(s) GPX "
        "jeté(s), dont %s ayant rempli des valeurs",
        resumes, notes, activites, prises, jetes, calcules)
