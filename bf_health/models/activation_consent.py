"""Avis d'activation de Healthy Fox et consentement exprès.

Sur une instance où le paramètre système ``bf_health.activation_consent_required``
est vrai (le profil de foyer Symbifox Perso le pose), Healthy Fox est FERMÉ à
chaque personne tant qu'elle n'a pas lu l'avis en vigueur et donné son accord,
à l'écran. Sans le paramètre, rien ne change (BF, PMEC, auto-hébergé).

**Fermé** veut dire, pour cette personne :

* les règles d'accès ajoutent un domaine FAUX à tous les modèles de Healthy Fox,
  sur tous les canaux (écran, XML-RPC, JSON-RPC), administrateurs compris. Les
  modèles compagnons (activités, messages, pièces jointes, abonnés) perdent ce
  qui pointe vers eux : chacun a une porte qui ne passe pas par le document
  (l'activité assignée, le message dont on est l'auteur) ;
* les sous-menus disparaissent, et l'appli mène à l'avis ;
* les tâches planifiées ne travaillent plus pour elle (prises du jour, rappels).

Exception : Blue Fox, administratrice du foyer, garde ce que la règle
``health_dependent_rule_admin`` lui donne déjà (le prénom et la naissance des
enfants qui ont 14 ans, pour ouvrir leur compte), et rien d'autre.

**Le consentement** :

* ne se donne qu'à l'écran, par la personne elle-même : le bouton du client web
  (``/web/dataset/call_button``) sous sa propre session. Jamais en
  superutilisateur, jamais pendant une incarnation (``bf_impersonate``), jamais
  par l'API (XML-RPC, JSON-RPC : Odoo y retire la requête de la pile), ni pour
  une autre personne ;
* vaut pour UNE version de l'avis (``AVIS_ACTIVATION_VERSION``) : une version
  neuve le redemande ;
* se garde comme preuve : date, version, langue et empreinte du texte affiché.
  Seule la personne le lit, administrateurs compris ;
* se retire d'un geste, par tous les canaux (retirer ne peut que refermer). Le
  retrait n'efface rien : l'effacement est une procédure sur demande.

⚠️ Limites, comme pour toute règle d'accès : le superutilisateur (``sudo`` dans
le code, ``odoo shell``, ``/web/become``) n'est pas concerné.
"""
import functools
import hashlib

from psycopg2.errors import UniqueViolation

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.http import request
from odoo.osv import expression
from odoo.tools import sql

#: Le paramètre système qui ferme Healthy Fox jusqu'au consentement.
PARAM_EXIGE = "bf_health.activation_consent_required"
VALEURS_VRAIES = ("1", "true", "yes", "oui", "vrai")

#: La version de l'avis. La changer redemande le consentement à tout le monde.
AVIS_ACTIVATION_VERSION = "2026-10-11"
#: Le gabarit QWeb de l'avis (à surcharger par qui active la garde ailleurs).
GABARIT_AVIS = "bf_health.health_activation_notice_body"

#: Les modèles qui pointent vers un document par (modèle, id), et le champ qui
#: nomme le modèle : la liste du verrou de Gen (``bf_claude_chat``), plus les
#: valeurs suivies des messages, que l'administration système lit.
COMPAGNONS = {
    "mail.activity": "res_model",
    "mail.message": "model",
    "ir.attachment": "res_model",
    "mail.followers": "res_model",
    "mail.tracking.value": "mail_message_id.model",
    # Une activité de type Réunion crée un événement lié à la fiche ; ses
    # participations en portent le nom (Many2one relu en superutilisateur).
    "calendar.event": "res_model",
    "calendar.attendee": "event_id.res_model",
    # Une note programmée sur une fiche (Odoo 18).
    "mail.scheduled.message": "model",
}

#: Les compagnons de calendrier : visibles d'une personne OUVERTE seulement si la
#: personne qui organise le rendez-vous a, elle aussi, Healthy Fox ouvert.
PROPRIETAIRE_CALENDRIER = {
    "calendar.event": "user_id.bf_health_ouvert",
    "calendar.attendee": "event_id.user_id.bf_health_ouvert",
}

