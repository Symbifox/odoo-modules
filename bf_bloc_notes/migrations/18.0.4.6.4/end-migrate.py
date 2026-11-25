"""18.0.4.6.4 : quatre textes francais repassent au vouvoiement EN BASE.

Les catalogues 4.6.2 et 4.6.3 avaient corrige ces traductions (l'aide de
l'onglet Partage, l'aide du rappel par defaut, le texte d'attente du contenu,
l'aide de la liste vide), sans toucher leur source anglaise. Or un `-u` GARDE
la traduction d'un terme de vue ou d'aide d'action dont l'anglais n'a pas
change, et le catalogue se charge sans ecraser : chez qui avait monte en 4.6.1,
le tutoiement est reste en base (mesure le 2026-11-24 sur quatre bases sur cinq).

`end` et non `post` : le catalogue du module se charge APRES le `post`. On ne
remplace un terme que s'il porte ENCORE le texte d'avant, mot pour mot ; une
retouche d'operateur reste intacte. Un point de sauvegarde PAR terme : une
erreur SQL avalee laisserait sinon la transaction avortee, et la montee
tomberait plus loin sans dire pourquoi. Ne leve jamais : une migration qui
leve ne se rejoue pas.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

# (identifiant XML, champ traduit par termes, source anglaise, ancien fr_CA, nouveau fr_CA)
TERMES = [
    ('bf_bloc_notes.bf_note_view_form', 'arch_db',
     'Write your note here…',
     'Écris ta note ici…',
     'Écrivez votre note ici…'),
    ('bf_bloc_notes.bf_note_view_form', 'arch_db',
     'A recipient reads the note. With "Can edit" they can also change its title and content, and attach files; the rest stays with you. A recipient also reads the note\'s whole message thread, internal notes and their files included, and can post in it. Sharing itself notifies nobody and subscribes nobody, and a recipient subscribes nobody to this note, themselves included, and schedules no activity on it.',
     "Un destinataire lit la note. Avec « Peut modifier », il peut aussi en changer le titre et le contenu, et y joindre des fichiers ; le reste reste à toi. Un destinataire lit aussi tout le fil de la note, notes internes et fichiers de ces messages compris, et il peut y écrire. Le partage lui-même n'avertit personne et n'abonne personne, et un destinataire n'abonne personne à cette note, lui-même compris, et n'y pose aucune activité.",
     "Un destinataire lit la note. Avec « Peut modifier », il peut aussi en changer le titre et le contenu, et y joindre des fichiers ; le reste vous revient. Un destinataire lit aussi tout le fil de la note, notes internes et fichiers de ces messages compris, et il peut y écrire. Le partage lui-même n'avertit personne et n'abonne personne, et un destinataire n'abonne personne à cette note, lui-même compris, et n'y pose aucune activité."),
    ('bf_bloc_notes.bf_note_user_pref_view_form', 'arch_db',
     'This reminder will be preselected in the quick creation dialog\n                        (systray icon or <kbd>Alt+N</kbd>). You can still change it\n                        when creating.',
     'Ce rappel sera pré-sélectionné dans le dialogue de création rapide\n                        (icône systray ou <kbd>Alt+N</kbd>). Tu peux toujours le changer au\n                        moment de la création.',
     'Ce rappel sera pré-sélectionné dans le dialogue de création rapide\n                        (icône systray ou <kbd>Alt+N</kbd>). Vous pouvez toujours le changer au\n                        moment de la création.'),
    ('bf_bloc_notes.bf_note_action', 'help',
     'Create your first quick note',
     'Crée ta première note rapide',
     'Créez votre première note rapide'),
]


def migrate(cr, version):
    if not version:
        return
    try:
        env = api.Environment(cr, SUPERUSER_ID, {})
        if not env["res.lang"]._lang_get("fr_CA"):
            return
    except Exception:  # noqa: BLE001 - une migration qui leve ne se rejoue pas
        _logger.exception("bf_bloc_notes 4.6.4 : langue fr_CA illisible, rien de fait")
        return
    for xmlid, champ, source, ancien, neuf in TERMES:
        try:
            with cr.savepoint():
                fiche = env.ref(xmlid, raise_if_not_found=False)
                if not fiche:
                    _logger.info("bf_bloc_notes 4.6.4 : %s absent, terme saute", xmlid)
                    continue
                actuel = {t["source"]: t["value"]
                          for t in fiche.get_field_translations(champ, langs=["fr_CA"])[0]}
                if actuel.get(source) == neuf:
                    _logger.info("bf_bloc_notes 4.6.4 : %s, terme deja a jour", xmlid)
                    continue
                if actuel.get(source) != ancien:
                    # Une retouche d'operateur, ou un reste d'une version plus
                    # ancienne qui differe d'un mot : on laisse, mais on le DIT.
                    _logger.warning("bf_bloc_notes 4.6.4 : %s, terme laisse tel quel : %r",
                                    xmlid, (actuel.get(source) or "")[:80])
                    continue
                fiche.update_field_translations(champ, {"fr_CA": {source: neuf}})
                _logger.info("bf_bloc_notes 4.6.4 : %s, un terme fr_CA remis au vouvoiement", xmlid)
        except Exception:  # noqa: BLE001
            _logger.exception("bf_bloc_notes 4.6.4 : %s, terme non remis a jour", xmlid)
