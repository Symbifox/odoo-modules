"""Purolator : déclaré, configurable, et il refuse de deviner.

Ce module ne contient **aucun appel** à Purolator, et c'est une décision, pas
un oubli. Ce qui a été mesuré le 2026-11-19 :

* ``https://webservices.purolator.com/EWS/V2/Tracking/TrackingService.asmx?wsdl``
  et son jumeau ``devwebservices`` rendent **401** : la WSDL n'est pas
  publique. Aucune forme de message SOAP n'est donc lisible à la source ;
* le portail ``ship.purolator.com/developer`` est une application JS dont les
  propres chaînes (``assets/i18n/en.json``, lues ce soir) décrivent la chaîne
  de clés : **clé de développement** en libre-service après acceptation du
  « Purolator E-Ship Commercial Web Services Developer Agreement » → **clé de
  certification** → **certification automatisée** → **clé de production**. Et
  deux conditions dures, dans leurs mots : « A Purolator Business Account
  number is also required when activating a Production Key » et « The
  Production Key you receive will be **inactive until you invite three
  people** from the business to create a Purolator account » ;
* leur catalogue distingue les « Web Services » (SOAP) d'une ligne **REST**
  (``restapicatalogue``), dont le contenu est derrière la même porte.

🔴 **Donc le connecteur existe, ses champs existent, et l'interrogation refuse
en disant pourquoi.** Écrire une enveloppe SOAP d'après des exemples tiers
aurait donné un module qui a l'air fini et qui ne l'est pas : le pire des deux
mondes pour un transporteur que le client croira branché. Un numéro Purolator
se suit donc à la main, et le colis le dit.

Quand la clé et la WSDL seront en main, l'appel vient ici et rien d'autre ne
change : le reste du module ne sait pas quel transporteur il interroge.
"""
from . import carrier_api as api

HELP = (
    "Purolator ne publie ni sa WSDL ni sa documentation sans compte : la "
    "documentation vit dans le Resource Centre, la clé de développement "
    "s'obtient au portail ship.purolator.com/developer après acceptation de "
    "leur entente, et la clé de production exige un numéro de compte "
    "d'affaires, la certification de l'intégration et trois contacts de "
    "l'entreprise. Aide technique : 1 800 459-5599, option 3."
)


def track(account, number):
    """Refuse, et dit exactement ce qui manque.

    ``permanent`` : rejouer n'y changerait rien, et l'interrogation ne doit pas
    revenir toutes les demi-heures. Le colis reste à suivre à la main.
    """
    raise api.CarrierError(
        "Le suivi Purolator n'est pas branché. " + HELP, permanent=True)
