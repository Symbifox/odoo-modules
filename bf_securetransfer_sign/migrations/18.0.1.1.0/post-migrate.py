"""Poser l'exigence d'entente sur les préréglages livrés par le socle.

⚠ Le fichier de données du pont ne suffit PAS ici, et c'est le genre de
défaut qui ne lève rien.

`data/secure_transfer_template_data.xml` écrit `nda_required` sur trois
enregistrements dont les identifiants XML appartiennent à `bf_securetransfer`.
Le bloc est `noupdate` — délibérément : un opérateur qui décoche l'entente ne
doit pas la voir revenir. Mais Odoo saute un enregistrement `noupdate` dont
l'identifiant existe DÉJÀ dès que le module est en mode « mise à jour ». Or
c'est exactement le cas au déploiement : le socle crée les trois préréglages à
son `-u`, puis le `-u` du pont trouve les identifiants en place et n'écrit
rien. Les salles de données sortiraient sans entente, en silence.

Le fichier de données couvre l'INSTALLATION neuve (mode « init », rien à
sauter) ; ce script couvre la MISE À JOUR. Les deux chemins, une seule fois
chacun.

La pose est conditionnelle : seuls les préréglages encore à leur valeur
d'origine sont touchés. Un locataire qui aurait déjà réglé la question garde
son choix.
"""
import logging

_logger = logging.getLogger(__name__)

_XMLIDS = (
    "bf_securetransfer.template_dataroom_standard",
    "bf_securetransfer.template_dataroom_wide",
    "bf_securetransfer.template_dataroom_restricted",
)


def migrate(cr, version):
    if not version:
        # Installation neuve : le fichier de données a déjà fait le travail.
        return
    cr.execute("""
        SELECT res_id FROM ir_model_data
         WHERE model = 'secure.transfer.template'
           AND module || '.' || name = ANY(%s)
    """, (list(_XMLIDS),))
    ids = [row[0] for row in cr.fetchall()]
    if not ids:
        _logger.info(
            "bf_securetransfer_sign: aucun préréglage livré trouvé — rien à poser.")
        return
    # `IS NOT TRUE` couvre aussi la colonne fraîchement créée, encore NULL sur
    # les lignes existantes : un simple `= false` les manquerait toutes.
    cr.execute("""
        UPDATE secure_transfer_template
           SET nda_required = true
         WHERE id = ANY(%s)
           AND nda_required IS NOT TRUE
    """, (ids,))
    _logger.info(
        "bf_securetransfer_sign: entente exigée sur %s préréglage(s) de salle "
        "de données (sur %s livrés).", cr.rowcount, len(ids))
