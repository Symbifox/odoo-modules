"""18.0.1.1.0 : les libellés passent d'une source française à une source anglaise.

🔴 Une mise à jour d'Odoo n'écrase JAMAIS une traduction déjà en base. Sur une
base installée avant, le catalogue fr_CA était une identité : il a posé des
valeurs que le nouveau catalogue ne remplace pas de lui-même. On recharge donc
le catalogue de ce module en écrasant.

Les deux messages de maison sont des données `noupdate` : la mise à jour ne
récrit pas leur texte, qui resterait français en en_US, et partirait tel quel
chez les correspondants d'un usager anglophone. On les bascule SEULEMENT s'ils
portent encore, terme pour terme, le texte livré. Un message retouché à la main
est laissé tel quel : c'est précisément pour ça qu'il est `noupdate`.

🔴 Un message retouché en français est effacé par le rechargement du catalogue
que fait Odoo à chaque mise à jour, AVANT cette étape : `post-migrate` en a
copié les valeurs hors en_US, on les remet ici et on efface la copie.

Le texte anglais est lu dans le fichier de données du module, pas recopié ici.

⚠️ En `end` et non en `post` : Odoo charge les traductions du module juste
après le `post`, sans écraser, et c'est ce passage qui doit venir en dernier.
"""

import json
import logging

from lxml import etree

from odoo import SUPERUSER_ID, api
from odoo.tools import SQL, file_open

_logger = logging.getLogger(__name__)

MODULE = "bf_contact_absence_autoreply"
DONNEES = "bf_contact_absence_autoreply/data/bf_absence_house_message_data.xml"
MESSAGES = ("house_message_delay", "house_message_away")

# Les termes livrés en 18.0.1.0.0. Un champ dont TOUS les termes sont ici n'a
# pas été retouché.
TERMES_LIVRES = {
    "Tout le monde",
    "Bonjour,",
    "Bonne journée!",
    "{nom}",
    "Merci pour votre message. Je suis en déplacement jusqu'au {retour} : je "
    "lis mes courriels, mais mes réponses seront plus lentes que d'habitude.",
    "Merci pour votre message. Je suis à l'extérieur du bureau jusqu'au "
    "{retour} et je ne consulte pas mes courriels d'ici là. Je traiterai "
    "votre demande à mon retour.",
    "Si c'est urgent, écrivez à [[releve]] ([[courriel]]), qui prend le relais.",
}


def _termes(champ, valeur):
    if not callable(champ.translate):
        return [valeur.strip()]
    termes = []
    champ.translate(lambda t: termes.append(t) or t, valeur)
    return [" ".join(t.split()) for t in termes if t.strip()]


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    with file_open(DONNEES, "rb") as f:
        arbre = etree.parse(f)
    Maison = env["bf.absence.house.message"].with_context(lang="en_US", active_test=False)
    for xmlid in MESSAGES:
        message = env.ref(f"{MODULE}.{xmlid}", raise_if_not_found=False)
        if not message:
            continue
        message = Maison.browse(message.id)
        valeurs = {}
        for nom in ("name", "body_html", "backup_html"):
            actuel = message[nom] or ""
            termes = _termes(message._fields[nom], actuel)
            if not termes or not set(termes) <= TERMES_LIVRES:
                _logger.info("%s : %s.%s retouché à la main, laissé tel quel", MODULE, xmlid, nom)
                continue
            noeud = arbre.find(f".//record[@id='{xmlid}']/field[@name='{nom}']")
            if noeud is not None and noeud.text:
                valeurs[nom] = noeud.text
        if valeurs:
            message.write(valeurs)
    _recharger_le_catalogue(env)
    _remettre(env)


TABLE = "_bf_absence_autoreply_retouches"


def _remettre(env):
    env.flush_all()
    env.cr.execute("SELECT to_regclass(%s)", (TABLE,))
    if not env.cr.fetchone()[0]:
        return
    env.cr.execute(SQL("SELECT nom_table, colonne, res_id, valeurs FROM %s", SQL.identifier(TABLE)))
    for table, nom, res_id, autres in env.cr.fetchall():
        env.cr.execute(SQL("UPDATE %s SET %s = %s || %s::jsonb WHERE id = %s",
                           SQL.identifier(table), SQL.identifier(nom), SQL.identifier(nom),
                           json.dumps(autres), res_id))
    env.cr.execute(SQL("DROP TABLE %s", SQL.identifier(TABLE)))
    env.invalidate_all()


def _recharger_le_catalogue(env):
    langues = [code for code, _nom in env["res.lang"].get_installed() if code != "en_US"]
    if langues:
        env["ir.module.module"]._load_module_terms([MODULE], langues, overwrite=True)
