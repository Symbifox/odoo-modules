"""Partage d'une note a des personnes et a des groupes.

Une LIGNE par destinataire, et non des champs sur la note : le droit d'ecrire se
porte par destinataire, et des many2many auraient oblige a poser ce droit sur la
note, donc le meme pour tout le monde.

🔴 La portee d'une note ne se decide nulle part ailleurs qu'ici.
`_domaine_pour_moi` NOMME les cas, un par un, et les regles d'enregistrement
comme les trois lectures de l'API mobile s'en servent. C'est la lecon de la
fuite du lot du bureau : un domaine vide, « pour laisser la regle decider », rend
TOUTE la base a un compte administrateur, parce que les regles d'un meme modele
se combinent en OU et que ce module en porte une pour `base.group_system`.

⚠️ Les departements RH ne sont pas ici : `hr` n'est pas une dependance de ce
module, qui est publie, et l'ajouter imposerait l'application RH a qui
l'installe. Un pont etend `kind` et `_domaine_pour_moi`, et n'a rien d'autre a
toucher.
"""

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.osv import expression

#: Les sortes de destinataires que ce module sait resoudre lui-meme.
SORTES = [
    ("user", "Person"),
    ("group", "Group"),
    ("channel", "Channel"),
]

#: Le champ qui porte la cible, par sorte. Un pont y ajoute sa ligne.
CHAMP_PAR_SORTE = {
    "user": "user_id",
    "group": "group_id",
    "channel": "channel_id",
}