#: Les modèles de Healthy Fox qui restent ouverts (sans eux, on ne pourrait pas
#: consentir), chacun réservé à la personne par le champ nommé ici.
CHAMP_PROPRE = {"health.activation.consent": "user_id", "health.activation.notice": "create_uid"}
HORS_GARDE = frozenset(CHAMP_PROPRE)

#: La seule route par laquelle un consentement se donne : LE bouton de l'avis, dans
#: le client web (``/web/dataset/call_button/<modèle>/<méthode>``). Odoo ne lit que
#: le CORPS de la requête pour savoir quoi appeler : on vérifie les deux.
ROUTE_BOUTON = "/web/dataset/call_button/health.activation.notice/action_accept"
APPEL_BOUTON = ("health.activation.notice", "action_accept")
#: Le formulaire de l'avis s'enregistre par ces routes du client web, sous la session.
ROUTE_FORMULAIRE = "/web/dataset/call_kw/health.activation.notice/"
#: La marque de session de ``bf_impersonate`` (une incarnation en cours).
CLE_INCARNATION = "bf_impersonate"
#: Contexte posé par le bouton de l'avis, seul chemin de création d'une preuve.
CLE_ECRAN = "bf_health_consentement_ecran"

_CLE_MEMO = "bf_health_ouverte"
_CLE_ANNULATION = ("bf_health_ouverte_annulation",)


def consentement_exige(env):
    """Vrai si l'instance ferme Healthy Fox jusqu'au consentement."""
    brut = env["ir.config_parameter"].sudo().get_param(PARAM_EXIGE, "") or ""
    return brut.strip().lower() in VALEURS_VRAIES


def modeles_gardes(env):
    """Les modèles de Healthy Fox fermés tant que la personne n'a pas consenti.

    Tous ceux que le module définit (assistants compris : ils portent la santé
    avant l'enregistrement), sauf les deux qui servent à consentir. Calculé une
    fois par registre ; la clé suit le nombre de modèles, qui change quand un
    module s'installe dans le même registre."""
    registre = env.registry
    cle = len(registre.models)
    memo = getattr(registre, "_bf_health_gardes", None)
    if memo is None or memo[0] != cle:
        gardes = frozenset(
            nom for nom, classe in registre.models.items()
            if getattr(classe, "_original_module", None) == "bf_health"
            and not classe._abstract and nom not in HORS_GARDE)
        memo = (cle, gardes)
        registre._bf_health_gardes = memo
    return memo[1]


def _table_prete(env):
    """La table existe-t-elle ? Vrai une fois pour toutes par registre (une
    montée qui crée la table en recrée un)."""
    registre = env.registry
    if getattr(registre, "_bf_health_table_ok", False):
        return True
    if "health.activation.consent" not in registre:
        return False
    ok = sql.table_exists(env.cr, "health_activation_consent")
    if ok:
        registre._bf_health_table_ok = True
    return ok


def _gid_systeme(env):
    return env["ir.model.data"]._xmlid_to_res_id("base.group_system", raise_if_not_found=False) or 0


def personnes_ouvertes(env, uids):
    """Parmi ``uids``, celles qui ont consenti à l'avis en vigueur.

    🔴 Jamais un administrateur système (Blue Fox) : sur un foyer, son compte est
    celui de l'exploitation, pas celui d'une personne, et un administrateur peut se
    donner le groupe santé. Aucune personne du foyer n'est
    administratrice (``bf_household_base`` le refuse).

    En SQL et pas par l'ORM : on est appelé DANS le calcul des règles, et relire
    le consentement par l'ORM y reviendrait. Les écritures en attente (un
    retrait) sont d'abord envoyées à la base."""
    uids = {u for u in uids if u}
    if not uids:
        return set()
    if not _table_prete(env):
        return set()
    env["health.activation.consent"].flush_model(["user_id", "version", "withdrawn_date"])
    env.cr.execute("""
        SELECT DISTINCT user_id FROM health_activation_consent
         WHERE user_id IN %s AND version = %s AND withdrawn_date IS NULL
           AND user_id NOT IN (SELECT uid FROM res_groups_users_rel WHERE gid = %s)
    """, [tuple(uids), AVIS_ACTIVATION_VERSION, _gid_systeme(env)])
    return {r[0] for r in env.cr.fetchall()}


