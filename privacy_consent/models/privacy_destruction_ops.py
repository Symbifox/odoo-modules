"""La primitive de destruction, en un seul endroit.

🔴 **Le défaut que ce fichier corrige** (tâche BF #24897). Les deux exécuteurs du
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
        Une chaîne courte qui nomme ce qui a été appliqué, pour le journal
        d'exécution de l'appelant.

    Raises:
        UserError: si la méthode affirme la disparition et que l'enregistrement
            est encore là au retour. ⚠ L'appelant DOIT laisser cette exception
            remonter : c'est le seul moyen d'empêcher la certification, les deux
            exécuteurs écrivant au registre après coup sans relire l'état.
    """
    model = record._name
    res_id = record.id

    if method == "anonymize":
        applied = "anonymisation"
        # ⚠ Une PIÈCE JOINTE ne s'anonymise pas. Archiver la pièce laisse les
        # octets dans le filestore, intacts et récupérables, pendant que le
        # certificat annonce « détruit ». On efface donc la charge utile, et on
        # le DIT dans l'étiquette rendue.
        if model == "ir.attachment":
            _clear_attachment_payload(record)
            applied = "anonymisation → effacement de la charge utile"
        if "active" in record._fields:
            record.sudo().write({"active": False})
        if "notes" in record._fields:
            record.sudo().write({"notes": f"[ANONYMISÉ le {fields.Date.today()}]"})
        return applied

    if method == "archive":
        # `archive` ne prétend pas détruire : elle met de côté. C'est le seul
        # mode où « détruit » serait un mensonge assumé, donc on le nomme.
        if "active" in record._fields:
            record.sudo().write({"active": False})
            return "archivage (mise de côté, contenu conservé)"
        return "archivage impossible (le modèle n'a pas de champ « actif »)"

    if method == "manual":
        return "aucune opération automatique (traitement manuel)"

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
    return applied
