"""Garde commune : une fiche personnelle ne change pas de propriétaire par RPC.

Isolation par personne. Odoo 18 contrôle les
règles d'enregistrement d'un `write` AVANT d'écrire, jamais après : une personne
peut donc poser `user_id = <quelqu'un d'autre>` sur SA propre fiche, et la
fiche passe chez l'autre. Pour une règle de tri, c'est le courrier de l'autre
qui se détourne ; pour un message d'absence, sa boîte qui répond un texte
choisi par un tiers ; pour un compte IMAP, une boîte étrangère branchée chez lui.

Le superutilisateur (crons, semis, contrôleurs en sudo), l'administrateur
technique (`base.group_system`) et l'administrateur courriel
(`group_email_admin`) gardent le geste.
"""
from odoo import _
from odoo.exceptions import AccessError


def garder_proprietaire(records, vals, champ="user_id"):
    if records.env.su or champ not in vals:
        return
    cible = vals[champ]
    if hasattr(cible, "id"):
        cible = cible.id
    if not cible or cible == records.env.uid:
        return
    # L'administrateur courriel garde le geste : il réassigne une règle sans
    # être administrateur système, et la garde ne doit pas le lui retirer.
    if records.env.user.has_group("base.group_system") or records.env.user.has_group(
            "bf_email_management.group_email_admin"):
        return
    raise AccessError(_(
        "Cette fiche est personnelle : elle ne se donne pas à quelqu'un d'autre."))


def garder_parent(records, vals, champ, modele):
    """Même garde, pour une fiche qui APPARTIENT à une fiche parente
    personnelle (réponse d'absence -> absence, condition -> règle ou réponse).

    Odoo contrôle les règles d'un `write` avant d'écrire, pas après : la
    règle ne voit pas la fiche parente d'arrivée. On exige donc le droit
    d'ÉCRIRE la fiche parente visée. Superutilisateur : inchangé."""
    if records.env.su or not vals.get(champ):
        return
    cible = vals[champ]
    if hasattr(cible, "id"):
        cible = cible.id
    records.env[modele].browse(cible).check_access("write")