def tous_les_ouverts(env):
    """Toutes les personnes qui ont consenti à l'avis en vigueur (pour les règles
    de partage, qui filtrent sur la personne propriétaire). Mêmes exclusions."""
    if not _table_prete(env):
        return set()
    env["health.activation.consent"].flush_model(["user_id", "version", "withdrawn_date"])
    env.cr.execute("""
        SELECT DISTINCT user_id FROM health_activation_consent
         WHERE version = %s AND withdrawn_date IS NULL
           AND user_id NOT IN (SELECT uid FROM res_groups_users_rel WHERE gid = %s)
    """, [AVIS_ACTIVATION_VERSION, _gid_systeme(env)])
    return {r[0] for r in env.cr.fetchall()}


def _requete_courante():
    try:
        return request._get_current_object() if request else None
    except RuntimeError:
        return None


def ouverte(env, uid=None):
    """Healthy Fox est-il ouvert à cette personne ?

    Vrai sans le paramètre. Mémorisé le temps d'une requête HTTP seulement
    (l'objet requête accompagne la valeur : ``id()`` d'un objet libéré peut
    resservir), et vidé à chaque consentement donné ou retiré. Hors requête
    (tâches planifiées, XML-RPC, essais), relu à chaque appel."""
    if not consentement_exige(env):
        return True
    uid = uid or env.uid
    requete = _requete_courante()
    if requete is None:
        return uid in personnes_ouvertes(env, [uid])
    cle = (_CLE_MEMO, id(requete), uid)
    memo = env.cr.cache
    valeur = memo.get(cle)
    if valeur is None or valeur[0] is not requete:
        valeur = (requete, uid in personnes_ouvertes(env, [uid]))
        memo[cle] = valeur
        if _CLE_ANNULATION not in memo:
            # Une transaction annulée (une route qui rend une erreur) ne laisse
            # pas derrière elle une valeur lue avant l'annulation.
            memo[_CLE_ANNULATION] = True
            env.cr.postrollback.add(functools.partial(_vider_memo, env.cr))
    return valeur[1]


def _vider_memo(cr):
    # 🔴 Lu dans ``__dict__`` et jamais par attribut : à la fermeture d'un vrai
    # curseur (``sql_db.Cursor._close``), ``cache`` et ``_obj`` sont supprimés AVANT
    # l'annulation qui lance ce crochet, et ``cr.cache`` passait alors par
    # ``__getattr__`` → ``_obj`` → récursion infinie : la vraie erreur était masquée
    # et la connexion jamais rendue au pool.
    cache = cr.__dict__.get("cache")
    if not cache:
        return
    for cle in [c for c in cache if isinstance(c, tuple) and c[:1] in ((_CLE_MEMO,), _CLE_ANNULATION)]:
        cache.pop(cle, None)


def oublier(env):
    """Après un consentement donné ou retiré : on relit dans la transaction, et
    les caches qui dépendent de la personne (menus) sont vidés partout."""
    for cle in [c for c in env.cr.cache if isinstance(c, tuple) and c[:1] == (_CLE_MEMO,)]:
        env.cr.cache.pop(cle, None)
    if "bf_health_ouvert" in env["res.users"]._fields:
        env["res.users"].invalidate_model(["bf_health_ouvert"])
    env.registry.clear_cache()


def garder_ouverts(records):
    """Les fiches dont la personne propriétaire a Healthy Fox ouvert.

    Pour les tâches planifiées, qui tournent en superutilisateur : la règle ne
    les touche pas. La propriétaire est l'auteure de la fiche (``create_uid``) ;
    les fiches d'un enfant ont pour auteur le parent qui les tient."""
    env = records.env
    if not records or not consentement_exige(env):
        return records
    proprietaires = {r.id: r.sudo().create_uid.id for r in records}
    ouverts = personnes_ouvertes(env, set(proprietaires.values()))
    return records.filtered(lambda r: proprietaires[r.id] in ouverts)


