"""18.0.1.25.0 : « Propulsé par » nomme Symbifox, plus la société servie.

Jusqu'ici, la page publique et les quatre courriels lisaient le nom PUBLIC de
la société servie (`appointment_brand_name`, puis `res.company.name`), et le
certificat d'accès lisait son nom LÉGAL. Résultat :
  - une marque pouvait créditer le nom de domaine d'un tiers ;
  - un client se créditait lui-même ;
  - une même marque disait un nom sur la page et un autre sur le certificat.

Décision : un champ par marque, rempli
« Symbifox » partout, lu par les trois surfaces. `appointment_brand_name` n'est
pas touché : il nomme aussi les pages publiques de prise de rendez-vous.

Cette étape crée la colonne elle-même, AVANT que l'ORM ne la crée et ne la
remplisse en silence avec le défaut du champ : chaque marque touchée est ainsi
écrite au journal, avec ce qu'elle affichait avant. Rien ne bascule sans trace.

Idempotente : seule une valeur vide est remplie ; un nom choisi à la main
est laissé intact.
"""
import logging

_logger = logging.getLogger(__name__)

NOM = "Symbifox"


def _colonne(cr, table, colonne):
    cr.execute(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = %s AND column_name = %s", (table, colonne))
    return bool(cr.fetchone())


def migrate(cr, version):
    if not version:
        return
    cr.execute("ALTER TABLE secure_transfer_brand "
               "ADD COLUMN IF NOT EXISTS powered_by_name varchar")
    # Ce que chaque marque affichait avant, pour le journal seulement.
    # Comme l'ancien `appointment_brand_name or company.name` : seule la
    # chaîne vide retombait sur la société, pas une chaîne d'espaces.
    public = ("NULLIF(c.appointment_brand_name, '')"
              if _colonne(cr, "res_company", "appointment_brand_name") else "NULL")
    cr.execute(f"""
        WITH avant AS (
            SELECT b.id,
                   COALESCE({public}, c.name, b.name) AS page,
                   c.name AS certificat
              FROM secure_transfer_brand b
              LEFT JOIN res_company c ON c.id = b.company_id
             WHERE COALESCE(TRIM(b.powered_by_name), '') = ''
        )
        UPDATE secure_transfer_brand b
           SET powered_by_name = %s
          FROM avant
         WHERE b.id = avant.id
     RETURNING b.id, b.name, b.powered_by, avant.page, avant.certificat
    """, (NOM,))
    lignes = sorted(cr.fetchall())
    for bid, nom, allumee, page, certificat in lignes:
        _logger.info(
            "bf_securetransfer 18.0.1.25.0 : marque %s (%s), mention %s : "
            "page « %s », certificat « %s » -> « %s »",
            bid, nom, "allumée" if allumee else "éteinte",
            page, certificat, NOM)
    _logger.info("bf_securetransfer 18.0.1.25.0 : %s marque(s) créditent "
                 "désormais « %s » (%s allumée(s))", len(lignes), NOM,
                 sum(1 for ligne in lignes if ligne[2]))
