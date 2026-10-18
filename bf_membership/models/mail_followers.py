"""Les abonnés et les notifications de l'association se lisent dans la société de la fiche.

Les règles GLOBALES de `security/membership_security.xml` réservent au rôle Membres les abonnés
et les notifications des modèles de l'association (`bf.membership*`) ; une personne lit toujours
les siens. Globales, parce qu'Odoo combine par OU les règles de groupe d'un même modèle : la
règle de groupe de n'importe quel module voisin sur `mail.followers` rouvrait ce que la nôtre
fermait.

Une règle ne peut pas joindre `res_id` à la société de la fiche suivie : ces deux champs non
stockés le font par leur recherche, en SQL, et bornent le rôle à ses sociétés. Un modèle de
l'association sans société stockée (le délégué) n'a pas de société à vérifier : ses abonnés
restent lisibles par le rôle.
"""
from odoo import _, api, fields, models, tools
from odoo.exceptions import UserError
from odoo.tools import SQL

# Le même motif que le `like` des règles : `bf.membership`, `bf.membership.type`, les greffons.
MOTIF = "bf.membership"


def _modeles_avec_societe(env):
    """Les noms des modèles de l'association dont la fiche porte une société stockée."""
    names = []
    for name in list(env.registry):
        if MOTIF not in name:
            continue
        model = env[name]
        field = model._fields.get("company_id")
        if model._abstract or not model._auto or not field or not field.store or field.type != "many2one":
            continue
        env.cr.execute(SQL("SELECT to_regclass(%s)", model._table))
        if env.cr.fetchone()[0]:
            names.append(name)
    return tuple(names)


def _modeles_avec_societe_en_cache(env):
    # Le registre ne change qu'au chargement d'un module : il est alors reconstruit, ses caches
    # vidés. Pendant le chargement (essais at_install compris), une table peut encore manquer :
    # on recalcule sans rien garder.
    if not env.registry.ready:
        return _modeles_avec_societe(env)
    return env["mail.followers"]._bf_membership_models_with_company()


def _hors_societe(env, sql_par_modele):
    """La sous-requête des ids dont la fiche de l'association est dans une société hors de portée.

    Une sous-requête plutôt qu'une liste d'ids : la liste grossissait avec les fiches des autres
    sociétés et revenait dans chaque recherche (la boîte de réception en fait une à chaque
    ouverture).
    """
    # Les sociétés cochées, bornées à celles de l'utilisateur : en sudo, le contexte n'est pas vérifié.
    societes = tuple((env.companies & env.user.company_ids).ids) or (0,)
    parts = [sql_par_modele(env[name], societes) for name in _modeles_avec_societe_en_cache(env)]
    return SQL(" UNION ALL ").join(parts) if parts else None


def _domaine(operator, value, sous_requete):
    if operator not in ("=", "!=") or not isinstance(value, bool):
        return None
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

    membership_out_of_company = fields.Boolean(
        compute="_compute_membership_out_of_company", search="_search_membership_out_of_company",
        string="Fiche de l'association hors de mes sociétés")

    @api.model
    @tools.ormcache()
    def _bf_membership_models_with_company(self):
        # Un tuple de noms, pas de recordset : le cache du registre survit aux environnements.
        return _modeles_avec_societe(self.env)

    @api.depends_context("uid", "allowed_company_ids")
    def _compute_membership_out_of_company(self):
        ids = _calcul(self, "mail_followers", self._membership_out_of_company_sql())
        for follower in self:
            follower.membership_out_of_company = follower.id in ids

    def _membership_out_of_company_sql(self):
        # to_flush : la recherche s'exécute plus tard que le domaine, le SQL doit lire la base à jour.
        fields_ = self.env["mail.followers"]._fields
        return _hors_societe(self.env, lambda model, societes: SQL(
            "SELECT f.id FROM mail_followers f JOIN %s r ON r.id = %s "
            "WHERE %s = %s AND %s NOT IN %s",
            SQL.identifier(model._table), SQL.identifier("f", "res_id", to_flush=fields_["res_id"]),
            SQL.identifier("f", "res_model", to_flush=fields_["res_model"]), model._name,
            SQL.identifier("r", "company_id", to_flush=model._fields["company_id"]), societes))

    def _search_membership_out_of_company(self, operator, value):
        domain = _domaine(operator, value, self._membership_out_of_company_sql())
        if domain is None:
            raise UserError(_("Recherche non prise en charge."))
        return domain


class MailNotification(models.Model):
    _inherit = "mail.notification"

    membership_registre = fields.Boolean(
        compute="_compute_membership_registre", search="_search_membership_registre",
        string="Message d'une fiche de l'association")
    membership_out_of_company = fields.Boolean(
        compute="_compute_membership_out_of_company", search="_search_membership_out_of_company",
        string="Fiche de l'association hors de mes sociétés")

    def _compute_membership_registre(self):
        ids = _calcul(self, "mail_notification", self._membership_registre_sql())
        for notification in self:
            notification.membership_registre = notification.id in ids

    def _membership_registre_sql(self):
        # Le terme `mail_message_id.model` d'une règle devient une sous-requête sur presque tout
        # `mail_message`, placée dans un OU : un balayage de la table à chaque ouverture de fil,
        # pour tout usager. Celle-ci ne retient que les messages de l'association, par l'index
        # (model, res_id) de `mail_message`.
        names = tuple(name for name in self.env.registry if MOTIF in name)
        return SQL(
            "SELECT n.id FROM mail_notification n JOIN mail_message m ON m.id = %s WHERE %s IN %s",
            SQL.identifier("n", "mail_message_id",
                           to_flush=self.env["mail.notification"]._fields["mail_message_id"]),
            SQL.identifier("m", "model", to_flush=self.env["mail.message"]._fields["model"]),
            names) if names else None

    def _search_membership_registre(self, operator, value):
        domain = _domaine(operator, value, self._membership_registre_sql())
        if domain is None:
            raise UserError(_("Recherche non prise en charge."))
        return domain

    @api.depends_context("uid", "allowed_company_ids")
    def _compute_membership_out_of_company(self):
        ids = _calcul(self, "mail_notification", self._membership_out_of_company_sql())
        for notification in self:
            notification.membership_out_of_company = notification.id in ids

    def _membership_out_of_company_sql(self):
        # to_flush : la recherche s'exécute plus tard que le domaine, le SQL doit lire la base à jour.
        notification = self.env["mail.notification"]._fields
        message = self.env["mail.message"]._fields
        return _hors_societe(self.env, lambda model, societes: SQL(
            "SELECT n.id FROM mail_notification n JOIN mail_message m ON m.id = %s "
            "JOIN %s r ON r.id = %s WHERE %s = %s AND %s NOT IN %s",
            SQL.identifier("n", "mail_message_id", to_flush=notification["mail_message_id"]),
            SQL.identifier(model._table), SQL.identifier("m", "res_id", to_flush=message["res_id"]),
            SQL.identifier("m", "model", to_flush=message["model"]), model._name,
            SQL.identifier("r", "company_id", to_flush=model._fields["company_id"]), societes))

    def _search_membership_out_of_company(self, operator, value):
        domain = _domaine(operator, value, self._membership_out_of_company_sql())
        if domain is None:
            raise UserError(_("Recherche non prise en charge."))
        return domain
