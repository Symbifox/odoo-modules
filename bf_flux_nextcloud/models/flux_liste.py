# -*- coding: utf-8 -*-
"""Le corpus d'une liste, en Markdown, déposé au Nextcloud.

Une base de connaissances lue par un agent : un dossier par source, un fichier
par mois, un bloc par élément, un index.
"""
import hashlib
import logging
import re
from collections import defaultdict

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.modules import module as odoo_module

from odoo.addons.bf_flux.models.flux_liste import TOUTE_LA_SOURCE

_logger = logging.getLogger(__name__)

SCISSION = 450 * 1024  # au-delà, le mois se scinde en suites
TEXTE_MAX = 12000  # un communiqué plus long est tronqué, et on le dit
ESSAIS_MAX = 12  # au-delà, le texte ne viendra plus : on dépose le chapeau


def _nom_sur(nom):
    """Un nom de dossier sans séparateur ni « .. » : WebDAV le prendrait pour un chemin."""
    nom = re.sub(r"[/\\]", "-", nom or "").strip()
    nom = re.sub(r"\.{2,}", ".", nom).strip(". ")
    return nom or "Sans nom"


def _dossier_sur(dossier):
    """Un dossier de dépôt : segments non vides, aucun « . » ni « .. »."""
    segments = [s for s in (dossier or "").replace("\\", "/").split("/") if s.strip()]
    return bool(segments) and all(s.strip() not in (".", "..") for s in segments)


