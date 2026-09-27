# -*- coding: utf-8 -*-
"""Un sujet surveillé : un nom (société, marque, client) qu'on veut voir passer.

Les flux généralistes ne parlent presque jamais d'une PME ni de ses clients :
surveiller un nom exige une recherche, pas un filtre sur les flux déjà lus.
Chaque sujet tient sa propre source de recherche (Google News la publie en
RSS), une par langue.

Le nom seul ne suffit pas à retenir : un nom d'entreprise a presque toujours
des homonymes (un club sportif, une expression courante). La règle garde le nom
et les exclusions ; la description dit au jugement IA de qui il s'agit, et c'est
lui qui écarte l'homonyme.
"""
import re
from datetime import timedelta
from urllib.parse import quote, urlsplit

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

from .flux_liste import compiler, sans_accents, trouves

RECHERCHE = "https://news.google.com/rss/search?q={q}&hl={hl}&gl=CA&ceid=CA:{langue}"
LANGUES = {"fr": "fr-CA", "en": "en-CA"}
# La recherche porte « when:7d » ; un résultat plus vieux ne se retient pas :
# Google News ramène des articles de 2018 pour un nom rare.
FRAICHEUR_JOURS = 7
CADENCE_RECHERCHE = 30


def hote(adresse):
    """« https://www.exemple.com/blog » → « exemple.com »."""
    adresse = (adresse or "").strip()
    if adresse and "://" not in adresse:
        adresse = "https://" + adresse
    nom = (urlsplit(adresse).hostname or "").lower()
    return nom[4:] if nom.startswith("www.") else nom


