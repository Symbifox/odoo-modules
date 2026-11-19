# -*- coding: utf-8 -*-
"""Conformité des extensions de navigateur : VOIR, jamais empêcher.

La question de départ était « pourrait-on ajouter les extensions Brave / Firefox aux
synchros via Signets ? ». Vérifié contre les API : une extension peut **lire** ce qui est
installé, activer, désactiver, désinstaller — mais ⛔ **installer une extension absente est
impossible** depuis une extension, par conception. C'est la politique du navigateur qui le
fait, et `bf.policy.extension` le fait déjà.

« Synchroniser » ne peut donc pas vouloir dire « poser » : ça existe. Ça veut dire **voir** :
quels postes portent vraiment ce qu'on impose, et si quelqu'un en a désactivé une.

🔴 **Rien n'est jamais bloqué, pour personne.** La politique rendue aux postes ne porte
qu'`ExtensionInstallForcelist` : aucune liste de refus, aucune liste d'autorisation. Un
administrateur — comme n'importe qui — installe ce qu'il veut. C'est tenu par l'essai
`test_la_politique_ne_bloque_jamais_rien`.

🔴 **La portée est vraie par CONSTRUCTION, des deux côtés.** C'est le serveur qui dit quels
identifiants vérifier (`conformite_a_verifier`), et c'est encore le serveur qui **écarte**
au retour tout identifiant qui n'est pas dans le catalogue imposé à cette personne
(`conformite_rapporter`). Une extension modifiée, ou un appel RPC forgé, ne peut donc rien
faire entrer d'autre : la liste complète des extensions de quelqu'un est très parlante
(mots de passe, VPN, santé, politique), et sur un poste client la rapporter serait une
collecte de données personnelles, avec avis et registre.

🔴 **Et ces deux méthodes sont les SEULS écrivains.** Les droits ne donnent à personne
`create` ni `write` sur ces deux modèles : seulement lire les siens, et retirer son propre
profil. C'est ce qui ferme le vrai trou : Odoo ne vérifie une
règle d'écriture que sur l'état d'AVANT et ne la revérifie JAMAIS après (`models.py`,
`check_access("write")` avant le `write`), donc avec un droit d'écriture un employé posait
`user_id` sur un collègue et la ligne sortait de sa portée juste après. Un contrôle d'après
ne se devine pas, il s'écrit — ou on retire le droit.

⚠️ `conformite_a_verifier` laisse tout usager interne lire la liste des extensions imposées
à lui-même. Ce n'est pas un secret — la même liste est dans `brave://policy` sur son poste —
mais c'est une surface de lecture neuve, dite ici plutôt que découverte. Un compte EXTERNE,
lui, n'y a pas accès : les trois méthodes travaillent en `sudo`, donc hors des droits d'accès,
et elles exigent un usager interne en entrée (`_exiger_un_usager_interne`). Sans ce contrôle
un client du portail lisait ce catalogue et se faisait écrire un profil à son nom.
"""
import re

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

# ⚠️ Bornes de ce qui vient du navigateur. Tout ce qui dépasse est écarté ou tronqué :
# ces valeurs traversent un RPC authentifié mais non privilégié.
CLE_RE = re.compile(r"\A[A-Za-z0-9_-]{8,64}\Z")
MAX_ETATS = 200
MAX_TEXTE = 64
# Un identifiant d'extension Chromium fait 32 caractères ; Firefox en met plus, sous forme
# d'adresse courriel ou d'accolades. Large, mais borné.
MAX_IDENTIFIANT = 128

ETATS = [
    ("presente", "Present and enabled"),
    ("desactivee", "Present but disabled"),
    ("absente", "Absent"),
    # 🔴 « Pas rapportée » n'est pas « absente » : un rapport tronqué, un catalogue qui a
    # grandi entre la question et la réponse, ou un navigateur qui n'a pas pu répondre ne
    # disent RIEN de l'extension. Sans cet état, `manquantes = 0` voulait dire « rien de
    # mauvais n'a été rapporté » tout en s'affichant « tout est là ».
    ("inconnu", "Not reported"),
]
# Ce qui demande un regard. « inconnu » n'en fait pas partie : ce n'est pas un défaut,
# c'est une absence de mesure, et elle a son propre compte.
A_REGARDER = ("absente", "desactivee")


