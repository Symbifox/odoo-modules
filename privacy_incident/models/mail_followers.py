"""Les abonnés et les notifications d'un avis ou d'une fiche du registre se lisent dans la
société de la fiche.

Une règle ne peut pas joindre `res_id` à la société de la fiche suivie (ni, pour une notification,
celui de son message) : ces champs non stockés le font par leur recherche, en SQL. Définis à
l'identique dans privacy_breach_notice et privacy_incident (chacun peut être installé seul) ; les
règles globales qui s'en servent ont le même domaine dans les deux.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import SQL

MODELES = ("privacy.breach.notice", "privacy.incident")


def _sous_requete(env, select_par_modele):
    """La sous-requête SQL des ids dont la fiche suivie est dans les sociétés de l'usager.

    Une sous-requête plutôt qu'une liste d'ids : la liste grossissait avec le registre et coûtait
    ses requêtes à chaque recherche (la boîte de réception en fait une à chaque ouverture).
    """
    # Les sociétés cochées, bornées à celles de l'utilisateur : en sudo, le contexte n'est pas vérifié.
    company_ids = tuple((env.companies & env.user.company_ids).ids) or (0,)
    # Un modèle hors du registre n'a ni fiche ni abonné : mail les efface à la désinstallation.
    parts = [select_par_modele(env[model], company_ids) for model in MODELES if model in env]
    return SQL(" UNION ALL ").join(parts) if parts else None


def _domaine(operator, value, sous_requete):
    if operator not in ("=", "!=") or not isinstance(value, bool):
        return None  # l'appelant lève : `_` traduit par l'env de la méthode
    positive = (operator == "=") == value
    if sous_requete is None:
        return [(0, "=", 1)] if positive else [(1, "=", 1)]
    # Les parenthèses : Odoo écrit « id IN <valeur> » tel quel (comme mail_thread_blacklist).
    return [("id", "in" if positive else "not in", SQL("(%s)", sous_requete))]


def _calcul(records, table, sous_requete):
    if sous_requete is None or not records.ids:
        return set()
    # execute_query vide d'abord les champs que la sous-requête déclare (to_flush).
    return {row[0] for row in records.env.execute_query(SQL(
        "SELECT id FROM %s WHERE id IN %s AND id IN (%s)",
        SQL.identifier(table), tuple(records.ids), sous_requete))}


class MailFollowers(models.Model):
    _inherit = "mail.followers"

    privacy_in_company = fields.Boolean(
        compute="_compute_privacy_in_company", search="_search_privacy_in_company",
        string="Fiche Vie privée dans mes sociétés")

    @api.depends_context("uid", "allowed_company_ids")
    def _compute_privacy_in_company(self):
        ids = _calcul(self, "mail_followers", self._privacy_in_company_sql())
        for follower in self:
            follower.privacy_in_company = follower.id in ids

    def _privacy_in_company_sql(self):
        # to_flush : la recherche s'exécute plus tard que le domaine, le SQL doit lire la base à jour.
        fields_ = self.env["mail.followers"]._fields
        return _sous_requete(self.env, lambda model, company_ids: SQL(
            "SELECT f.id FROM mail_followers f JOIN %s r ON r.id = %s "
            "WHERE %s = %s AND %s IN %s",
            SQL.identifier(model._table), SQL.identifier("f", "res_id", to_flush=fields_["res_id"]),
            SQL.identifier("f", "res_model", to_flush=fields_["res_model"]), model._name,
            SQL.identifier("r", "company_id", to_flush=model._fields["company_id"]), company_ids))

    def _search_privacy_in_company(self, operator, value):
        domain = _domaine(operator, value, self._privacy_in_company_sql())
        if domain is None:
            raise UserError(_("Recherche non prise en charge."))
        return domain


class MailNotification(models.Model):
    _inherit = "mail.notification"

    privacy_registre = fields.Boolean(
        compute="_compute_privacy_registre", search="_search_privacy_registre",
        string="Message d'un avis ou du registre")
    privacy_in_company = fields.Boolean(
        compute="_compute_privacy_in_company", search="_search_privacy_in_company",
        string="Fiche Vie privée dans mes sociétés")

    def _compute_privacy_registre(self):
        ids = _calcul(self, "mail_notification", self._privacy_registre_sql())
        for notification in self:
            notification.privacy_registre = notification.id in ids

    def _privacy_registre_sql(self):
        # Le terme `mail_message_id.model` d'une règle devient une sous-requête sur presque tout
        # `mail_message`, placée dans un OU : un balayage de la table à chaque ouverture de fil,
        # pour tout usager. Celle-ci ne retient que les messages du registre, par l'index
        # (model, res_id) de `mail_message`.
        return SQL(
            "SELECT n.id FROM mail_notification n JOIN mail_message m ON m.id = %s WHERE %s IN %s",
            SQL.identifier("n", "mail_message_id",
                           to_flush=self.env["mail.notification"]._fields["mail_message_id"]),
            SQL.identifier("m", "model", to_flush=self.env["mail.message"]._fields["model"]),
            MODELES)

    def _search_privacy_registre(self, operator, value):
        domain = _domaine(operator, value, self._privacy_registre_sql())
        if domain is None:
            raise UserError(_("Recherche non prise en charge."))
        return domain

    @api.depends_context("uid", "allowed_company_ids")
    def _compute_privacy_in_company(self):
        ids = _calcul(self, "mail_notification", self._privacy_in_company_sql())
        for notification in self:
            notification.privacy_in_company = notification.id in ids

    def _privacy_in_company_sql(self):
        # to_flush : la recherche s'exécute plus tard que le domaine, le SQL doit lire la base à jour.
        notification = self.env["mail.notification"]._fields
        message = self.env["mail.message"]._fields
        return _sous_requete(self.env, lambda model, company_ids: SQL(
            "SELECT n.id FROM mail_notification n JOIN mail_message m ON m.id = %s "
            "JOIN %s r ON r.id = %s WHERE %s = %s AND %s IN %s",
            SQL.identifier("n", "mail_message_id", to_flush=notification["mail_message_id"]),
            SQL.identifier(model._table), SQL.identifier("m", "res_id", to_flush=message["res_id"]),
            SQL.identifier("m", "model", to_flush=message["model"]), model._name,
            SQL.identifier("r", "company_id", to_flush=model._fields["company_id"]), company_ids))

    def _search_privacy_in_company(self, operator, value):
        domain = _domaine(operator, value, self._privacy_in_company_sql())
        if domain is None:
            raise UserError(_("Recherche non prise en charge."))
        return domain
