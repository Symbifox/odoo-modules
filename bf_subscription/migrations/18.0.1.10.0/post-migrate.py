"""18.0.1.10.0 : le registre des abonnements ne vient plus avec le statut d'employé.

Jusqu'ici `base.group_user` impliquait `group_subscription_user` : tout employé
interne lisait, créait et modifiait les abonnements (fournisseurs, montants,
cycles) sans qu'aucun droit ne lui soit accordé, et le groupe, sans catégorie,
n'apparaissait dans aucun écran de droits.

🔴 Retirer l'implication ne sort PERSONNE du groupe : Odoo écrit les membres
d'un groupe impliqué dans `res_groups_users_rel` au moment où il les ajoute, et
ne les retire pas quand le lien disparaît. Il faut donc vider le groupe, puis
n'y laisser que ceux que la règle retient.

La règle : garde le registre qui voit déjà les
factures fournisseurs, donc les mêmes montants — les porteurs de
`account.group_account_invoice` — plus les gestionnaires des abonnements (et,
par eux, les administrateurs). Les autres le perdent ; un administrateur le
redonne dans les droits d'accès, catégorie Abonnements.

Les membres se lisent sur la relation (`users`, archivés compris), jamais par
`has_group`, qui sert un cache.
"""
import logging

from odoo import SUPERUSER_ID, Command, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {"active_test": False})
    group_employee = env.ref("base.group_user", raise_if_not_found=False)
    group_sub = env.ref("bf_subscription.group_subscription_user", raise_if_not_found=False)
    group_mgr = env.ref("bf_subscription.group_subscription_manager", raise_if_not_found=False)
    group_invoice = env.ref("account.group_account_invoice", raise_if_not_found=False)
    if not (group_employee and group_sub and group_mgr and group_invoice):
        # Lever plutôt que passer : une migration qui saute en silence laisserait
        # tout le personnel dans le registre en annonçant la version posée.
        raise RuntimeError("bf_subscription 18.0.1.10.0 : groupe introuvable, rien n'est retiré")

    if group_sub in group_employee.implied_ids:
        group_employee.write({"implied_ids": [Command.unlink(group_sub.id)]})
        _logger.info("bf_subscription 18.0.1.10.0 : base.group_user n'implique plus le registre")

    keep = group_mgr.users | group_invoice.users
    # 🔴 Le MODÈLE des nouveaux usagers (`base.default_user`) n'est pas une
    # personne : il porte la facturation, donc la règle le garderait, et
    # `_default_groups()` donnerait le registre à tout usager créé ensuite. Il le
    # perd ; le registre se donne. 
    template = env.ref("base.default_user", raise_if_not_found=False)
    if template:
        keep -= template
    removed = group_sub.users - keep
    if removed:
        group_sub.write({"users": [Command.unlink(user.id) for user in removed]})
    _logger.info(
        "bf_subscription 18.0.1.10.0 : registre retiré à %d compte(s) [%s] ; gardé par %d [%s]",
        len(removed), ", ".join(sorted(removed.mapped("login"))),
        len(group_sub.users), ", ".join(sorted(group_sub.users.mapped("login"))),
    )

    # Ce qui reste branché sur ceux qui viennent de perdre le registre : la
    # migration ne le retire pas (une décision par personne), elle le NOMME.
    # Le récapitulatif les saute désormais de lui-même ; les relances, le fil
    # et les activités, non.
    if removed:
        Sub = env["subscription.subscription"]
        branches = {
            "responsable": Sub.search([("responsible_id", "in", removed.ids)]).responsible_id,
            "abonné au fil": removed.filtered(lambda u: env["mail.followers"].search_count([
                ("res_model", "=", "subscription.subscription"),
                ("partner_id", "=", u.partner_id.id)])),
            "destinataire du récapitulatif": env["subscription.digest"].search([]).user_ids & removed,
            "activité ouverte": env["mail.activity"].with_context(active_test=True).search([
                ("res_model", "=", "subscription.subscription"),
                ("user_id", "in", removed.ids)]).user_id,
        }
        for role, users in branches.items():
            if users:
                _logger.warning(
                    "bf_subscription 18.0.1.10.0 : %s sans le registre : %s",
                    role, ", ".join(sorted(users.mapped("login"))))