def domaine_garde(env, nom_modele):
    """Le domaine à AJOUTER aux règles de ``nom_modele``, ou ``None``."""
    if env.su:
        return None
    if nom_modele in CHAMP_PROPRE:
        # La preuve et l'avis : à la personne seule, administrateurs compris, quoi
        # qu'il advienne des règles en données (qu'un administrateur peut couper).
        return [(CHAMP_PROPRE[nom_modele], "=", env.uid)]
    gardes = modeles_gardes(env)
    if nom_modele not in gardes and nom_modele not in COMPAGNONS:
        return None
    if not consentement_exige(env):
        return None
    if nom_modele == "mail.tracking.value":
        # Seule l'administration système y a accès, sans règle ni contrôle du
        # document : fermées à tous sur un foyer, par le message ET par le champ.
        return [("mail_message_id.model", "not in", sorted(gardes)), ("field_id.model", "not in", sorted(gardes))]
    if ouverte(env):
        if nom_modele in PROPRIETAIRE_CALENDRIER:
            # Le rendez-vous lié à la fiche d'une personne fermée ne se lit plus.
            return ["|", (COMPAGNONS[nom_modele], "not in", sorted(gardes)),
                    (PROPRIETAIRE_CALENDRIER[nom_modele], "=", True)]
        return None
    systeme = env.user._is_system()
    if nom_modele in gardes:
        if nom_modele == "health.dependent" and systeme:
            # Blue Fox ouvre le compte de l'ado : ce que sa règle lui donne déjà.
            return [("passage_atteint", "=", True)]
        return list(expression.FALSE_DOMAIN)
    champ = COMPAGNONS[nom_modele]
    ferme = [(champ, "not in", sorted(gardes))]
    passage = env.ref("bf_health.mail_activity_type_passage_14", raise_if_not_found=False)
    if systeme and nom_modele == "mail.activity" and passage:
        # Blue Fox garde SES rappels « Passage à 14 ans » sur une personne à charge
        # (« un compte à ouvrir »), et rien d'autre : ni messages, ni valeurs
        # suivies (prénoms), ni une autre activité qu'on lui aurait assignée.
        return expression.OR([ferme, [(champ, "=", "health.dependent"), ("user_id", "=", env.uid),
                                      ("activity_type_id", "=", passage.id)]])
    return ferme


def _empreinte(texte):
    return hashlib.sha256(str(texte).encode("utf-8")).hexdigest()


def exiger_ecran(env):
    """Refuse (AccessError) si l'appel n'est pas un clic de la personne dans le
    client web, sous sa propre session."""
    if not _a_l_ecran(env):
        raise AccessError(_(
            "Le consentement à Healthy Fox se donne à l'écran, par la personne "
            "elle-même. Il ne peut pas être donné autrement."))


def _a_l_ecran(env):
    return not env.su and _requete_du_bouton(env.uid)


def _requete_du_bouton(uid):
    """Vrai si la requête en cours est le clic sur le bouton de l'avis, sous la
    session de ``uid``, hors incarnation."""
    if not uid or not request:
        return False
    try:
        chemin = request.httprequest.path or ""
    except RuntimeError:
        return False
    if chemin.rstrip("/") != ROUTE_BOUTON:
        return False
    try:
        params = (request.get_json_data() or {}).get("params") or {}
    except Exception:  # noqa: BLE001
        return False
    if (params.get("model"), params.get("method")) != APPEL_BOUTON:
        return False
    return _session_de(uid)


def _session_de(uid):
    session = getattr(request, "session", None)
    if session is None or not session.uid or session.uid != uid:
        return False
    return not session.get(CLE_INCARNATION)


