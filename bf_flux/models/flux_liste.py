# -*- coding: utf-8 -*-
"""Une liste de distribution : des sources, des règles, une audience.

Les règles s'appliquent dans cet ordre :

1. une **exclusion** écarte, quoi qu'il y ait d'autre (un appel de résultats
   trimestriels n'apprend rien, même d'un émetteur du bon secteur) ;
2. un **terme du secteur** suffit à retenir ;
3. un **émetteur spécialisé** retient seul : tout ce qu'il diffuse compte ;
4. un **émetteur à deux lignes d'affaires** ne retient jamais seul. Il n'est
   noté que si un terme du secteur est déjà là, pour dire pourquoi l'élément
   compte.

Sans terme ni émetteur spécialisé, la liste prend tout ce que ses sources
apportent, moins les exclusions : la catégorie du flux fait alors le tri.

Une ligne est un mot ou une expression, cherché sans égard à la casse ni aux
accents, en début de mot. Une ligne qui commence par « re: » est une
expression régulière, telle quelle.
"""
import re
import unicodedata
from datetime import timedelta

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError


# Motif d'un élément retenu sans filtre. C'est une donnée stockée et comparée,
# donc une constante, pas une chaîne traduite selon la langue de l'usager.
TOUTE_LA_SOURCE = "toute la source"


def sans_accents(texte):
    return "".join(
        c for c in unicodedata.normalize("NFD", texte or "")
        if unicodedata.category(c) != "Mn")


def compiler(lignes):
    """(ligne d'origine, motif compilé, tel quel) pour chaque ligne non vide.

    Une ligne simple se cherche dans le texte sans accents. Une expression
    régulière se cherche dans le texte tel quel : c'est son auteur qui décide
    des accents, sinon « défense » écrit en expression ne trouverait jamais rien.
    """
    sortie = []
    for brute in (lignes or "").splitlines():
        ligne = brute.strip()
        if not ligne or ligne.startswith("#"):
            continue
        if ligne.lower().startswith("re:"):
            expression = ligne[3:].strip()
            sortie.append((expression, re.compile(expression, re.I), True))
        else:
            motif = re.compile(
                r"(?<!\w)" + re.escape(sans_accents(ligne)), re.I)
            sortie.append((ligne, motif, False))
    return sortie


def trouves(lignes, texte, texte_plat):
    return [l for l, m, tel_quel in compiler(lignes)
            if m.search(texte if tel_quel else texte_plat)]