class FluxListe(models.Model):
    _inherit = "bf.flux.liste"

    nc_actif = fields.Boolean("Déposer au Nextcloud", tracking=True)
    nc_config_id = fields.Many2one(
        "nextcloud.document.config", string="Connexion Nextcloud",
        help="La connexion WebDAV de la synchronisation des documents.")
    nc_dossier = fields.Char(
        "Dossier", help="Chemin du dossier de dépôt, depuis la racine de la connexion.")
    nc_nom_index = fields.Char(
        "Nom de l'index", default="00 Index.md", required=True)
    nc_nom_element = fields.Char(
        "Un élément se dit", default="élément", required=True,
        help="Le mot employé dans les fichiers : élément, communiqué, article…")
    nc_intro = fields.Text(
        "Présentation", help="Markdown repris en tête de l'index : ce que le "
                              "corpus contient, pour qui, ce qui reste à valider.")
    nc_derniere = fields.Datetime("Dernier dépôt", readonly=True)
    nc_etat = fields.Selection(
        [("ok", "À jour"), ("attente", "Dépôt en attente")],
        string="État du dépôt", readonly=True)
    nc_message = fields.Char("Message du dépôt", readonly=True)
    nc_depot_ids = fields.One2many("bf.flux.depot", "liste_id", string="Fichiers déposés")

    @api.constrains("nc_actif", "nc_config_id", "nc_dossier", "nc_nom_index")
    def _check_nc(self):
        for liste in self:
            if liste.nc_actif and not (liste.nc_config_id and liste.nc_dossier):
                raise ValidationError(_("Pour déposer, choisissez une connexion et un dossier."))
            if liste.nc_dossier and not _dossier_sur(liste.nc_dossier):
                raise ValidationError(_("Le dossier ne peut contenir ni « . » ni « .. »."))
            # L'index est un nom de fichier Markdown, jamais un chemin : sinon
            # il écraserait n'importe quel fichier du compte relié.
            if not re.match(r"^[^/\\]+\.md$", liste.nc_nom_index or "") or ".." in liste.nc_nom_index:
                raise ValidationError(_("L'index est un nom de fichier « .md », sans dossier."))

    # ------------------------------------------------------------ mise en forme

    def _nc_pluriel(self, n):
        mot = self.nc_nom_element
        return mot if n == 1 else (mot if mot.endswith(("s", "x")) else mot + "s")

    def _nc_bloc(self, ret):
        """Un élément retenu, tel qu'il paraît dans le fichier du mois."""
        elem = ret.element_id
        jour = fields.Date.to_string(elem.date_publication) if elem.date_publication else "0000-00-00"
        langue = "français" if (elem.langue or "").startswith("fr") else "anglais"
        meta = [f"*Émetteur : {elem.emetteur or 'non précisé'}*",
                f"*Diffusé le {jour} (UTC), en {langue}*"]
        if elem.sujets:
            meta.append(f"*Sujets : {elem.sujets}*")
        if ret.motifs and ret.motifs != TOUTE_LA_SOURCE:
            meta.append(f"*Retenu sur : {ret.motifs}*")
        meta.append(f"*Lien : {elem.lien}*")
        bloc = [f"## {jour} — {elem.titre}", ""] + meta + [""]
        if elem.texte_etat == "ok" and elem.texte:
            texte = elem.texte
            bloc.append(texte[:TEXTE_MAX])
            if len(texte) > TEXTE_MAX:
                bloc += ["", f"*(texte tronqué à {TEXTE_MAX:,} caractères)*".replace(",", " ")]
        elif elem.resume:
            bloc.append(elem.resume)
            if elem.texte_etat not in ("sans", False):
                bloc += ["", "*(seul le chapeau du flux a pu être récupéré : "
                         f"{elem.texte_message or 'page inaccessible'})*"]
        else:
            bloc.append("_(sans corps récupérable)_")
        bloc += ["", "---", ""]
        return "\n".join(bloc)

    def _nc_source_de(self, elem):
        """La source sous laquelle l'élément est classé : la première de la
        liste qui l'a apporté, dans l'ordre de création des sources."""
        communes = (elem.source_ids & self.source_ids).sorted("id")
        return communes[:1] or elem.source_ids.sorted("id")[:1]

    def _nc_fichiers(self):
        """{chemin relatif: contenu} : les mois de chaque source, puis l'index."""
        self.ensure_one()
        # Un texte encore attendu n'entre pas : le déposer avec son seul chapeau
        # dirait « page inaccessible » à tort, puis redéposerait le fichier à
        # l'arrivée du texte. On l'attend.
        retenues = self.retenue_ids.filtered(
            lambda r: r.etat == "retenu" and not (
                r.element_id.texte_etat == "a_faire"
                or (r.element_id.texte_etat == "passager"
                    and r.element_id.texte_essais < ESSAIS_MAX)))
        paquets = defaultdict(list)
        for ret in retenues:
            source = self._nc_source_de(ret.element_id)
            mois = (fields.Date.to_string(ret.element_id.date_publication) or "sans-date")[:7]
            paquets[(source, mois)].append(ret)

        fichiers, releve = {}, []
        for (source, mois), rets in sorted(paquets.items(), key=lambda kv: (kv[0][0].name, kv[0][1])):
            rets.sort(key=lambda r: (r.element_id.date_publication or fields.Datetime.now(), r.element_id.id))
            blocs = [(r, self._nc_bloc(r)) for r in rets]
            morceaux, cur, taille = [], [], 0
            for r, bloc in blocs:
                b = len(bloc.encode())
                if cur and taille + b > SCISSION:
                    morceaux.append(cur)
                    cur, taille = [], 0
                cur.append((r, bloc))
                taille += b
            if cur:
                morceaux.append(cur)
            dossier = _nom_sur(source.name)
            for n, morceau in enumerate(morceaux, 1):
                # La phrase du filtre ne se dit que si une entrée du fichier a
                # réellement été filtrée : un élément reçu aussi par une source
                # prise en entière peut être classé sous une source filtrée.
                filtre = any(r.motifs != TOUTE_LA_SOURCE for r, _b in morceau)
                # ⚠️ Le premier morceau garde TOUJOURS le nom nu du mois : un
                # mois qui grossit ne fait qu'ajouter des suites (voir le module).
                nom = f"{mois}{' - suite %s' % n if n > 1 else ''}.md"
                debut = fields.Date.to_string(morceau[0][0].element_id.date_publication)
                fin = fields.Date.to_string(morceau[-1][0].element_id.date_publication)
                periode = f"{debut} au {fin}"
                entete = [
                    f"# {source.name} — {mois}"
                    + (f" ({n} de {len(morceaux)})" if len(morceaux) > 1 else ""),
                    "",
                    f"{len(morceau)} {self._nc_pluriel(len(morceau))} du {periode}, "
                    f"relevés sur le flux RSS {source.url}. Dédoublonnage par "
                    f"identifiant, version {'française' if source.langue_preferee == 'fr' else 'anglaise'} "
                    f"retenue quand elle existe."
                    + (" Le texte est celui de la page complète, pas du chapeau du flux."
                       if source.texte_complet else "")
                    + (" Ce flux passe par un filtre : ce qui l'a retenu est indiqué "
                       "sous chaque entrée." if filtre else ""),
                    "",
                ]
                contenu = "\n".join(entete) + "\n".join(b for _r, b in morceau)
                fichiers[f"{dossier}/{nom}"] = contenu
                releve.append({"source": source, "nom": nom, "n": len(morceau),
                               "ko": round(len(contenu.encode()) / 1024, 1),
                               "periode": periode})
        fichiers[self.nc_nom_index] = self._nc_index(releve)
        return fichiers

    def _nc_index(self, releve):
        total = sum(r["n"] for r in releve)
        lignes = [
            f"# {self.name} : corpus et index", "",
            # La date seule : un index réécrit à la minute serait redéposé à
            # chaque passage pour rien.
            f"Dernière mise à jour : {fields.Date.to_string(fields.Date.today())}. "
            f"**{total} {self._nc_pluriel(total)}** au corpus.", "",
        ]
        if self.nc_intro:
            lignes += [self.nc_intro.strip(), ""]
        lignes += ["## Sources", "", "| Source | Éléments | Fichiers | Traitement |",
                   "|---|---|---|---|"]
        for source in self.source_ids.sorted("id"):
            rs = [r for r in releve if r["source"] == source]
            if not (self.termes or self.emetteurs_seuls) or source in self.sources_entieres_ids:
                traitement = "prise en entier"
            else:
                traitement = "filtrée"
            if not source.active:
                traitement += ", hors service"
            lignes.append(f"| {source.name} | {sum(r['n'] for r in rs)} | {len(rs)} | {traitement} |")
        lignes += ["", "## Fichiers", ""]
        for source in sorted({r["source"] for r in releve}, key=lambda s: s.name):
            lignes += [f"### {_nom_sur(source.name)}", ""]
            for r in sorted((r for r in releve if r["source"] == source), key=lambda r: r["nom"]):
                lignes.append(f"- `{r['nom']}` — {r['n']} {self._nc_pluriel(r['n'])}, "
                              f"{r['ko']} Ko ({r['periode']})")
            lignes.append("")
        return "\n".join(lignes)

    # ------------------------------------------------------------------ dépôt

    def _nc_deposer(self):
        """Dépose ce qui a changé. Rend (déposés, inchangés, échecs)."""
        self.ensure_one()
        config = self.nc_config_id.sudo()
        racine = "/" + self.nc_dossier.strip("/")
        registre = {d.chemin: d for d in self.sudo().nc_depot_ids}
        deposes, inchanges, echecs, dossiers = 0, 0, [], set()
        if not _dossier_sur(self.nc_dossier):
            raise UserError(_("Dossier de dépôt refusé."))
        for chemin, contenu in self._nc_fichiers().items():
            # Seul du Markdown, et seulement sous le dossier de la liste.
            if not chemin.endswith(".md") or ".." in chemin.split("/"):
                continue
            brut = contenu.encode()
            empreinte = hashlib.sha1(brut).hexdigest()
            deja = registre.get(chemin)
            if deja and deja.empreinte == empreinte:
                inchanges += 1
                continue
            complet = f"{racine}/{chemin}"
            try:
                parent = complet.rsplit("/", 1)[0]
                # MKCOL de chaque niveau, une fois par passage (405 = existe).
                partiel = ""
                for morceau in parent.strip("/").split("/"):
                    partiel += "/" + morceau
                    if partiel not in dossiers:
                        config._webdav_mkcol(partiel)
                        dossiers.add(partiel)
                config._webdav_put(complet, brut, content_type="text/markdown; charset=utf-8")
            except UserError as exc:
                echecs.append(f"{chemin} : {exc}")
                continue
            vals = {"empreinte": empreinte, "taille": len(brut), "date": fields.Datetime.now()}
            if deja:
                deja.write(vals)
            else:
                registre[chemin] = self.env["bf.flux.depot"].sudo().create(
                    dict(vals, liste_id=self.id, chemin=chemin))
            deposes += 1
        self.sudo().write({
            "nc_derniere": fields.Datetime.now(),
            "nc_etat": "attente" if echecs else "ok",
            "nc_message": (_("%(n)s dépôt(s) en attente : %(e)s", n=len(echecs), e=echecs[0])[:250]
                           if echecs else False),
        })
        return deposes, inchanges, echecs

    def action_nc_deposer(self):
        # Le dépôt passe par la connexion Nextcloud en superutilisateur :
        # seule la gestion des flux le déclenche.
        if not (self.env.su or self.env.user.has_group("bf_flux.group_flux_gestion")):
            raise AccessError(_("Déposer au Nextcloud est réservé à la gestion des flux."))
        for liste in self:
            deposes, inchanges, echecs = liste._nc_deposer()
            liste.message_post(body=_(
                "Dépôt Nextcloud : %(d)s fichier(s) déposé(s), %(i)s inchangé(s), %(e)s en attente.",
                d=deposes, i=inchanges, e=len(echecs)))
        return True

    @api.model
    def _cron_nc_deposer(self):
        en_essai = bool(odoo_module.current_test)
        for liste in self.search([("nc_actif", "=", True), ("active", "=", True)]):
            try:
                liste._nc_deposer()
            except Exception:
                _logger.exception("Dépôt Nextcloud de la liste %s", liste.name)
                if not en_essai:
                    self.env.cr.rollback()
                continue
            if not en_essai:
                self.env.cr.commit()
        return True