def _formulaire_a_l_ecran(env):
    """Vrai si l'avis s'enregistre depuis son formulaire, dans le client web, sous la
    session de la personne (et non par l'API)."""
    if env.su or not request:
        return False
    try:
        chemin = request.httprequest.path or ""
    except RuntimeError:
        return False
    return (chemin.startswith(ROUTE_FORMULAIRE) or chemin.rstrip("/") == ROUTE_BOUTON) and _session_de(env.uid)


def _canal():
    """Par où passe un retrait : l'écran, l'API (ou du code serveur)."""
    try:
        chemin = request.httprequest.path if request else ""
    except RuntimeError:
        chemin = ""
    return "ecran" if (chemin or "").startswith("/web/dataset/call_button") else "api"


class IrRule(models.Model):
    _inherit = "ir.rule"

    @api.model
    def _compute_domain(self, model_name, mode="read"):
        domaine = super()._compute_domain(model_name, mode)
        garde = domaine_garde(self.env, model_name)
        if garde is not None:
            domaine = expression.AND([domaine, garde]) if domaine else garde
        return domaine


class ResUsers(models.Model):
    _inherit = "res.users"

    #: Lu par les règles de partage (``health_security.xml``), en superutilisateur :
    #: ce qu'une personne a partagé ne se lit plus quand Healthy Fox lui est fermé.
    bf_health_ouvert = fields.Boolean(
        string="Healthy Fox ouvert", compute="_compute_bf_health_ouvert",
        search="_search_bf_health_ouvert")

    @api.depends_context("uid")
    def _compute_bf_health_ouvert(self):
        # 🔴 Relu en superutilisateur par le contrôle d'accès PAR FICHE
        # (``_check_access`` → ``filtered_domain``), qui suit le chemin des règles
        # de partage : la vérité y est due, pour toute personne. Hors
        # superutilisateur, pour soi seulement : dire qui a consenti, c'est dire
        # qui suit sa santé.
        if consentement_exige(self.env):
            ouverts = personnes_ouvertes(self.env, self.ids)
        else:
            ouverts = set(self.ids)
        for user in self:
            vrai = user.id in ouverts
            user.bf_health_ouvert = vrai if self.env.su else (user.id == self.env.uid and vrai)

    def _search_bf_health_ouvert(self, operator, value):
        if not self.env.su:
            raise AccessError(_("Ce champ ne se cherche que dans les règles d'accès."))
        if operator not in ("=", "!=") or not isinstance(value, bool):
            raise UserError(_("Recherche non prise en charge."))
        positif = (operator == "=") == value
        if not consentement_exige(self.env):
            return list(expression.TRUE_DOMAIN if positif else expression.FALSE_DOMAIN)
        return [("id", "in" if positif else "not in", sorted(tous_les_ouverts(self.env)))]


class IrModel(models.Model):
    _inherit = "ir.model"

    @api.depends_context("uid")
    def _compute_count(self):
        """Le nombre d'enregistrements d'un modèle se lit en SQL brut : sur un foyer,
        rien pour les modèles de Healthy Fox, hors superutilisateur."""
        super()._compute_count()
        if self.env.su or not consentement_exige(self.env):
            return
        caches = modeles_gardes(self.env) | HORS_GARDE
        for model in self:
            if model.model in caches:
                model.count = 0


class IrUiMenu(models.Model):
    _inherit = "ir.ui.menu"

    @api.model
    def _load_menus_blacklist(self):
        """Fermé : l'appli reste, ses sous-menus partent (l'appli mène à l'avis).
        ``load_menus`` est mis en cache par personne : ``oublier`` le vide, et
        ``set_param`` vide tous les caches quand l'instance change de régime."""
        noire = super()._load_menus_blacklist()
        if not consentement_exige(self.env):
            # Rien à consentir sur cette instance : « Mon consentement » n'a pas lieu d'être.
            mien = self.env.ref("bf_health.health_menu_activation_consent", raise_if_not_found=False)
            return list(noire) + ([mien.id] if mien else [])
        if ouverte(self.env):
            return noire
        racine = self.env.ref("bf_health.health_menu_root", raise_if_not_found=False)
        if not racine:
            return noire
        dessous = self.with_context(**{"ir.ui.menu.full_list": True}).sudo().search(
            [("id", "child_of", racine.id), ("id", "!=", racine.id)])
        return list(noire) + dessous.ids


