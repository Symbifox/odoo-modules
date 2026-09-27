# -*- coding: utf-8 -*-
"""Un élément de flux : un article, un communiqué, un billet.

Un élément n'entre qu'une fois, par sa clé. Le même communiqué reçu par deux
flux est un seul élément rattaché aux deux sources.
"""
import logging
import re

from lxml import html as lhtml

from odoo import _, api, fields, models
from odoo.modules import module as odoo_module

from .flux_source import FluxErreur

_logger = logging.getLogger(__name__)

# Ce qui n'est jamais le corps d'un article.
_BRUIT = ("script", "style", "noscript", "nav", "header", "footer", "aside",
          "form", "iframe", "svg", "button")
# Conteneurs du corps, du plus précis au plus large.
_CORPS = ("//*[@itemprop='articleBody']", "//article", "//main",
          "//*[@role='main']", "//body")


def texte_de_page(brut):
    """Texte lisible d'une page HTML, paragraphes conservés."""
    if isinstance(brut, bytes):
        # Sans charset déclaré, lxml suppose le latin-1 : « DÃ©fense ». On
        # tente l'UTF-8 strict d'abord, et on laisse lxml deviner sinon.
        try:
            brut = brut.decode("utf-8")
        except UnicodeDecodeError:
            pass
    try:
        doc = lhtml.document_fromstring(brut)
    except (ValueError, lhtml.etree.ParserError):
        return ""
    for tag in _BRUIT:
        for n in doc.iter(tag):
            n.drop_tree()
    corps = None
    for xp in _CORPS:
        trouves = doc.xpath(xp)
        if trouves:
            corps = max(trouves, key=lambda n: len(n.text_content()))
            break
    if corps is None:
        return ""
    for n in corps.iter("br"):
        n.tail = "\n" + (n.tail or "")
    for n in corps.iter("p", "div", "li", "h1", "h2", "h3", "h4", "tr"):
        n.tail = "\n" + (n.tail or "")
    lignes, vide = [], 0
    for ln in corps.text_content().replace("\xa0", " ").splitlines():
        ln = re.sub(r"[ \t]{2,}", " ", ln).strip()
        if not ln:
            vide += 1
            if vide > 1:
                continue
        else:
            vide = 0
        lignes.append(ln)
    return "\n".join(lignes).strip()