def _exiger_un_usager_interne(env):
    """🔴 Rien ici n'est servi a un compte EXTERNE (portail, public).

    Les trois methodes travaillent en `sudo` — elles le doivent, personne n'ayant
    le droit d'ecrire ces modeles — et `sudo` saute les droits d'acces. Sans ce
    controle d'entree, un client du portail lisait le catalogue impose de
    l'organisation (identifiants ET noms) et se faisait ecrire un profil a son
    nom : mesure faite, trois extensions rendues et une ligne creee.

    C'est la garde que le module porte deja sur le chemin de la politique
    (`BfPolicyOrg._user_allowed` : `user.share` refuse dans TOUS les modes) ;
    elle se rejoue ici au lieu de se deviner. `share` est l'exact contraire de
    l'appartenance a `base.group_user`.
    """
    user = env.user
    if not user or user.share:
        raise AccessError(_("Extension conformance is for internal users only."))


def _propre(valeur):
    """Un texte court, sans caractère de contrôle, borné — ou une chaîne vide."""
    if not isinstance(valeur, str):
        return ""
    return "".join(c for c in valeur if c.isprintable())[:MAX_TEXTE].strip()


class BfPolicyBrowserProfile(models.Model):
    _name = "bf.policy.browser.profile"
    _description = "Blue Fox Policy — Reported browser profile"
    _order = "vu_le desc, id desc"
    _rec_name = "navigateur"

    # 🔴 Jamais pris de la charge : c'est TOUJOURS l'appelant. Un profil ne peut donc pas
    # être posé au nom de quelqu'un d'autre, même par un appel forgé.
    user_id = fields.Many2one("res.users", required=True, ondelete="cascade", index=True,
                              default=lambda self: self.env.user)
    # ⚠️ L'extension ne connaît PAS le `machine_uuid` que l'agent BFOS envoie à
    # `bf.policy.machine` : c'est une autre clé. Celle-ci est tirée au hasard par
    # l'extension à sa première passe et gardée dans son stockage local, donc elle
    # identifie un PROFIL de navigateur, pas une machine.
    cle = fields.Char(required=True, index=True, string="Profile key")
    navigateur = fields.Char(string="Browser")
    plateforme = fields.Char(string="Platform")
    vu_le = fields.Datetime(string="Last seen", readonly=True)
    state_ids = fields.One2many("bf.policy.extension.state", "profile_id",
                                string="Extensions")
    # 🔴 STOCKÉS, et c'est le correctif d'un écran qui mentait : la facette « Conformes »
    # était écrite `[('state_ids.etat', 'not in', [...])]`, qu'Odoo traduit par « AU MOINS
    # UNE ligne qui n'est ni absente ni désactivée » — donc un profil à qui il manque une
    # extension s'affichait sous « Conformes » ET sous « À regarder ». Un compte stocké se
    # cherche et se trie, et la facette devient exacte.
    manquantes = fields.Integer(compute="_compute_comptes", store=True,
                                string="To look at",
                                help="Enforced but absent or disabled.")
    inconnues = fields.Integer(compute="_compute_comptes", store=True,
                               string="Unreported",
                               help="Enforced extensions this browser said nothing about.")

    _sql_constraints = [
        ("user_cle_uniq", "unique(user_id, cle)",
         "This browser profile is already reported for this person."),
    ]

    @api.depends("state_ids.etat")
    def _compute_comptes(self):
        for profil in self:
            profil.manquantes = len(
                profil.state_ids.filtered(lambda s: s.etat in A_REGARDER))
            profil.inconnues = len(
                profil.state_ids.filtered(lambda s: s.etat == "inconnu"))

    @api.constrains("cle")
    def _check_cle(self):
        for profil in self:
            if not CLE_RE.fullmatch(profil.cle or ""):
                raise ValidationError(_(
                    "A browser profile key is 8 to 64 characters: letters, digits, "
                    "hyphen and underscore."))

    # ------------------------------------------------------------------ le catalogue
    def _catalogue_impose(self):
        """Les extensions imposées à l'appelant : org + ses ajouts, moins ses retraits.

        Même règle que la politique servie aux postes (`BfPolicyOrg._extensions_for`),
        parce que c'est la MÊME question : ce qui devrait être là.
        """
        Org = self.env["bf.policy.org"].sudo()
        org = Org.search([("company_id", "=", self.env.company.id)], limit=1)
        if not org and Org.search_count([]) == 1:
            # ⚠️ Même repli que `_select_for_host` : avec UNE SEULE organisation, c'est
            # elle qui est servie, quelle que soit la société active. Sans ce repli, une
            # personne dont `env.company` n'est pas celle de la ligne de politique voyait
            # un catalogue VIDE et son écran affirmait « votre organisation n'impose
            # aucune extension » pendant que son poste recevait la liste imposée.
            org = Org.search([], limit=1)
        if not org:
            return self.env["bf.policy.extension"].sudo()
        # 🔴 Sur la societe de l'ORGANISATION, jamais celle de la session : c'est ce
        # que fait `get_policy_json` (`("company_id", "=", self.company_id.id)`).
        # Avec `env.company`, le repli mono-organisation juste au-dessus trouvait un
        # catalogue d'org mais AUCUNE surcharge personnelle : l'ecran verifiait alors
        # une extension que la politique retire a cette personne, et ignorait celle
        # qu'elle lui ajoute — en affirmant « meme regle que la politique ».
        override = self.env["bf.policy.user"].sudo().search(
            [("user_id", "=", self.env.user.id),
             ("company_id", "=", org.company_id.id)], limit=1)
        skip = set(override.extension_remove_ids.ids) if override else set()
        chosen = org.extension_ids | (override.extension_ids if override
                                      else self.env["bf.policy.extension"].sudo())
        return chosen.filtered(lambda e: e.id not in skip)

    @api.model
    def conformite_a_verifier(self):
        """Ce que l'extension a le droit de regarder, et RIEN d'autre.

        🔴 C'est le serveur qui pose la question. L'extension n'a jamais « la liste de tout
        ce qui est installé » à donner : elle n'interroge que ces identifiants-là.
        """
        _exiger_un_usager_interne(self.env)
        catalogue = self._catalogue_impose()
        return {
            "extensions": [{"id": e.extension_id, "nom": e.name}
                           for e in catalogue.sorted(lambda e: (e.name or "", e.id))
                           if e.extension_id],
            "etats": [code for code, _libelle in ETATS],
        }

    # ------------------------------------------------------------------ le rapport
    @api.model
    def conformite_rapporter(self, cle, etats, navigateur=None, plateforme=None):
        """Range l'état des extensions IMPOSÉES pour ce profil de navigateur.

        `etats` : {identifiant d'extension: "presente" | "desactivee" | "absente" |
        "inconnu"}.

        🔴 Tout identifiant hors du catalogue imposé à cette personne est **écarté** et
        n'est jamais écrit : c'est la deuxième moitié de la garantie de portée, celle qui
        tient même si le code de l'extension change d'avis.

        🔴 Et c'est ICI qu'on écrit, en `sudo`, parce que personne n'a le droit d'écrire
        ces modèles directement. `user_id` vient de `self.env.user`, jamais de la charge :
        un appel forgé ne peut donc pas poser un profil au nom d'un collègue.
        """
        _exiger_un_usager_interne(self.env)
        if not CLE_RE.fullmatch(cle or ""):
            raise UserError(_("Invalid profile key."))
        if not isinstance(etats, dict):
            raise UserError(_("A conformance report is a {identifier: state} mapping."))
        catalogue = {e.extension_id: e for e in self._catalogue_impose()
                     if e.extension_id}
        # ⚠️ Le plafond suit le catalogue : écrit en dur à 200, il aurait refusé POUR
        # TOUJOURS le client honnête d'une organisation qui impose plus que ça.
        plafond = max(MAX_ETATS, len(catalogue) + 50)
        if len(etats) > plafond:
            raise UserError(_("Too many entries in a report: %(n)s, maximum %(max)s.",
                              n=len(etats), max=plafond))

        permis = {code for code, _libelle in ETATS}
        retenus, ecartes = {}, 0
        for identifiant, etat in etats.items():
            # ⚠️ La longueur se contrôle AUSSI : le plafond ne comptait que les entrées,
            # donc deux cents clés d'un mégaoctet traversaient le RPC avant d'être
            # écartées. Un identifiant d'extension fait 32 caractères.
            trop_long = (not isinstance(identifiant, str)
                         or len(identifiant) > MAX_IDENTIFIANT)
            ext = None if trop_long else catalogue.get(identifiant)
            if ext is None or etat not in permis:
                ecartes += 1
                continue
            retenus[ext] = etat
        # 🔴 Ce dont le navigateur n'a rien dit n'est pas « absent » : c'est « pas
        # rapporté », et ça se voit.
        for ext in catalogue.values():
            retenus.setdefault(ext, "inconnu")

        Profil = self.sudo()
        profil = Profil.search([("user_id", "=", self.env.user.id), ("cle", "=", cle)],
                               limit=1)
        valeurs = {"vu_le": fields.Datetime.now()}
        nav, plat = _propre(navigateur), _propre(plateforme)
        if nav:
            valeurs["navigateur"] = nav
        if plat:
            valeurs["plateforme"] = plat
        if profil:
            profil.write(valeurs)
        else:
            profil = Profil.create({"user_id": self.env.user.id, "cle": cle, **valeurs})

        Etat = self.env["bf.policy.extension.state"].sudo()
        connus = {e.extension_id: e for e in profil.state_ids}
        for ext, etat in retenus.items():
            ligne = connus.get(ext)
            if ligne:
                if ligne.etat != etat:
                    ligne.etat = etat
            else:
                Etat.create({"profile_id": profil.id, "extension_id": ext.id,
                             "etat": etat})
        # Une extension retiree du catalogue n'a plus d'etat a porter.
        for ext, ligne in connus.items():
            if ext not in retenus:
                ligne.unlink()
        rapportes = sum(1 for e in retenus.values() if e != "inconnu")
        return {"enregistres": rapportes,
                "inconnues": len(retenus) - rapportes,
                "ecartes": ecartes, "profil": profil.id}

    @api.model
    def conformite_oublier(self, cle):
        """Retire ce profil et tout ce qu'il portait. Le geste de retrait, côté personne.

        🔴 Une fonction de vie privée qui s'éteint sans rien effacer laisse son dernier
        instantané en base pour toujours, et la personne n'a aucun écran pour l'enlever
        (le menu est réservé à l'administration). Éteindre la case appelle donc ceci.
        """
        _exiger_un_usager_interne(self.env)
        if not CLE_RE.fullmatch(cle or ""):
            raise UserError(_("Invalid profile key."))
        profil = self.sudo().search([("user_id", "=", self.env.user.id),
                                     ("cle", "=", cle)], limit=1)
        if not profil:
            return {"oublie": 0}
        profil.unlink()
        return {"oublie": 1}


class BfPolicyExtensionState(models.Model):
    _name = "bf.policy.extension.state"
    _description = "Blue Fox Policy — State of an enforced extension on a profile"
    _order = "extension_id, id"
    _rec_name = "extension_id"

    profile_id = fields.Many2one("bf.policy.browser.profile", required=True,
                                 ondelete="cascade", index=True)
    user_id = fields.Many2one(related="profile_id.user_id", store=True, index=True,
                              readonly=True)
    extension_id = fields.Many2one("bf.policy.extension", required=True,
                                   ondelete="cascade")
    # ⚠️ Le libelle s'ecrit : sans lui Odoo le derive du NOM du champ, et un module
    # dont la source est anglaise affichait « Etat » en en-tete de colonne.
    etat = fields.Selection(ETATS, required=True, index=True, string="State")

    _sql_constraints = [
        ("profile_extension_uniq", "unique(profile_id, extension_id)",
         "This extension already has a state on this browser profile."),
    ]
