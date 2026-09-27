"""Ce qui se pose UNE FOIS, à l'installation, et que le module ne reprend pas.

🔴 **Pourquoi un crochet et pas un fichier de données.** Le but est un défaut
réversible : cacher deux menus d'atelier, mais laisser l'administrateur les
remettre sans que la mise à jour suivante les lui reprenne. Les deux formes XML
échouent, et elles échouent en sens opposés :

- `<data noupdate="0">` écrit à CHAQUE `-u`. L'administrateur qui réactive un
  menu le voit disparaître à la mise à jour suivante, sans comprendre pourquoi.
  C'est confisquer, pas proposer.
- `<data noupdate="1">` sur le xmlid d'un AUTRE module n'écrit **jamais**.
  L'enregistrement existe déjà — il appartient à `maintenance` — et le mode
  « mise à jour » saute les enregistrements marqués `noupdate`. Mesuré :
  `-u` passe au vert, les deux menus restent actifs, et rien ne
  le dit. Le fichier avait l'air juste et ne faisait rien.

Le crochet d'installation, lui, joue une fois et ne rejoue pas : c'est
exactement la sémantique cherchée.
"""
import logging

_logger = logging.getLogger(__name__)

# Deux des 17 menus de `maintenance` viennent de
# l'atelier et non de l'immeuble : ils mesurent la performance d'un équipement
# industriel — disponibilité, cadence, pertes de production. Un syndicat de
# copropriété n'a aucune production à mesurer, et le concierge y voyait deux
# rapports vides.
WORKSHOP_MENUS = (
    "maintenance.menu_m_reports_oee",
    "maintenance.menu_m_reports_losses",
)


def post_init_hook(env):
    """Cache les deux rapports d'atelier, une seule fois.

    ⚠️ **Ce que ça coûte, et il faut le dire** : la désactivation vaut pour TOUT
    ce que le locataire fait avec `maintenance`, pas pour la seule copropriété.
    Le gestionnaire qui s'en sert aussi pour de l'équipement non immobilier perd
    ces deux rapports. C'est assumé parce que c'est le cas rare, et c'est
    réversible parce que le cas rare existe : il les réactive, et rien ne
    viendra les lui reprendre.

    ⚠️ On ne touche qu'à ce qui est ENCORE actif. Un menu déjà désactivé — par
    un autre module, ou par quelqu'un — n'est pas réécrit : le journal dirait
    alors avoir fait un geste qu'il n'a pas fait.
    """
    for xmlid in WORKSHOP_MENUS:
        menu = env.ref(xmlid, raise_if_not_found=False)
        if menu and menu.active:
            menu.active = False
            _logger.info(
                "bf_property_operations : %s masqué (rapport d'atelier, sans "
                "objet sur un immeuble). Réactivable ; le module ne le "
                "reprendra pas.", xmlid,
            )
