"""La primitive de destruction, en un seul endroit.

🔴 **Le défaut que ce fichier corrige.** Les deux exécuteurs du
module — la ligne de campagne et la demande de destruction documentaire —
portaient chacun leur copie de ceci ::

    elif method == "delete":
        if hasattr(record, "active"):
            record.sudo().write({"active": False})
        else:
            record.sudo().unlink()

Tout modèle Odoo qui porte un champ ``active`` — c'est-à-dire la majorité, et
notamment ``res.partner``, ``meeting.record``, ``bf.email``, ``hr.applicant`` —
était donc ARCHIVÉ, pas supprimé. Le contenu restait intégralement en base,
consultable en cochant « Archivé », pendant que l'appelant inscrivait au registre
IMMUABLE une entrée disant « Suppression ». Le registre ne se corrige pas : il
refuse ``write`` et ``unlink``.

Deux copies du même bloc, c'est deux fois l'occasion d'en corriger une seule.
D'où cette fonction unique, que les deux exécuteurs appellent.

⚠ **Ce que la fonction garantit, et ce qu'elle ne garantit pas.** Elle garantit
qu'après un ``delete`` ou un ``secure_wipe`` rendu sans exception, la ligne
n'est plus dans la table. Elle ne dit rien des blocs PostgreSQL sous-jacents, du
journal d'écriture, ni des sauvegardes : une entrée de registre qui affirme la
suppression reste muette sur les dépôts restic, et c'est à l'énoncé de portée de
l'appelant de le dire.

🔴 **Le second défaut, celui que ce lot corrige.** ``anonymize`` ne touchait
que trois choses : la charge utile d'une ``ir.attachment``, le champ ``active``
s'il existait, le champ ``notes`` s'il existait. **Aucun champ identifiant
n'était jamais réécrit.** Relevé sur l'ensemble classifiable réel
de chaque locataire : sur les dizaines de modèles classifiables d'une base réelle, **une majorité ne
subissait rien du tout** (``account.move``, ``mail.message``, ``secure.transfer``,
``sms.archive.message``, les cinq ``bf.ex.*``…), 11 étaient seulement archivés,
dont ``res.partner``, et 7 voyaient leur champ ``notes`` écrasé. L'étiquette
rendue disait « anonymisation » dans tous les cas, l'appelant écrivait
``DÉTRUIT (anonymisation)``, et le registre scellait une anonymisation.

``anonymize`` est donc devenu un **contrat** : le modèle déclare
``_privacy_anonymize()``, qui vide ce qui identifie et rend la liste des champs
partis. Un modèle qui ne le déclare pas fait LEVER la primitive, et l'appelant
consigne le refus au lieu de certifier. refuser, plutôt que retomber en silence sur une suppression.
"""
import logging

from odoo import _, fields
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Méthodes qui affirment que l'enregistrement a disparu. Toute autre laisse la
# ligne debout, et le registre doit le dire autrement.
REMOVAL_METHODS = ("delete", "secure_wipe")

# Méthodes acceptées par les deux exécuteurs. `archive` n'est offerte que par la
# demande de destruction ; la ligne de campagne la refuse, et c'est son affaire.
VALID_METHODS = ("anonymize", "delete", "secure_wipe", "archive", "manual")