class FluxListe(models.Model):
    _name = "bf.flux.liste"
    _description = "Liste de distribution de flux"
    _inherit = ["mail.thread"]
    _order = "name"
    _check_company_auto = True

    name = fields.Char("Nom", required=True, tracking=True)
    description = fields.Text("Description")
    active = fields.Boolean(default=True, tracking=True)
    company_id = fields.Many2one(
        "res.company", string="Société", default=lambda s: s.env.company)
    source_ids = fields.Many2many(
        "bf.flux.source", "bf_flux_liste_source_rel", "liste_id", "source_id",
        string="Sources", check_company=True)

    sources_entieres_ids = fields.Many2many(
        "bf.flux.source", "bf_flux_liste_source_entiere_rel", "liste_id",
        "source_id", string="Sources prises en entier",
        help="Parmi les sources de la liste, celles dont la catégorie fait "
             "déjà le tri : tout ce qu'elles apportent est retenu, moins les "
             "exclusions. Les autres passent par les termes et les émetteurs.")

    termes = fields.Text(
        "Termes du secteur",
        help="Un par ligne. Un seul suffit à retenir l'élément.")
    emetteurs_seuls = fields.Text(
        "Émetteurs spécialisés",
        help="Un par ligne. Tout ce qu'ils diffusent est retenu.")
    emetteurs_notes = fields.Text(
        "Émetteurs à deux lignes d'affaires",
        help="Un par ligne. Jamais retenus sur leur seul nom : notés "
             "seulement quand un terme du secteur est déjà présent.")
    exclusions = fields.Text(
        "Exclusions",
        help="Un par ligne. Écarte l'élément quoi qu'il contienne d'autre : "
             "appels de résultats, avis aux actionnaires, etc.")

    department_ids = fields.Many2many(
        "hr.department", string="Services",
        help="Les employés de ces services, sous-services compris, reçoivent la liste.")
    project_ids = fields.Many2many(
        "project.project", string="Projets",
        help="Les abonnés internes de ces projets reçoivent la liste.")
    user_ids = fields.Many2many(
        "res.users", "bf_flux_liste_user_rel", "liste_id", "user_id",
        string="Personnes", domain=[("share", "=", False)],
        help="Des personnes abonnées nommément, en plus des services et des "
             "projets : une veille personnelle, un invité d'un autre service.")
    membre_user_ids = fields.Many2many(
        "res.users", string="Membres", compute="_compute_membres",
        help="Calculés depuis les services et les projets, moins les "
             "personnes qui se sont désabonnées.")
    membre_count = fields.Integer("Nombre de membres", compute="_compute_membres")
    preference_ids = fields.One2many(
        "bf.flux.preference", "liste_id", string="Préférences")

    channel_id = fields.Many2one(
        "discuss.channel", string="Canal Discussion", readonly=True, copy=False,
        ondelete="set null")
    retenue_ids = fields.One2many("bf.flux.retenue", "liste_id", string="Éléments retenus")
    retenue_count = fields.Integer("Retenus", compute="_compute_retenue_count")

    # ------------------------------------------------------------------ règles

    @api.constrains("source_ids", "sources_entieres_ids")
    def _check_sources_entieres(self):
        for liste in self:
            if liste.sources_entieres_ids - liste.source_ids:
                raise ValidationError(_(
                    "Une source prise en entier doit aussi être une source de la liste."))

    @api.constrains("termes", "emetteurs_seuls", "emetteurs_notes", "exclusions")
    def _check_regles(self):
        for liste in self:
            for champ in ("termes", "emetteurs_seuls", "emetteurs_notes", "exclusions"):
                try:
                    compiler(liste[champ])
                except re.error as exc:
                    raise ValidationError(_(
                        "Expression régulière invalide dans « %(champ)s » : %(err)s",
                        champ=liste._fields[champ].string, err=exc)) from exc

    def _flux_motifs(self, element):
        """Motifs qui retiennent l'élément. Liste vide : écarté."""
        self.ensure_one()
        texte = " ".join(filter(None, [
            element.titre, element.resume, element.emetteur, element.sujets]))
        plat = sans_accents(texte)
        if trouves(self.exclusions, texte, plat):
            return []
        if not (self.termes or self.emetteurs_seuls) or (
                element.source_ids & self.sources_entieres_ids):
            return [TOUTE_LA_SOURCE]
        forts = trouves(self.termes, texte, plat)
        seuls = trouves(self.emetteurs_seuls, texte, plat)
        if not (forts or seuls):
            return []
        notes = trouves(self.emetteurs_notes, texte, plat) if forts else []
        return forts + seuls + notes

    def _flux_evaluer(self, elements):
        """Crée les retenues des éléments qui passent les règles de chaque liste."""
        Retenue = self.env["bf.flux.retenue"]
        vals = []
        deja = {
            (r.liste_id.id, r.element_id.id)
            for r in Retenue.search([
                ("liste_id", "in", self.ids), ("element_id", "in", elements.ids)])
        }
        for liste in self:
            for elem in elements:
                if (liste.id, elem.id) in deja:
                    continue
                if not (elem.source_ids & liste.source_ids):
                    continue
                motifs = liste._flux_motifs(elem)
                if motifs:
                    vals.append({
                        "liste_id": liste.id, "element_id": elem.id,
                        "motifs": ", ".join(motifs)[:500],
                    })
        return Retenue.create(vals)

    def action_appliquer_existants(self):
        """Applique les règles aux éléments des sept derniers jours, sans
        rien diffuser : sert à régler une liste sur des cas réels."""
        if not (self.env.su or self.env.user.has_group("bf_flux.group_flux_gestion")):
            raise AccessError(_("Régler une liste est réservé à la gestion des flux."))
        for liste in self:
            elements = self.env["bf.flux.element"].search([
                ("source_ids", "in", liste.source_ids.ids),
                ("date_publication", ">=", fields.Datetime.now() - timedelta(days=7)),
            ])
            retenues = liste._flux_evaluer(elements)
            retenues._flux_juger()
            retenues.write({"diffusee": True, "rattrapage": True})
        return True

    # ---------------------------------------------------------------- membres

    def _flux_membres_calcules(self):
        """Membres avant désabonnement : services et projets."""
        self.ensure_one()
        Users = self.env["res.users"].sudo()
        users = self.user_ids.sudo()
        if self.department_ids:
            employes = self.env["hr.employee"].sudo().search([
                ("department_id", "child_of", self.department_ids.ids),
                ("user_id", "!=", False),
            ])
            users |= employes.user_id
        if self.project_ids:
            partenaires = self.project_ids.sudo().message_partner_ids
            users |= Users.search([
                ("partner_id", "in", partenaires.ids), ("share", "=", False)])
            users |= self.project_ids.sudo().user_id
        return users.filtered(lambda u: u.active and not u.share)

    @api.depends("department_ids", "project_ids", "user_ids", "preference_ids.desabonne")
    def _compute_membres(self):
        for liste in self:
            desabonnes = liste.preference_ids.filtered("desabonne").user_id
            membres = liste._flux_membres_calcules() - desabonnes
            liste.membre_user_ids = membres
            liste.membre_count = len(membres)

    @api.depends("retenue_ids")
    def _compute_retenue_count(self):
        for liste in self:
            liste.retenue_count = len(liste.retenue_ids)

    # ------------------------------------------------------------------ canal

    def _flux_canal(self):
        """Le canal Discuss de la liste, créé au besoin."""
        self.ensure_one()
        if not self.channel_id:
            canal = self.env["discuss.channel"].sudo().create({
                "name": _("Flux · %s", self.name),
                "channel_type": "channel",
                "description": self.description or _(
                    "Éléments retenus par la liste de flux « %s ».", self.name),
                "group_public_id": self.env.ref("base.group_user").id,
            })
            # La création y abonne son auteur : il n'est membre que s'il l'est
            # par un service ou un projet.
            canal.channel_member_ids.unlink()
            self.sudo().channel_id = canal
        return self.channel_id

    def _flux_synchroniser_canal(self):
        """Aligne les membres du canal sur les membres calculés de la liste."""
        # Les membres dépendent des fiches d'employé et des abonnés de projet,
        # que le calcul ne surveille pas : on le refait à chaque alignement.
        self.invalidate_recordset(["membre_user_ids", "membre_count"])
        for liste in self:
            canal = liste._flux_canal().sudo()
            voulus = liste.membre_user_ids.partner_id
            presents = canal.channel_member_ids.partner_id
            a_ajouter = voulus - presents
            if a_ajouter:
                canal.add_members(
                    partner_ids=a_ajouter.ids, post_joined_message=False)
            canal.channel_member_ids.filtered(
                lambda m: m.partner_id and m.partner_id not in voulus).unlink()
        return True

    @api.model
    def _cron_synchroniser(self):
        self.search([("active", "=", True)])._flux_synchroniser_canal()
        return True

    @api.model_create_multi
    def create(self, vals_list):
        listes = super().create(vals_list)
        listes._flux_synchroniser_canal()
        return listes

    def write(self, vals):
        res = super().write(vals)
        if {"department_ids", "project_ids", "user_ids", "active"} & set(vals):
            self.filtered("active")._flux_synchroniser_canal()
        if "name" in vals:
            for liste in self.filtered("channel_id"):
                liste.channel_id.sudo().name = _("Flux · %s", liste.name)
        return res

    # ------------------------------------------------------------- navigation

    def action_voir_retenues(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.name,
            "res_model": "bf.flux.retenue",
            "view_mode": "list,form",
            "domain": [("liste_id", "=", self.id)],
            "context": {"search_default_retenus": 1},
        }

    def action_ouvrir_canal(self):
        self.ensure_one()
        canal = self._flux_canal()
        return {
            "type": "ir.actions.client",
            "tag": "mail.action_discuss",
            "params": {"active_id": f"discuss.channel_{canal.id}"},
        }

    def _flux_carte_html(self, element, motifs):
        """Le message posé au canal pour un élément retenu."""
        resume = (element.resume or "").strip()
        if len(resume) > 400:
            resume = resume[:400].rsplit(" ", 1)[0] + " …"
        lignes = Markup("<p><b><a href='%s' target='_blank'>%s</a></b></p>") % (
            element._flux_url_lire(), element.titre)
        meta = " · ".join(filter(None, [
            element.emetteur,
            fields.Date.to_string(element.date_publication) if element.date_publication else "",
        ]))
        if meta:
            lignes += Markup("<p><i>%s</i></p>") % meta
        if resume:
            lignes += Markup("<p>%s</p>") % resume
        # « toute la source » n'apprend rien : le motif ne se dit que s'il filtre.
        if motifs and motifs != TOUTE_LA_SOURCE:
            lignes += Markup("<p><small>%s</small></p>") % _("Retenu pour : %s", motifs)
        return lignes