class BfNoteShare(models.Model):
    _name = "bf.note.share"
    _description = "Note share"
    _order = "note_id, kind, id"

    note_id = fields.Many2one(
        "bf.note", string="Note", required=True, ondelete="cascade", index=True)
    kind = fields.Selection(SORTES, string="Shared with", required=True, default="user")
    user_id = fields.Many2one(
        "res.users", string="Person", ondelete="cascade",
        # Un compte technique n'est pas une personne : `Gen`, les comptes d'API
        # et les comptes de service sont internes et seraient proposes comme
        # n'importe quel collegue.
        #
        # ⚠️ Ce `domain=` ne filtre QUE ce que l'ECRAN propose. L'ORM ne
        # l'applique JAMAIS a un `create` : mesure, parametre pose
        # a ses identifiants, la ligne vers l'assistant se creait par RPC et il lisait la
        # note. Le commentaire d'avant affirmait le contraire (« pour qu'un
        # appel RPC le subisse aussi »), et c'est lui qui a endormi la
        # vigilance jusqu'a la relecture.
        #
        # Ce qui tient la donnee, c'est la contrainte `_check_personne_humaine`,
        # d'ou que vienne l'ecriture ; ce qui ferme les sortes « groupe » et
        # « canal », c'est l'exclusion de la portee dans `_domaine_pour_moi`.
        domain=lambda self: self._domaine_des_personnes())
    group_id = fields.Many2one("res.groups", string="Group", ondelete="cascade")
    channel_id = fields.Many2one("discuss.channel", string="Channel", ondelete="cascade")
    can_write = fields.Boolean(
        string="Can edit", default=False,
        help="The recipient may change the title and the content. Pinning, colour, "
             "tags, sharing, archiving and the Gen flag stay with the author.")
    display_name = fields.Char(compute="_compute_display_name")
    # La regle de lecture des lignes s'appuie sur ce champ : un destinataire ne
    # lit QUE la ligne qui le concerne.
    est_pour_moi = fields.Boolean(
        string="Concerns me", compute="_compute_est_pour_moi",
        search="_search_est_pour_moi", compute_sudo=False)

    # ------------------------------------------------------------------
    # La portee : un seul endroit, et il nomme les cas
    # ------------------------------------------------------------------
    @api.model
    def _domaine_pour_moi(self, en_ecriture=False):
        """Les lignes de partage qui designent l'usager connecte.

        🔴 Chaque sorte a sa branche, et chaque branche nomme SON champ. Rien
        n'est laisse a une regle d'enregistrement : voir l'en-tete du fichier.

        Un pont surcharge cette methode, appelle `super()` et ajoute sa branche
        par `expression.OR`.
        """
        usager = self.env.user
        if usager._bf_note_no_shares():
            # 🔴 Un compte non humain
            # n'entre dans AUCUNE portee, et pas seulement dans la liste des
            # personnes nommables. Les sortes « groupe » et « canal » n'ont
            # aucun domaine, et c'est mesure : un partage a `base.group_user` en
            # ecriture donnait la note a Gen, au compte d'API et au compte
            # client, en ecriture. La contrainte refuse la ligne NOMMEE ; cette
            # branche-ci ferme les deux autres sortes, et toutes celles qu'un
            # pont ajouterait.
            return [("id", "=", 0)]
        branches = [
            [("kind", "=", "user"), ("user_id", "=", usager.id)],
            [("kind", "=", "group"), ("group_id", "in", usager.groups_id.ids)],
            [("kind", "=", "channel"), ("channel_id", "in", self._mes_canaux())],
        ]
        domaine = expression.OR(branches)
        if en_ecriture:
            domaine = expression.AND([domaine, [("can_write", "=", True)]])
        return domaine

    @api.model
    def _mes_canaux(self):
        """Les canaux Discuss dont l'usager est membre.

        ⚠️ En sudo, et ce sudo est borne a l'usager connecte : il ne lit que SES
        propres lignes d'appartenance, et ne rend que des identifiants de canaux
        qu'il voit deja dans Discuss.
        """
        partenaire = self.env.user.partner_id
        if not partenaire:
            return []
        membres = self.env["discuss.channel.member"].sudo().search([
            ("partner_id", "=", partenaire.id),
        ])
        return membres.channel_id.ids

    @api.model
    def _domaine_des_personnes(self):
        """Les comptes que l'on peut nommer : des personnes, pas des machines.

        ⚠️ Sur une base reelle : la plupart des comptes internes actifs
        n'etaient pas des personnes (un assistant, un compte d'API,
        un compte partage). Rien dans la fiche d'un usager ne dit « ceci est un
        robot », et aucun discriminant mesure sur la base ne les separe des
        humains sans faux positif : c'est donc une case de la fiche,
        `bf_note_no_shared_notes`, posee par l'administration.

        Le parametre systeme `bf_bloc_notes.comptes_exclus` d'avant ce
        correctif est garde pour le locataire qui l'a pose, et la migration
        18.0.4.6.0 en fait des cases.
        """
        exclus = self._comptes_exclus()
        domaine = [("share", "=", False), ("active", "=", True),
                   ("bf_note_no_shared_notes", "=", False)]
        if exclus:
            domaine.append(("id", "not in", exclus))
        return domaine

    @api.model
    def _comptes_exclus(self):
        brut = self.env["ir.config_parameter"].sudo().get_param(
            "bf_bloc_notes.comptes_exclus", "")
        ids = []
        for morceau in (brut or "").replace(";", ",").split(","):
            morceau = morceau.strip()
            if morceau.isdigit():
                ids.append(int(morceau))
        return ids

    @api.depends("kind", "user_id", "group_id", "channel_id")
    @api.depends_context("uid")
    def _compute_est_pour_moi(self):
        """Le MEME domaine que la recherche, evalue en Python.

        🔴 Pas une deuxieme ecriture de la regle : `filtered_domain` relit
        exactement `_domaine_pour_moi`. Deux ecritures de la meme portee, c'est
        deux verites qui divergent au premier changement.
        """
        pour_moi = set(self.sudo().filtered_domain(self._domaine_pour_moi()).ids)
        for ligne in self:
            ligne.est_pour_moi = ligne.id in pour_moi

    def _search_est_pour_moi(self, operator, value):
        if operator not in ("=", "!=") or not isinstance(value, bool):
            raise UserError(_("Unsupported search on a sharing flag."))
        positif = (operator == "=") == bool(value)
        domaine = self._domaine_pour_moi()
        return domaine if positif else ["!"] + domaine

    # ------------------------------------------------------------------
    # Affichage
    # ------------------------------------------------------------------
    @api.depends("kind", "user_id", "group_id", "channel_id", "can_write")
    def _compute_display_name(self):
        for ligne in self:
            cible = ligne._cible()
            ligne.display_name = cible.display_name if cible else _("(nobody)")

    def _cible(self):
        """L'enregistrement vise par la ligne, quelle que soit sa sorte."""
        self.ensure_one()
        champ = CHAMP_PAR_SORTE.get(self.kind)
        return self[champ] if champ and champ in self._fields else None

    # ------------------------------------------------------------------
    # Gardes
    # ------------------------------------------------------------------
    @api.constrains("kind", "user_id", "group_id", "channel_id")
    def _check_une_seule_cible(self):
        """La sorte et la cible disent la meme chose, et la cible existe."""
        for ligne in self:
            champ = CHAMP_PAR_SORTE.get(ligne.kind)
            if not champ:
                continue  # une sorte apportee par un pont se garde chez lui
            if not ligne[champ]:
                raise ValidationError(_("Choose who this note is shared with."))
            autres = [
                c for sorte, c in CHAMP_PAR_SORTE.items()
                if c != champ and c in ligne._fields and ligne[c]
            ]
            if autres:
                raise ValidationError(_(
                    "A share line names one recipient, not several."))

    @api.constrains("note_id", "kind", "user_id", "group_id", "channel_id")
    def _check_doublon(self):
        """Deux fois le meme destinataire sur la meme note : refuse.

        ⚠️ En Python et non par un index unique : Postgres tient deux NULL pour
        distincts, donc un index sur (note, sorte, usager, groupe, canal)
        laisserait passer exactement le doublon qu'on veut interdire, les deux
        champs inutilises etant nuls.
        """
        for ligne in self:
            champ = CHAMP_PAR_SORTE.get(ligne.kind)
            if not champ or not ligne[champ]:
                continue
            jumelles = self.sudo().search_count([
                ("id", "!=", ligne.id),
                ("note_id", "=", ligne.note_id.id),
                ("kind", "=", ligne.kind),
                (champ, "=", ligne[champ].id),
            ])
            if jumelles:
                raise ValidationError(_("This note is already shared with %s.",
                                        ligne.display_name))

    @api.constrains("kind", "user_id")
    def _check_personne_humaine(self):
        """🔴 Un compte non humain ne se nomme pas comme personne.

        Mesuré en relecture : un `domain=` de champ
        n'est jamais applique par l'ORM au `create`. Le paramètre pose, la ligne
        vers Gen se creait par RPC et Gen lisait la note : la protection etait
        inerte. Une contrainte, elle, refuse la donnee d'ou qu'elle vienne.

        ⚠️ Aucune exemption de superutilisateur : c'est un invariant de la
        donnee, pas un controle d'acces. Un geste serveur qui partagerait une
        note a un robot se trompe aussi.
        """
        for ligne in self:
            if ligne.kind == "user" and ligne.user_id \
                    and ligne.user_id._bf_note_no_shares():
                raise ValidationError(_(
                    "%s does not receive shared notes: it is a service account, "
                    "not a person.", ligne.user_id.display_name))

    @api.constrains("note_id", "kind", "user_id")
    def _check_pas_a_l_auteur(self):
        """Se partager sa propre note a soi-meme ne veut rien dire."""
        for ligne in self:
            if ligne.kind == "user" and ligne.user_id == ligne.note_id.user_id:
                raise ValidationError(_("A note is already readable by its author."))

    def write(self, vals):
        """🔴 Une ligne de partage ne change pas de note.

        Meme classe de defaut que le lien de note, corrige plus tot : deplacer la ligne
        d'une note vers une autre donnerait acces a une note dont on n'est pas
        l'auteur, puisque la regle d'ecriture ne juge que la note COURANTE.
        """
        if "note_id" in vals and not self.env.su:
            cible = vals["note_id"]
            cible = cible.id if hasattr(cible, "id") else cible
            if any(ligne.note_id.id != cible for ligne in self):
                raise AccessError(_("A share line cannot be moved to another note."))
        return super().write(vals)