class Applied(str):
    """Ce qui a été fait, dans les termes que le registre doit écrire.

    ``label``   : phrase courte pour le journal d'exécution de l'appelant.
    ``scope``   : ``full`` quand l'enregistrement a disparu, ``partial`` quand
                  il survit, amputé de ce que nomme ``fields``.
    ``fields``  : noms techniques des champs vidés, ou description courte du
                  sous-ensemble parti. ⚠ Vide SEULEMENT quand ``removed`` est
                  faux : la garde de ``privacy.destruction.register.create()``
                  refuse une portée partielle qui ne nomme rien.
    ``removed`` : faux quand l'opération n'a RIEN retiré (``archive``,
                  ``manual``). L'appelant ne certifie alors rien : « rien
                  détruit, rien certifié » vaut sur les DEUX exécuteurs, pas
                  seulement sur la demande.

    🔴 **C'est une vraie chaîne, pas un n-uplet nommé.** La version d'avant
    était un ``NamedTuple`` dont la docstring promettait qu'il « se lit comme
    une chaîne » : ``f"{x}"`` et ``str(x)`` rendaient bien l'étiquette, mais
    ``"%s" % x`` la lisait comme un n-uplet d'arguments et
    ``", ".join([..., x])`` levait une ``TypeError``. Aucun appelant en arbre ne
    le faisait, mais le lot 5 (``cq_consent``) et la copie publique partent de
    cette docstring. En héritant de ``str``, les quatre formes de formatage
    rendent l'étiquette, et les attributs restent lisibles.

    ⚠ **Ce qui reste interdit, et ce qui perd les attributs en silence.** Dit
    ici parce que le lot 5 (``cq_consent``) et la copie publique partent de
    cette docstring, et qu'un attribut perdu fait écrire au registre une portée
    de repli, « enregistrement entier », qui affirme une disparition :

    * le **dépaquetage positionnel** (``label, scope, fields = applied``) lit
      les caractères de l'étiquette. Il n'a jamais été promis ;
    * toute opération de ``str`` rend un ``str`` NU, sans attributs :
      concaténation (``applied + " "``), ``.strip()``, ``.format()``,
      ``str(applied)``, et l'écriture dans un champ Odoo
      (``Char.convert_to_cache`` appelle ``str()``). Un ``Applied`` relu depuis
      la base est donc une chaîne ordinaire : relire ``scope`` sur un champ
      stocké lèverait un ``AttributeError``. Les exécuteurs lisent les
      attributs AVANT d'écrire, et stockent chaque membre dans SA colonne ;
    * ``if applied:`` est faux quand l'étiquette est vide, là où un n-uplet
      nommé était toujours vrai. Les appelants testent ``applied.removed``,
      jamais l'objet lui-même ;
    * ``copy``, ``deepcopy`` et ``pickle`` passaient à côté des attributs (le
      protocole de ``str`` ne reconstruit que la chaîne). ``__reduce__`` les
      rend corrects, plutôt que de documenter une limite qu'on peut lever.
    """

    def __new__(cls, label, scope, fields, removed=True):
        objet = super().__new__(cls, label)
        objet.label = label
        objet.scope = scope
        objet.fields = fields
        objet.removed = removed
        return objet

    def __reduce__(self):
        """Reconstruit l'objet AVEC ses attributs pour copy, deepcopy et pickle.

        🔴 Sans lui, le protocole hérité de ``str`` ne reconstruisait que les
        caractères : ``copy.copy(applied)`` rendait un objet ``Applied`` sans
        ``scope`` ni ``removed``, et le premier accès levait un
        ``AttributeError`` loin de la cause. Un ``TypeError`` à la copie serait
        encore pire : il tomberait dans l'``except`` d'un exécuteur et
        passerait pour un échec de destruction.
        """
        return (self.__class__, (self.label, self.scope, self.fields, self.removed))


def _clear_attachment_payload(record):
    """Vide la charge utile d'une pièce jointe avant de la retirer.

    Sur ``ir.attachment``, le renseignement personnel EST le contenu du fichier.
    Le mettre à ``False`` fait décrémenter le compteur de références du
    filestore par Odoo lui-même, ce qu'un simple ``unlink`` ferait aussi — mais
    l'ordre explicite laisse une trace lisible de l'intention et couvre le cas
    d'un fichier partagé par déduplication SHA-1 avec un autre enregistrement.
    """
    record.sudo().write({
        "datas": False,
        "description": f"[EFFACÉ le {fields.Date.today()}]",
    })


