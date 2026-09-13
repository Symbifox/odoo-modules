# -*- coding: utf-8 -*-
"""Qui relève de qui, sur la fiche de contact.

⛔ `parent_id` n'est pas réutilisé, et ce n'est pas un choix d'esthétique.
Il porte déjà « travaille chez », et le détourner réécrit l'adresse de la
fiche avec celle du parent, en silence, même quand « société » est coché
(mesuré le 2026-09-13). Le lien hiérarchique a donc son propre champ.
"""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from odoo.addons.bf_org_chart.moteur import modele


class ResPartner(models.Model):
    _name = "res.partner"
    _inherit = ["res.partner", "bf.org.chart.source"]

    manager_id = fields.Many2one(
        "res.partner",
        string="Supérieur immédiat",
        index=True,
        ondelete="set null",
        domain="[('is_company', '=', False)]",
        help="La personne dont ce contact relève. Distinct de « Société », "
             "qui dit où il travaille.",
    )
    subordinate_ids = fields.One2many(
        "res.partner", "manager_id", string="Relèvent de ce contact",
    )
    subordinate_count = fields.Integer(
        string="Personnes sous sa responsabilité",
        compute="_compute_subordinate_count",
    )
    org_chart_people_count = fields.Integer(
        string="Personnes dans l'organigramme",
        compute="_compute_org_chart_people_count",
        help="Nombre de personnes que l'organigramme de cette société "
             "dessinerait. Zéro tant que personne n'a de supérieur inscrit.",
    )

    @api.depends("child_ids.manager_id", "manager_id", "subordinate_ids")
    def _compute_org_chart_people_count(self):
        """Ce que le dessin montrerait, compté sans le construire.

        ⚠️ Une fiche de SOCIÉTÉ n'a jamais de supérieur : sans ce compte, le
        bouton se cacherait précisément là où l'organigramme a un sens.
        """
        societes = self.filtered("is_company")
        for fiche in self - societes:
            fiche.org_chart_people_count = (
                1 if (fiche.manager_id or fiche.subordinate_ids) else 0)
        if not societes:
            return
        groupes = self.env["res.partner"]._read_group(
            [("commercial_partner_id", "in", societes.ids),
             ("is_company", "=", False),
             "|", ("manager_id", "!=", False), ("subordinate_ids", "!=", False)],
            ["commercial_partner_id"], ["__count"])
        compte = {societe.id: n for societe, n in groupes}
        for fiche in societes:
            fiche.org_chart_people_count = compte.get(fiche.id, 0)

    @api.depends("subordinate_ids")
    def _compute_subordinate_count(self):
        # Un lot plutôt qu'une lecture par fiche : la liste des contacts
        # affiche cette colonne sur des centaines de lignes.
        groupes = self.env["res.partner"]._read_group(
            [("manager_id", "in", self.ids)], ["manager_id"], ["__count"])
        compte = {sup.id: n for sup, n in groupes}
        for fiche in self:
            fiche.subordinate_count = compte.get(fiche.id, 0)

    @api.constrains("manager_id", "is_company")
    def _check_manager_est_une_personne(self):
        """Une société ne relève de personne, et personne ne relève d'une société.

        🔴 Mesuré le 2026-09-13 : sans cette garde, une société posée en
        « supérieur immédiat » par RPC entrait dans l'organigramme des
        personnes comme un supérieur externe. Le domaine du champ ne garde que
        l'écran; l'import, lui, passe à côté.
        """
        for fiche in self:
            if fiche.manager_id and fiche.manager_id.is_company:
                raise ValidationError(_(
                    "« %(nom)s » est une société : une personne relève de "
                    "quelqu'un, pas d'une entreprise. Le rattachement à une "
                    "entreprise, c'est le champ « Société ».",
                    nom=fiche.manager_id.display_name))
            if fiche.is_company and fiche.manager_id:
                raise ValidationError(_(
                    "Une société n'a pas de supérieur immédiat."))

    @api.constrains("manager_id")
    def _check_manager_recursion(self):
        """Une chaîne hiérarchique qui se referme est une boucle infinie.

        Odoo garde `parent_id` de cette façon depuis toujours; le nouveau champ
        mérite la même garde, sinon le premier organigramme saisi de travers
        fait tourner le dessin en rond.
        """
        # 18.0 : `_check_recursion` est déprécié et lève un avertissement.
        if self._has_cycle("manager_id"):
            raise ValidationError(_(
                "Un contact ne peut pas relever de lui-même, directement ou en "
                "remontant la chaîne."))

    @api.onchange("manager_id")
    def _onchange_manager_id(self):
        """Prévient quand le supérieur travaille ailleurs, sans l'interdire.

        Un directeur de groupe qui chapeaute le responsable TI d'une filiale
        est un cas réel, pas une faute de saisie. On le signale, on ne le
        refuse pas.
        """
        for fiche in self:
            sup = fiche.manager_id
            if sup and fiche.parent_id and sup.commercial_partner_id \
                    and sup.commercial_partner_id != fiche.commercial_partner_id:
                return {"warning": {
                    "title": _("Supérieur d'une autre entreprise"),
                    "message": _(
                        "%(sup)s est rattaché à %(societe)s. C'est permis (les "
                        "groupes fonctionnent comme ça), mais vérifiez que ce "
                        "n'est pas une erreur de saisie.",
                        sup=sup.display_name,
                        societe=sup.commercial_partner_id.display_name),
                }}

    # --- contrat du socle ---------------------------------------------------
    def _org_chart_genres(self):
        genres = super()._org_chart_genres()
        return genres + [{
            "code": "personnes",
            "libelle": _("Organigramme des personnes"),
            "sequence": 10,
        }]

    def _org_chart_racine(self):
        """La société d'où part le dessin, ou la fiche elle-même."""
        self.ensure_one()
        return self.commercial_partner_id or self

    def _org_chart_personnes(self):
        """Les fiches à dessiner : celles de l'entreprise, plus les supérieurs
        externes qu'il faut bien montrer pour que la chaîne tienne debout."""
        self.ensure_one()
        racine = self._org_chart_racine()
        # ⚠️ MÊME portée que `org_chart_people_count`, qui décide de
        # l'affichage du bouton : `child_of` ramassait aussi les contacts des
        # sociétés filles, que le compte, lui, ne voyait pas. Le bouton se
        # cachait alors devant un dessin qui avait du contenu.
        gens = self.search(
            [("commercial_partner_id", "=", racine.id), ("is_company", "=", False)],
            # ⚠️ Borner ICI, pas après : le plafond du socle compte des boîtes
            # déjà construites, donc tout le travail est déjà payé quand il se
            # déclenche. Une société à 20 000 contacts coûtait une seconde de
            # processeur pour finir sur un refus.
            limit=self.PLAFOND_BOITES + 1)
        # `sup not in gens` est un `in` sur un tuple d'identifiants : quadratique.
        dedans = set(gens.ids)
        externes_ids = {f.manager_id.id for f in gens
                        if f.manager_id and f.manager_id.id not in dedans}
        externes = self.browse(sorted(externes_ids))
        # 🔴 `gens` est filtré par les règles d'enregistrement, `manager_id` ne
        # l'est par rien : un supérieur d'une autre société faisait tomber la
        # page sur un AccessError au moment de lire son nom. C'est justement le
        # cas que le module revendique de savoir traiter.
        return gens, externes._filtered_access("read")

    def _org_chart_carte(self, code):
        if code != "personnes":
            return super()._org_chart_carte(code)
        self.ensure_one()
        racine = self._org_chart_racine()
        gens, externes = self._org_chart_personnes()
        carte = modele.Carte(
            titre=racine.display_name or _("Organigramme"),
            sous_titre=_("Organigramme des personnes, relevé du %s",
                         fields.Date.to_string(fields.Date.context_today(self))),
            avertissements=(
                [_("Seules les %s premières personnes sont dessinées.",
                   self.PLAFOND_BOITES)]
                if len(gens) > self.PLAFOND_BOITES else []),
            # ⛔ Pas de marque de la maison ici : le module est distribué, et
            # ce pied-là s'imprimerait au bas de l'organigramme d'un tiers.
            pied=_("Relevé dans Odoo le %s",
                   fields.Date.to_string(fields.Date.context_today(self))),
        )
        for fiche in gens + externes:
            teinte = "neutre"
            if fiche in externes:
                teinte = "ambre"
            elif not fiche.manager_id:
                teinte = "bleu"
            carte.boites.append(modele.Boite(
                cle="p%s" % fiche.id,
                titre=fiche.name or _("Sans nom"),
                sous_titre=fiche.function or "",
                note=(fiche.commercial_partner_id.name or "")
                if fiche in externes else "",
                teinte=teinte,
                lien="/odoo/contacts/%s" % fiche.id,
            ))
        cles = {"p%s" % f.id for f in gens + externes}
        for fiche in gens + externes:
            if fiche.manager_id and "p%s" % fiche.manager_id.id in cles:
                carte.aretes.append(modele.Arete(
                    de="p%s" % fiche.manager_id.id, vers="p%s" % fiche.id))
        carte.legende = [
            ("bleu", _("Sans supérieur inscrit")),
            ("ambre", _("Supérieur d'une autre entreprise")),
        ]
        return carte

    def action_org_chart_personnes(self):
        return self.action_org_chart("personnes")