class HealthActivationConsent(models.Model):
    _name = "health.activation.consent"
    _description = "Consentement à Healthy Fox"
    _order = "consent_date desc, id desc"
    _rec_name = "version"

    # « restrict » : supprimer la personne n'efface pas sa preuve en silence ;
    # la procédure d'effacement la retire d'abord, exprès.
    user_id = fields.Many2one(
        "res.users", string="Personne", required=True, readonly=True, index=True,
        ondelete="restrict")
    version = fields.Char(string="Version de l'avis", required=True, readonly=True)
    lang = fields.Char(string="Langue de l'avis", readonly=True)
    notice_sha256 = fields.Char(string="Empreinte du texte lu", readonly=True)
    notice_html = fields.Html(string="Texte de l'avis lu", readonly=True, sanitize=False)
    withdrawn_via = fields.Selection(
        [("ecran", "À l'écran"), ("api", "Par l'API")], string="Retiré par", readonly=True)
    consent_date = fields.Datetime(string="Consentement donné le", required=True, readonly=True)
    withdrawn_date = fields.Datetime(string="Retiré le", readonly=True)
    state = fields.Selection(
        [("valide", "En vigueur"), ("retire", "Retiré"), ("remplace", "Avis remplacé")],
        string="État", compute="_compute_state")

    @api.depends("version")
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = _("Consentement à l'avis du %s", rec.version)

    @api.depends("version", "withdrawn_date")
    def _compute_state(self):
        for rec in self:
            if rec.withdrawn_date:
                rec.state = "retire"
            elif rec.version != AVIS_ACTIVATION_VERSION:
                rec.state = "remplace"
            else:
                rec.state = "valide"

    def init(self):
        # Une seule preuve en vigueur par personne et par version : deux onglets
        # qui cliquent ensemble n'en laissent pas une seconde, que le retrait
        # « d'un geste » oublierait.
        self.env.cr.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS health_activation_consent_une_valide
                ON health_activation_consent (user_id, version) WHERE withdrawn_date IS NULL
        """)

    @api.model_create_multi
    def create(self, vals_list):
        # 🔴 La preuve ne naît que du bouton de l'avis : superutilisateur, marque
        # d'écran, ET la requête en cours est le clic de cette personne-là sur ce
        # bouton (ni une action serveur, ni une automatisation, ni une tâche).
        # Aucun droit de création n'est donné à personne. Date du serveur.
        if not (self.env.su and self.env.context.get(CLE_ECRAN)) or not all(
                _requete_du_bouton(vals.get("user_id")) for vals in vals_list):
            raise AccessError(_(
                "Le consentement à Healthy Fox se donne à l'écran, par la personne "
                "elle-même. Il ne peut pas être donné autrement."))
        maintenant = fields.Datetime.now()
        for vals in vals_list:
            vals.update(consent_date=maintenant, withdrawn_date=False, withdrawn_via=False)
        records = super().create(vals_list)
        oublier(self.env)
        return records

    def write(self, vals):
        # Une preuve ne se modifie pas : seul le retrait s'y ajoute, une fois,
        # à l'heure du serveur.
        if set(vals) - {"withdrawn_date", "withdrawn_via"} or not vals.get("withdrawn_date"):
            raise AccessError(_("Un consentement ne se modifie pas. Il se retire."))
        if any(rec.withdrawn_date for rec in self):
            raise AccessError(_("Ce consentement est déjà retiré."))
        vals = dict(vals, withdrawn_date=fields.Datetime.now())
        res = super().write(vals)
        oublier(self.env)
        return res

    def unlink(self):
        # La procédure d'effacement seulement, en superutilisateur.
        if not self.env.su:
            raise AccessError(_("Un consentement ne se supprime pas. Il se retire."))
        res = super().unlink()
        oublier(self.env)
        return res

    def action_withdraw(self):
        """Retirer son consentement : Healthy Fox se referme pour la personne.
        Par tous les canaux (retirer ne peut que refermer), jamais pour autrui."""
        self.check_access("read")
        if not self.env.su and any(rec.user_id.id != self.env.uid for rec in self):
            raise AccessError(_("Seule la personne peut retirer son consentement."))
        # Toutes les preuves en vigueur de la personne : le geste referme, quoi
        # qu'il reste d'un autre onglet.
        a_retirer = self.sudo().search([("user_id", "in", self.user_id.ids), ("withdrawn_date", "=", False)])
        if a_retirer:
            a_retirer.write({"withdrawn_date": fields.Datetime.now(), "withdrawn_via": _canal()})
        return {"type": "ir.actions.client", "tag": "reload"}

    @api.model
    def _bf_action_entree(self):
        """L'entrée de l'appli : le tableau de bord si Healthy Fox est ouvert à la
        personne, l'avis sinon."""
        if ouverte(self.env):
            return self.env["ir.actions.actions"]._for_xml_id("bf_health.health_dashboard_action")
        return self.env["ir.actions.actions"]._for_xml_id("bf_health.action_health_activation_notice")

    @api.model
    def _bf_action_mon_consentement(self):
        """« Mon consentement » : le consentement en vigueur de la personne, à
        retirer d'un geste ; l'avis s'il n'y en a pas."""
        mien = self.search([("user_id", "=", self.env.uid),
                            ("version", "=", AVIS_ACTIVATION_VERSION),
                            ("withdrawn_date", "=", False)], limit=1)
        if not mien:
            return self._bf_action_entree()
        action = self.env["ir.actions.actions"]._for_xml_id(
            "bf_health.action_health_activation_consent_mine")
        action["res_id"] = mien.id
        return action