class FluxElement(models.Model):
    _name = "bf.flux.element"
    _description = "Élément de flux"
    _order = "date_publication desc, id desc"
    _rec_name = "titre"

    titre = fields.Char("Titre", required=True)
    cle = fields.Char("Identifiant", required=True, index=True, readonly=True,
                      help="Identifiant stable offert par le flux. C'est lui, "
                           "et non le titre ou le lien, qui dédoublonne.")
    lien = fields.Char("Lien", required=True)
    langue = fields.Char("Langue")
    emetteur = fields.Char("Émetteur")
    emetteur_url = fields.Char(
        "Adresse du diffuseur", readonly=True,
        help="Donnée par un agrégateur, dont le lien reste chez lui.")
    sujets = fields.Char("Sujets")
    resume = fields.Text("Chapeau")
    date_publication = fields.Datetime("Publié le", index=True)
    source_ids = fields.Many2many(
        "bf.flux.source", "bf_flux_element_source_rel", "element_id",
        "source_id", string="Sources", readonly=True)

    texte = fields.Text("Texte complet")
    texte_langue = fields.Char(
        "Langue du texte", readonly=True,
        help="Langue de ce qui a réellement été récupéré. Elle peut différer "
             "de la langue de l'élément tant qu'une version préférée n'a pas "
             "encore été rapatriée.")
    texte_etat = fields.Selection(
        [("sans", "Non demandé"), ("a_faire", "À récupérer"),
         ("ok", "Récupéré"), ("passager", "Échec passager"),
         ("definitif", "Échec définitif")],
        string="Texte", default="sans", readonly=True, index=True)
    texte_essais = fields.Integer("Essais", readonly=True)
    texte_message = fields.Char("Message", readonly=True)

    retenue_ids = fields.One2many("bf.flux.retenue", "element_id", string="Retenu par")
    image_url = fields.Char("Image")

    # Le lecteur : ce que MES listes ont retenu, et ce que j'ai lu.
    pour_moi = fields.Boolean(
        "Dans mes listes", compute="_compute_pour_moi", search="_search_pour_moi")
    lu = fields.Boolean("Lu", compute="_compute_lu", search="_search_lu")
    listes_noms = fields.Char("Listes", compute="_compute_listes_noms")
    sources_noms = fields.Char("Source", compute="_compute_listes_noms")

    _sql_constraints = [
        ("cle_unique", "UNIQUE(cle)", "Cet élément est déjà connu."),
    ]

    # ---------------------------------------------------------------- lecteur

    def _mes_listes(self):
        listes = self.env["bf.flux.liste"].sudo().search([("active", "=", True)])
        return listes.filtered(lambda l: self.env.user in l.membre_user_ids)

    def _compute_pour_moi(self):
        mes = self._mes_listes()
        for elem in self:
            elem.pour_moi = bool(elem.sudo().retenue_ids.filtered(
                lambda r: r.etat == "retenu" and r.liste_id in mes))

    def _search_pour_moi(self, operator, value):
        ids = self.env["bf.flux.retenue"].sudo().search([
            ("liste_id", "in", self._mes_listes().ids), ("etat", "=", "retenu"),
        ]).element_id.ids
        positif = (operator == "=") == bool(value)
        return [("id", "in" if positif else "not in", ids)]

    def _compute_lu(self):
        lus = set(self.env["bf.flux.lecture"].search([
            ("user_id", "=", self.env.uid), ("element_id", "in", self.ids)]).element_id.ids)
        for elem in self:
            elem.lu = elem.id in lus

    def _search_lu(self, operator, value):
        ids = self.env["bf.flux.lecture"].search([("user_id", "=", self.env.uid)]).element_id.ids
        positif = (operator == "=") == bool(value)
        return [("id", "in" if positif else "not in", ids)]

    def _compute_listes_noms(self):
        mes = self._mes_listes()
        for elem in self:
            listes = elem.sudo().retenue_ids.filtered(lambda r: r.etat == "retenu").liste_id
            elem.listes_noms = ", ".join((listes & mes or listes).mapped("name"))
            elem.sources_noms = ", ".join(elem.sudo().source_ids.mapped("name"))

    def _flux_url_lire(self):
        """Le lien qui marque lu puis mène à l'article."""
        self.ensure_one()
        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url")
        return f"{base}/flux/lire/{self.id}"

    def action_lire(self):
        """Ouvre l'article et le marque lu pour moi."""
        self.ensure_one()
        self.env["bf.flux.lecture"]._flux_marquer(self, self.env.user)
        return {"type": "ir.actions.act_url", "url": self.lien, "target": "new"}

    def action_marquer_lu(self):
        self.env["bf.flux.lecture"]._flux_marquer(self, self.env.user)
        # Recharger la vue : l'élément lu quitte « À lire » tout de suite.
        return {"type": "ir.actions.client", "tag": "soft_reload"}

    def action_marquer_non_lu(self):
        self.env["bf.flux.lecture"].search([
            ("user_id", "=", self.env.uid), ("element_id", "in", self.ids)]).unlink()
        return {"type": "ir.actions.client", "tag": "soft_reload"}

    # ------------------------------------------------------------ intégration

    @api.model
    def _flux_integrer(self, source, recs):
        """Intègre les éléments lus dans un flux.

        Rend (touchés, nouveaux) : les touchés comprennent les nouveaux et ceux
        dont la version dans la langue préférée vient de remplacer l'autre.
        """
        touches = self.browse()
        nouveaux = self.browse()
        existants = {
            e.cle: e for e in self.with_context(active_test=False).search(
                [("cle", "in", [r["cle"] for r in recs])])
        }
        pref = source.langue_preferee or ""
        maintenant = fields.Datetime.now()
        for rec in recs:
            vals = {
                "titre": (rec.get("titre") or "")[:1000],
                "lien": rec["lien"],
                "langue": rec.get("langue") or "",
                "emetteur": rec.get("emetteur") or "",
                "sujets": ", ".join(rec.get("sujets") or [])[:1000],
                "resume": rec.get("resume") or "",
                "image_url": rec.get("image") or False,
                "emetteur_url": rec.get("source_url") or False,
                # Une date absente ou dans le futur ne dit rien de la fraîcheur :
                # elle vaudrait « neuf » pour toujours aux yeux d'une alerte.
                "date_publication": min(rec.get("date") or maintenant, maintenant),
            }
            elem = existants.get(rec["cle"])
            if not elem:
                vals.update(cle=rec["cle"], source_ids=[(4, source.id)])
                if source.texte_complet:
                    vals["texte_etat"] = "a_faire"
                elem = self.create(vals)
                existants[rec["cle"]] = elem
                nouveaux |= elem
                touches |= elem
                continue
            if source not in elem.source_ids:
                # Déjà connu, mais nouveau pour les listes de cette source.
                elem.source_ids = [(4, source.id)]
                touches |= elem
            if elem._flux_remplacer_par(vals["langue"], pref):
                # Le lien change de langue : le texte déjà rapatrié reste en
                # place, mais il est redemandé. « Langue du texte » dit la
                # vérité tant que la nouvelle version n'est pas arrivée.
                vals.pop("date_publication")
                if source.texte_complet or elem.texte_etat != "sans":
                    vals.update(texte_etat="a_faire", texte_essais=0)
                elem.write(vals)
                touches |= elem
        return touches, nouveaux

    def _flux_remplacer_par(self, langue, pref):
        self.ensure_one()
        if not pref or not langue:
            return False
        return langue.startswith(pref) and not (self.langue or "").startswith(pref)

    # ---------------------------------------------------------- texte complet

    def _flux_nettoyer_texte(self, texte):
        """Coupe le texte à la première ligne qui commence par une coupure de
        la source. Point d'accroche pour un nettoyage propre à un site."""
        self.ensure_one()
        debuts = [
            c.strip().lower()
            for c in (self.source_ids.mapped("coupures") or [])
            for c in (c or "").splitlines() if c.strip()
        ]
        if not debuts:
            return texte
        lignes = texte.splitlines()
        for i, ligne in enumerate(lignes):
            if ligne.strip().lower().startswith(tuple(debuts)):
                return "\n".join(lignes[:i]).strip()
        return texte

    def _flux_recuperer_texte(self):
        for elem in self:
            source = elem.source_ids[:1]
            try:
                brut, ctype = source._flux_telecharger(
                    elem.lien, accept="text/html, */*", publique_seulement=True)
                if ctype and "html" not in ctype.lower():
                    raise FluxErreur(f"Type {ctype}", passager=False)
                texte = elem._flux_nettoyer_texte(texte_de_page(brut))
                if not texte:
                    raise FluxErreur(_("Page sans texte lisible"), passager=False)
            except FluxErreur as exc:
                elem.write({
                    "texte_etat": "passager" if exc.passager else "definitif",
                    "texte_essais": elem.texte_essais + 1,
                    "texte_message": str(exc)[:250],
                })
                continue
            elem.write({
                "texte": texte, "texte_langue": elem.langue,
                "texte_etat": "ok", "texte_message": False,
            })

    @api.model
    def _cron_texte_complet(self, limite=40, essais_max=12):  # voir ESSAIS_MAX du dépôt
        # Seuls les éléments qu'une liste a retenus : sur un flux généraliste,
        # rapatrier toutes les pages frapperait l'hôte pour rien.
        todo = self.search([
            ("texte_etat", "in", ("a_faire", "passager")),
            ("texte_essais", "<", essais_max),
            ("retenue_ids.etat", "=", "retenu"),
        ], order="date_publication desc", limit=limite)
        en_essai = bool(odoo_module.current_test)
        for i, elem in enumerate(todo):
            if i and not en_essai:
                self.env["bf.flux.source"]._pause()
            elem._flux_recuperer_texte()
            if not en_essai:
                self.env.cr.commit()
        return True

    # ------------------------------------------------------ tri et diffusion

    def _flux_trier_et_diffuser(self):
        """Passe les éléments aux listes abonnées à leurs sources."""
        if not self:
            return self.env["bf.flux.retenue"]
        listes = self.mapped("source_ids.liste_ids")
        listes |= self.env["bf.flux.sujet"].sudo().search([("etat", "=", "actif")]).liste_id.sudo()
        retenues = listes.filtered("active")._flux_evaluer(self)
        if self.env.context.get("flux_rattrapage"):
            # Première relève d'une recherche : sa semaine d'arriéré entre sans
            # être diffusée, ni reprise au résumé, ni alertée.
            retenues.write({"rattrapage": True})
        retenues._flux_juger()
        retenues.filtered(lambda r: r.etat == "retenu")._flux_diffuser()
        return retenues

    def action_ouvrir_lien(self):
        self.ensure_one()
        return {"type": "ir.actions.act_url", "url": self.lien, "target": "new"}

    def action_poser_projet(self):
        self.ensure_one()
        projets = self.retenue_ids.liste_id.project_ids
        return {
            "type": "ir.actions.act_window",
            "name": _("Poser au fil d'un projet"),
            "res_model": "bf.flux.poser.projet",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_element_id": self.id,
                "default_project_id": projets[:1].id,
            },
        }