def destroy_record(record, method):
    """Applique `method` à `record` et rend l'étiquette de ce qui a été fait.

    Args:
        record: le recordset d'UN enregistrement, déjà contrôlé en droits par
            l'appelant (cette fonction escalade en ``sudo()``).
        method: l'une de ``VALID_METHODS``.

    Returns:
        Un ``Applied`` : l'étiquette pour le journal d'exécution, la portée que
        l'entrée de registre doit déclarer, les champs vidés quand la portée
        est partielle, et ``removed`` qui dit si quelque chose a VRAIMENT été
        retiré. ⚠ C'est une sous-classe de ``str`` : les quatre formes de
        formatage (``f"{x}"``, ``str(x)``, ``"%s" % x``, ``", ".join``) rendent
        l'étiquette, et seul le dépaquetage positionnel est interdit. Un
        appelant qui écrit au registre doit lire ``scope``, ``fields`` et
        ``removed`` : sans eux l'entrée retombe sur « enregistrement entier »,
        qui affirme la disparition (le défaut corrigé ici), ou certifie une
        opération qui n'a rien retiré.

    Raises:
        UserError: si la méthode affirme la disparition et que l'enregistrement
            est encore là au retour ; ou si « anonymisation » est demandée à un
            modèle qui ne déclare pas ``_privacy_anonymize()``, ou dont
            l'anonymiseur refuse ce cas précis. ⚠ L'appelant DOIT laisser ces
            exceptions remonter : c'est le seul moyen d'empêcher la
            certification, les deux exécuteurs écrivant au registre après coup
            sans relire l'état.
    """
    model = record._name
    res_id = record.id

    if method == "anonymize":
        return _anonymize_record(record)

    if method == "archive":
        # `archive` ne prétend pas détruire : elle met de côté. C'est le seul
        # mode où « détruit » serait un mensonge assumé, donc on le nomme. ⚠ Sa
        # portée est « partielle » : l'enregistrement survit, et une entrée de
        # registre en portée « entière » affirmerait qu'il a disparu.
        #
        # 🔴 `fields` reste VIDE et `removed` est faux. La version d'avant
        # rendait ici « aucun champ vidé, mise de côté », une phrase qui
        # satisfaisait la garde « toute portée partielle nomme ce qui est
        # parti » en disant que rien n'était parti : la garde ne contrôle que la
        # présence d'un texte. L'appelant lit maintenant `removed` et ne
        # certifie rien.
        if "active" in record._fields:
            record.sudo().write({"active": False})
            return Applied(
                "archivage (mise de côté, contenu conservé, rien de retiré)",
                "partial", "", removed=False,
            )
        return Applied(
            "archivage impossible (le modèle n'a pas de champ « actif »)",
            "partial", "", removed=False,
        )

    if method == "manual":
        return Applied(
            "aucune opération automatique (traitement manuel)",
            "partial", "", removed=False,
        )

    if method not in REMOVAL_METHODS:
        raise UserError(_("Méthode de destruction invalide : %s", method))

    # === Les deux méthodes qui affirment la disparition ===
    applied = "suppression"
    if model == "ir.attachment":
        _clear_attachment_payload(record)
        applied = "effacement de la charge utile puis suppression"
    elif method == "secure_wipe":
        applied = "suppression"

    record.sudo().unlink()

    # ⚠ Contrôler, plutôt que croire. `unlink` peut être surchargé par un module
    # tiers pour archiver — c'est un patron courant, et c'est littéralement le
    # défaut que cette tâche corrige — et il rendrait alors True sans rien
    # supprimer. La certification qui suit serait fausse, et irréversible.
    # `exists()` interroge les identifiants sans filtre `active`, donc il voit
    # aussi un enregistrement seulement archivé.
    survivor = record.sudo().exists()
    if survivor:
        still_active = getattr(survivor, "active", None)
        _logger.error(
            "privacy_consent: %s,%s existe toujours après unlink() (actif=%s) — "
            "un module surcharge probablement unlink() pour archiver.",
            model, res_id, still_active,
        )
        raise UserError(_(
            "La suppression de « %(model)s »,%(id)s n'a pas eu lieu : "
            "l'enregistrement est encore en base après l'appel. Rien ne sera "
            "inscrit au registre — une certification de destruction est "
            "définitive, et celle-ci serait fausse.",
            model=model, id=res_id,
        ))
    return Applied(applied, "full", "")