class HealthActivationNotice(models.TransientModel):
    _name = "health.activation.notice"
    _description = "Avis d'activation de Healthy Fox"

    # 🔴 Champs calculés ET valeur par défaut : au premier appel d'``onchange``
    # (le formulaire neuf du client web), Odoo met FAUX dans chaque champ de la
    # vue qui n'a pas de défaut, et ne recalcule que ce qui dépend d'un champ
    # changé. Sans défaut, l'avis s'ouvrait vide et « rien à consentir » (vu au
    # parcours).
    version = fields.Char(default=lambda self: AVIS_ACTIVATION_VERSION, readonly=True)
    notice_html = fields.Html(string="Avis", compute="_compute_notice_html", sanitize=False,
                              default=lambda self: self._bf_rendu_avis())
    # L'empreinte du texte AFFICHÉ, renvoyée par le formulaire : un avis qui a
    # bougé pendant la lecture (version, traduction) n'est pas accepté.
    notice_sha256 = fields.Char(string="Empreinte du texte affiché", readonly=True,
                                default=lambda self: _empreinte(self._bf_rendu_avis()))
    lu = fields.Boolean(
        string="J'ai lu cet avis. Je consens à ce que Healthy Fox recueille et conserve "
               "mes renseignements de santé aux fins qu'il décrit.")
    avis_change = fields.Boolean(string="Avis changé depuis le dernier accord", compute="_compute_etat",
                                 default=lambda self: self._bf_etat()["avis_change"])
    deja_ouvert = fields.Boolean(string="Déjà ouvert", compute="_compute_etat",
                                 default=lambda self: self._bf_etat()["deja_ouvert"])
    exige = fields.Boolean(string="Consentement exigé sur l'instance", compute="_compute_etat",
                           default=lambda self: self._bf_etat()["exige"])

    def _bf_rendu_avis(self):
        return self.env["ir.qweb"]._render(GABARIT_AVIS, {"version": AVIS_ACTIVATION_VERSION})

    @api.model
    def default_get(self, fields_list):
        # La case n'arrive jamais cochée, quoi qu'en dise le contexte de l'action.
        valeurs = super().default_get(fields_list)
        valeurs.pop("lu", None)
        return valeurs

    @api.model_create_multi
    def create(self, vals_list):
        # Une case cochée ne vient que du formulaire, à l'écran : un avis créé par
        # l'API (Gen, un script) n'arrive jamais coché, même ouvert ensuite par un lien.
        if any(vals.get("lu") for vals in vals_list) and not _formulaire_a_l_ecran(self.env):
            raise AccessError(_("La case de l'avis se coche à l'écran, par la personne elle-même."))
        return super().create(vals_list)

    def write(self, vals):
        if vals.get("lu") and not _formulaire_a_l_ecran(self.env):
            raise AccessError(_("La case de l'avis se coche à l'écran, par la personne elle-même."))
        return super().write(vals)

    @api.depends("version")
    @api.depends_context("lang", "uid")
    def _compute_notice_html(self):
        rendu = self._bf_rendu_avis()
        for rec in self:
            rec.notice_html = rendu

    @api.model
    def _bf_etat(self):
        exige = consentement_exige(self.env)
        ouvert = ouverte(self.env)
        anciens = self.env["health.activation.consent"].search_count([
            ("user_id", "=", self.env.uid), ("version", "!=", AVIS_ACTIVATION_VERSION),
            ("withdrawn_date", "=", False)])
        return {"exige": exige, "deja_ouvert": exige and ouvert,
                "avis_change": exige and not ouvert and bool(anciens)}

    @api.depends("version")
    @api.depends_context("uid")
    def _compute_etat(self):
        etat = self._bf_etat()
        for rec in self:
            rec.update(etat)

    def action_accept(self):
        self.ensure_one()
        exiger_ecran(self.env)
        if self.create_uid.id != self.env.uid:
            raise AccessError(_("Seule la personne peut consentir pour elle-même."))
        if self.version != AVIS_ACTIVATION_VERSION:
            raise UserError(_("L'avis a changé pendant votre lecture. Rouvrez Healthy Fox "
                              "pour lire la nouvelle version."))
        if not self.lu:
            raise UserError(_("Cochez la case pour confirmer que vous avez lu l'avis et "
                              "que vous consentez."))
        if consentement_exige(self.env) and self.env.user._is_system():
            raise UserError(_("Un compte d'administration système ne consent pas à Healthy Fox "
                              "sur cette instance : Healthy Fox lui reste fermé."))
        # La langue de l'écran (celle de la personne, à défaut du contexte), et le
        # texte tel qu'il s'y affiche : il doit être celui qu'elle a lu.
        langue = self.env.lang or self.env.user.lang or "en_US"
        texte = str(self.with_context(lang=langue)._bf_rendu_avis())
        if self.notice_sha256 != _empreinte(texte):
            raise UserError(_("L'avis a changé pendant votre lecture. Rouvrez Healthy Fox "
                              "pour lire la nouvelle version."))
        if consentement_exige(self.env) and not ouverte(self.env):
            try:
                with self.env.cr.savepoint():
                    self.env["health.activation.consent"].sudo().with_context(**{CLE_ECRAN: True}).create({
                        "user_id": self.env.uid,
                        "version": AVIS_ACTIVATION_VERSION,
                        "lang": langue,
                        "notice_sha256": _empreinte(texte),
                        "notice_html": texte,
                        "consent_date": fields.Datetime.now(),
                    })
            except UniqueViolation:
                # Un autre onglet vient de consentir : Healthy Fox est ouvert.
                oublier(self.env)
        # Recharger le client (les menus de l'appli reviennent), puis rouvrir
        # l'entrée de l'appli : le tableau de bord. Le seul menu ramenait à
        # l'accueil de la personne (vu au parcours).
        racine = self.env.ref("bf_health.health_menu_root", raise_if_not_found=False)
        entree = self.env.ref("bf_health.action_health_entree", raise_if_not_found=False)
        params = {}
        if racine:
            params["menu_id"] = racine.id
        if entree:
            params["action_id"] = entree.id
        return {"type": "ir.actions.client", "tag": "reload", "params": params}