class FluxSujet(models.Model):
    _name = "bf.flux.sujet"
    _description = "Sujet surveillé"
    _order = "etat, name"
    _check_company_auto = True

    name = fields.Char("Nom surveillé", required=True)
    variantes = fields.Text(
        "Autres graphies",
        help="Une par ligne : sigle, nom commercial, ancienne raison sociale. "
             "Chacune est cherchée comme le nom.")
    description = fields.Text(
        "De qui il s'agit",
        help="Pour le jugement IA : qui est ce sujet, et quels homonymes ignorer. "
             "Exemple : « Firme de consultation TI de Sherbrooke. Pas le club "
             "de hockey Herning Blue Fox ni le club de handball de Montpellier. »")
    exclusions = fields.Text(
        "Exclusions",
        help="Une par ligne. Écarte l'élément quoi qu'il contienne d'autre : "
             "Herning, handball…")
    langues = fields.Selection(
        [("fr_en", "Français et anglais"), ("fr", "Français"), ("en", "Anglais")],
        string="Langues", default="fr_en", required=True)
    etat = fields.Selection(
        [("propose", "Proposé"), ("actif", "Surveillé")],
        string="État", default="actif", required=True, index=True,
        help="Un sujet proposé depuis les clients n'est pas cherché tant qu'il "
             "n'est pas validé : un nom ambigu surveillé sans exclusions "
             "remplirait la liste d'homonymes.")
    liste_id = fields.Many2one(
        "bf.flux.liste", string="Liste", required=True, ondelete="cascade",
        index=True, check_company=True)
    company_id = fields.Many2one(related="liste_id.company_id", store=True)
    partner_id = fields.Many2one(
        "res.partner", string="Client", ondelete="set null",
        help="Le client dont vient ce sujet, s'il a été proposé depuis les clients actifs.")
    source_ids = fields.One2many(
        "bf.flux.source", "sujet_id", string="Recherches",
        context={"active_test": False})
    active = fields.Boolean(default=True)

    # ---------------------------------------------------------------- noms

    def _flux_noms(self):
        self.ensure_one()
        return [n for n in [self.name] + (self.variantes or "").splitlines()
                if n.strip() and not n.strip().startswith("#")]

    def _flux_requete(self):
        """« "Blue Fox" OR "Symbifox" when:7d » : le nom exact, jamais ses mots."""
        noms = ['"%s"' % n.strip().replace('"', "") for n in self._flux_noms()]
        return " OR ".join(noms) + f" when:{FRAICHEUR_JOURS}d"

    def _flux_urls(self):
        self.ensure_one()
        langues = ["fr", "en"] if self.langues == "fr_en" else [self.langues]
        q = quote(self._flux_requete())
        return {lg: RECHERCHE.format(q=q, hl=LANGUES[lg], langue=lg) for lg in langues}

    # ------------------------------------------------------------ règles

    def _flux_est_maison(self, element):
        """Vrai si l'élément vient du site du locataire, sous-domaines compris,
        par son lien ou par l'adresse du diffuseur qu'un agrégateur donne."""
        maison = self._flux_hotes_maison()
        for h in (hote(element.lien), hote(element.emetteur_url)):
            if h and any(h == m or h.endswith("." + m) for m in maison):
                return True
        return False

    def _flux_hotes_maison(self):
        """Les hôtes du locataire lui-même : son site et celui de la société."""
        adresses = [
            self.env["ir.config_parameter"].sudo().get_param("web.base.url") or "",
            self.company_id.website or "", self.env.company.website or "",
        ]
        return {hote(a) for a in adresses if hote(a)}

    def _flux_motifs(self, element):
        """Motifs par lesquels ce sujet retient l'élément. Vide : pas retenu.

        Ce que le locataire publie lui-même (son blogue) n'est pas une nouvelle
        à son sujet : sans cette garde, chaque billet devient une alerte.
        """
        self.ensure_one()
        if self._flux_est_maison(element):
            return []
        publie = element.date_publication or element.create_date
        if publie and publie < fields.Datetime.now() - timedelta(days=FRAICHEUR_JOURS):
            return []
        texte = " ".join(filter(None, [
            element.titre, element.resume, element.emetteur, element.sujets]))
        plat = sans_accents(texte)
        exclusions = "\n".join(filter(None, [self.exclusions, self.liste_id.exclusions]))
        if trouves(exclusions, texte, plat):
            return []
        if not trouves("\n".join(self._flux_noms()), texte, plat):
            return []
        return [_("Sujet : %s", self.name)]

    # ------------------------------------------------------------ sources

    def _flux_aligner_sources(self):
        """Une source de recherche par langue, tenue par le sujet.

        Un sujet proposé, archivé ou dont on retire une langue n'est plus
        cherché : sa source est archivée, pas supprimée, pour garder ses
        éléments et leurs retenues.
        """
        Source = self.env["bf.flux.source"].sudo().with_context(active_test=False)
        for sujet in self.sudo().with_context(active_test=False):
            cherche = sujet.active and sujet.etat == "actif" and sujet.liste_id.active
            voulues = sujet._flux_urls() if cherche else {}
            existantes = {s.langue_preferee: s for s in sujet.source_ids}
            for lg, url in voulues.items():
                vals = {
                    "name": _("Recherche : %(nom)s (%(lg)s)", nom=sujet.name, lg=lg),
                    "url": url, "active": True,
                    # La recherche appartient au sujet : elle suit sa liste et sa
                    # société, et quitte l'ancienne liste quand il en change.
                    "liste_ids": [(6, 0, sujet.liste_id.ids)],
                    "company_id": sujet.company_id.id,
                }
                src = existantes.get(lg)
                if src:
                    if src.url != url or not src.active:
                        # Une requête neuve, ou une recherche qui reprend : ce
                        # qu'elle ramène d'abord est un arriéré, pas du neuf.
                        vals.update(rattrapage_du=True, prochaine_releve=fields.Datetime.now())
                    src.write(vals)
                else:
                    Source.create(dict(
                        vals, sujet_id=sujet.id, langue_preferee=lg,
                        cadence_minutes=CADENCE_RECHERCHE, rattrapage_du=True))
            for lg, src in existantes.items():
                if lg not in voulues and src.active:
                    src.active = False

    @api.model_create_multi
    def create(self, vals_list):
        sujets = super().create(vals_list)
        sujets._flux_aligner_sources()
        return sujets

    def write(self, vals):
        res = super().write(vals)
        if {"name", "variantes", "langues", "etat", "active", "liste_id"} & set(vals):
            self._flux_aligner_sources()
        return res

    @api.constrains("name", "variantes", "exclusions")
    def _check_noms(self):
        """Une règle cassée levait à chaque élément de chaque relève : elle se
        refuse ici, à la saisie."""
        for sujet in self:
            for nom in [sujet.name] + (sujet.variantes or "").splitlines():
                nom = nom.strip()
                if not nom or nom.startswith("#"):
                    continue
                if nom.lower().startswith("re:"):
                    raise ValidationError(_(
                        "Un nom ou une graphie se cherche tel quel dans les nouvelles : "
                        "pas d'expression régulière (« %s »).", nom))
                if len(nom) < 3:
                    raise ValidationError(_(
                        "Un nom surveillé d'une ou deux lettres retiendrait presque tout (« %s »).", nom))
            try:
                compiler(sujet.exclusions)
            except re.error as exc:
                raise ValidationError(_(
                    "Expression régulière invalide dans les exclusions : %s", exc)) from exc

    def action_valider(self):
        if not (self.env.su or self.env.user.has_group("bf_flux.group_flux_gestion")):
            raise AccessError(_("Valider un sujet est réservé à la gestion des flux."))
        self.write({"etat": "actif"})
        return True

    def action_mettre_en_attente(self):
        if not (self.env.su or self.env.user.has_group("bf_flux.group_flux_gestion")):
            raise AccessError(_("Suspendre un sujet est réservé à la gestion des flux."))
        self.write({"etat": "propose"})
        return True