def _anonymize_record(record):
    """Demande au modèle de s'anonymiser, et refuse s'il ne sait pas le faire.

    Le contrat du crochet ``_privacy_anonymize()`` :

    * il vide, remplace ou détache ce qui identifie la personne, en ``sudo()``
      si besoin (l'appelant a déjà contrôlé les droits) ;
    * il rend la liste des champs partis — des noms techniques, ou une
      description courte d'un sous-ensemble (« fichiers déposés ») ;
    * il LÈVE une ``UserError`` quand l'anonymisation ne peut pas être honnête
      pour CE enregistrement, par exemple une fiche de contact qui porte des
      pièces comptables ;
    * il ne rend JAMAIS une liste vide en prétendant avoir travaillé : la
      primitive lève, parce qu'une anonymisation qui ne retire rien n'en est pas
      une et que le registre, lui, ne se corrige pas.

    ⚠ Un modèle sans crochet n'est pas un oubli à rattraper par un repli : pour
    ``secure.transfer.file``, ``mail.message`` ou ``sms.archive.message``, le
    renseignement personnel EST le contenu, donc la seule méthode honnête est la
    suppression. Le refus dit cela à l'opérateur, dans le journal d'exécution.
    """
    model = record._name
    anonymiser = getattr(record, "_privacy_anonymize", None)
    if anonymiser is None:
        _logger.warning(
            "privacy_consent: %s,%s — anonymisation refusée, le modèle ne "
            "déclare pas _privacy_anonymize().", model, record.id,
        )
        raise UserError(_(
            "« %(model)s » ne déclare pas comment s'anonymiser. L'anonymisation "
            "ne retirerait rien qui identifie la personne — au mieux elle "
            "archiverait — et le registre attesterait pourtant une "
            "anonymisation. Déclarez « _privacy_anonymize() » sur ce modèle, ou "
            "choisissez une méthode qui dit la vérité : « Suppression », ou "
            "« Archivage », qui ne prétend rien détruire.",
            model=model,
        ))
    champs = anonymiser()
    if isinstance(champs, str):
        champs = [champs]
    champs = [str(c) for c in (champs or []) if c]
    if not champs:
        raise UserError(_(
            "L'anonymisation de « %(model)s »,%(id)s n'a nommé aucun champ "
            "vidé. Rien ne sera inscrit au registre : une anonymisation qui ne "
            "retire rien n'en est pas une.",
            model=model, id=record.id,
        ))
    noms = ", ".join(champs)
    return Applied("anonymisation (%s)" % noms, "partial", noms)


def refresh_res_name(record, *porteurs):
    """Recopie le nom COURANT de l'enregistrement sur ce qui porte `res_name`.

    🔴 **Pourquoi le socle en a besoin.** La ligne de campagne et la
    classification figent `res_name` au moment du CLASSEMENT. Le socle recopie
    ensuite celui de la ligne dans l'entrée de registre, c'est-à-dire dans la
    seule table du module qui refuse `write` et `unlink`. Sur une fiche
    anonymisée, le registre scellait donc le nom qu'on venait d'effacer : le
    nom survivait à son propre effacement, et sans recours.

    Trois ponts faisaient déjà ce geste, chacun pour son modèle
    (`bf_appointment`, `bf_outreach_privacy`, `bf_persona_privacy`) : ils
    remplacent `res_name` par un numéro avant de rendre la main. Le socle le
    fait maintenant pour tous, en relisant le nom APRÈS l'anonymisation. Un
    pont futur dont `_privacy_anonymize()` renomme son enregistrement en hérite
    sans y penser, et sans connaître le registre.

    ⚠ Appelé seulement quand l'enregistrement SURVIT (portée partielle). Après
    une suppression, le nom relevé serait celui d'avant de toute façon, et
    l'entrée de registre doit nommer ce qu'elle atteste avoir détruit : c'est
    aux ponts dont le nom EST le renseignement personnel de le remplacer par un
    numéro, comme ils le font déjà.

    🔴 **Un nom relu VIDE n'est pas une raison de se taire**. La version d'avant rendait la main quand le nom relu était
    vide, donc l'ancien nom restait sur la ligne et partait au registre
    immuable. C'est précisément ce que fait un anonymiseur qui VIDE son champ
    de nom au lieu de le remplacer : le lot 4 prévoit de vider `name` sur
    `bf.outreach.target`. On écrit alors le substitut non identifiant que
    `bf_outreach_privacy` écrit déjà à la main, « <modèle> #<id> ».
    """
    if not record.exists():
        return None
    nom = record.sudo().display_name
    if not nom:
        nom = "%s #%s" % (record._name, record.id)
    for porteur in porteurs:
        if porteur and "res_name" in porteur._fields and porteur.res_name != nom:
            porteur.sudo().write({"res_name": nom})
    return nom
