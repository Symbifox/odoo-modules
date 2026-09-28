"""Associer des étiquettes par tableur : une ligne, une étiquette ou une plage.

Colonnes reconnues (l'en-tête compte, pas l'ordre ; accents et casse ignorés) :

``etiquette``  « 12 », « 12-20 », « QR-0012 », « QR-0012..QR-0020 » ou le code
``type``       le type de fiche : son libellé (« Contact ») ou son nom technique
``fiche``      l'identifiant de la fiche, ou son nom exact
``adresse``    une adresse https, à la place d'une fiche
``geste``      facultatif : ouvrir, adresse, passage, ou le code d'un geste
``libelle``    facultatif
``posee_sur``  facultatif
``public``     facultatif : oui / non

🔴 **Tout est vérifié avant la première écriture.** L'aperçu montre chaque ligne
avec ce qu'elle fera ou pourquoi elle est refusée. Rien n'est écrit tant qu'une
ligne est en erreur, sauf si on choisit explicitement d'ignorer ces lignes. Un
tableur à moitié appliqué est pire qu'un tableur refusé : on ne sait plus quelles
étiquettes ont bougé.
"""
import base64
import csv
import io
import re
import unicodedata

from odoo import _, api, fields, models
from odoo.exceptions import UserError

LIGNES_MAX = 5000
GESTES_ALIAS = {
    "ouvrir": "open", "fiche": "open", "open": "open",
    "adresse": "url", "url": "url", "lien": "url",
    "passage": "note", "note": "note", "visite": "note",
}
PLAGE_NUM = re.compile(r"^\s*(\d+)\s*(?:-|\.\.|à|a)\s*(\d+)\s*$")
PLAGE_REF = re.compile(r"^\s*([A-Za-z0-9]+-\d+)\s*(?:\.\.|:|à)\s*([A-Za-z0-9]+-\d+)\s*$")


def _cle(texte):
    texte = unicodedata.normalize("NFKD", str(texte or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", texte.strip().lower()).strip("_")


def lire_tableur(nom, donnees):
    """Rend la liste des lignes (dicts par en-tête normalisé), en-tête exclu."""
    if (nom or "").lower().endswith((".xlsx", ".xlsm")):
        import openpyxl
        classeur = openpyxl.load_workbook(io.BytesIO(donnees), read_only=True, data_only=True)
        rangs = [[("" if v is None else v) for v in rang]
                 for rang in classeur.active.iter_rows(values_only=True)]
    else:
        texte = donnees.decode("utf-8-sig", errors="replace")
        try:
            dialecte = csv.Sniffer().sniff(texte[:4096], delimiters=",;\t")
        except csv.Error:
            dialecte = csv.excel
        rangs = list(csv.reader(io.StringIO(texte), dialecte))
    rangs = [r for r in rangs if any(str(v).strip() for v in r)]
    if not rangs:
        return []
    entete = [_cle(v) for v in rangs[0]]
    lignes = []
    for rang in rangs[1:]:
        valeurs = {}
        for i, cle in enumerate(entete):
            if cle and i < len(rang):
                v = rang[i]
                if isinstance(v, float) and v.is_integer():
                    v = int(v)
                valeurs[cle] = str(v).strip()
        lignes.append(valeurs)
    return lignes


class BfQrImport(models.TransientModel):
    _name = "bf.qr.import"
    _description = "Associer des étiquettes QR par tableur"

    batch_id = fields.Many2one("bf.qr.batch", string="Lot",
                               help="Pour lire les numéros seuls (« 12 ») dans le préfixe de ce lot.")
    fichier = fields.Binary(string="Tableur", required=True)
    fichier_nom = fields.Char()
    state = fields.Selection([("saisie", "Fichier"), ("apercu", "Aperçu")], default="saisie")
    ligne_ids = fields.One2many("bf.qr.import.ligne", "import_id", string="Lignes")
    count_ok = fields.Integer(compute="_compute_counts", string="Prêtes")
    count_erreur = fields.Integer(compute="_compute_counts", string="En erreur")
    count_etiquettes = fields.Integer(compute="_compute_counts", string="Étiquettes touchées")
    ignorer_erreurs = fields.Boolean(string="Appliquer les lignes prêtes et laisser les autres")

    @api.depends("ligne_ids.statut", "ligne_ids.tag_ids")
    def _compute_counts(self):
        for assistant in self:
            ok = assistant.ligne_ids.filtered(lambda l: l.statut == "ok")
            assistant.count_ok = len(ok)
            assistant.count_erreur = len(assistant.ligne_ids) - len(ok)
            assistant.count_etiquettes = len(ok.tag_ids)

    # ------------------------------------------------------------------
    # Analyse
    # ------------------------------------------------------------------
    def action_analyser(self):
        self.ensure_one()
        if not self.fichier:
            raise UserError(_("Joignez un tableur (.xlsx ou .csv)."))
        try:
            lignes = lire_tableur(self.fichier_nom, base64.b64decode(self.fichier))
        except Exception as echec:  # noqa: BLE001
            raise UserError(_("Ce fichier ne se lit pas comme un tableur : %s", echec))
        if not lignes:
            raise UserError(_("Le tableur est vide, ou n'a qu'un en-tête."))
        if len(lignes) > LIGNES_MAX:
            raise UserError(_("%s lignes au plus par tableur.", LIGNES_MAX))
        if "etiquette" not in lignes[0] and not any("etiquette" in l for l in lignes):
            raise UserError(_("La colonne « etiquette » est introuvable dans l'en-tête."))
        self.ligne_ids.unlink()
        vues = {}
        valeurs = []
        for rang, ligne in enumerate(lignes, start=2):
            valeurs.append(self._analyser_ligne(rang, ligne, vues))
        self.write({"ligne_ids": [(0, 0, v) for v in valeurs], "state": "apercu"})
        return self._rouvrir()

    def _analyser_ligne(self, rang, ligne, vues):
        resume = " · ".join(v for v in (ligne.get("etiquette"), ligne.get("type"), ligne.get("fiche"),
                                        ligne.get("adresse")) if v)
        base = {"rang": rang, "resume": resume[:200], "statut": "erreur"}
        try:
            etiquettes = self._etiquettes(ligne.get("etiquette", ""))
            for tag in etiquettes:
                if tag.id in vues:
                    raise UserError(_("%(e)s figure déjà à la ligne %(l)s.",
                                      e=tag.qr_reference, l=vues[tag.id]))
                if not tag.qr_vierge:
                    raise UserError(_("%s est déjà associée : réinitialisez-la d'abord.",
                                      tag.qr_reference))
            cible = self._cible(ligne.get("type", ""), ligne.get("fiche", ""))
            adresse = ligne.get("adresse", "")
            geste = self._geste(ligne.get("geste", ""), cible, adresse)
            public = _cle(ligne.get("public", "")) in ("oui", "o", "yes", "y", "1", "vrai", "true", "x")
            # Le contrôle complet est celui de l'association elle-même, joué à blanc :
            # le tableur ne peut pas accepter ce que l'écran refuserait.
            with self.env.cr.savepoint() as point:
                etiquettes._associer(geste, cible=cible, url=adresse,
                                     libelle=ligne.get("libelle"), place=ligne.get("posee_sur"),
                                     public=public)
                point.rollback()
            self.env.invalidate_all()
            for tag in etiquettes:
                vues[tag.id] = rang
            quoi = cible.display_name if cible else adresse
            base.update({
                "statut": "ok",
                "tag_ids": [(6, 0, etiquettes.ids)],
                "gesture_id": geste.id,
                "res_model": cible._name if cible else False,
                "res_id": cible.id if cible else False,
                "url": adresse or False,
                "libelle": ligne.get("libelle") or False,
                "place": ligne.get("posee_sur") or False,
                "public": public,
                "message": _("%(n)s étiquette(s) → %(g)s : %(q)s", n=len(etiquettes),
                             g=geste.name, q=quoi or "—"),
            })
        except UserError as refus:
            self.env.invalidate_all()
            base["message"] = str(refus)[:500]
        except Exception as refus:  # noqa: BLE001 — droits, valeur absurde : une phrase, pas un plantage
            self.env.invalidate_all()
            base["message"] = str(refus)[:500]
        return base

    def _etiquettes(self, texte):
        Tag = self.env["bf.nfc.tag"].sudo().with_context(active_test=False)
        societe = self.batch_id.company_id or self.env.company
        texte = (texte or "").strip()
        if not texte:
            raise UserError(_("La colonne « etiquette » est vide."))
        prefixe = self.batch_id.prefixe
        numeros = None
        plage = PLAGE_NUM.match(texte)
        if plage or texte.isdigit():
            if not prefixe:
                raise UserError(_("« %s » : un numéro seul se lit dans un lot. Lancez l'import "
                                  "depuis le lot, ou écrivez le numéro complet (QR-0012).", texte))
            debut, fin = (int(plage.group(1)), int(plage.group(2))) if plage else (int(texte),) * 2
            numeros = (prefixe, debut, fin)
        plage_ref = PLAGE_REF.match(texte)
        if plage_ref:
            (p1, n1), (p2, n2) = (plage_ref.group(i).rsplit("-", 1) for i in (1, 2))
            if p1.upper() != p2.upper():
                raise UserError(_("« %s » : une plage garde le même préfixe.", texte))
            numeros = (p1.upper(), int(n1), int(n2))
        if numeros:
            prefixe, debut, fin = numeros
            if fin < debut:
                raise UserError(_("« %s » : la plage est à l'envers.", texte))
            if fin - debut >= LIGNES_MAX:
                raise UserError(_("« %s » : plage trop longue.", texte))
            trouvees = Tag.search([("company_id", "=", societe.id), ("qr_prefixe", "=", prefixe),
                                   ("qr_numero", ">=", debut), ("qr_numero", "<=", fin),
                                   ("active", "=", True)])
            manquants = set(range(debut, fin + 1)) - set(trouvees.mapped("qr_numero"))
            if manquants:
                raise UserError(_("« %(t)s » : %(n)s numéro(s) introuvable(s), dont %(p)s-%(x)s.",
                                  t=texte, n=len(manquants), p=prefixe, x=min(manquants)))
            return trouvees.sorted("qr_numero")
        tag = Tag.search([("company_id", "=", societe.id), ("active", "=", True), "|",
                          ("qr_reference", "=ilike", texte), ("code", "=", texte.upper())], limit=1)
        if not tag or not tag.qr_batch_id:
            raise UserError(_("Étiquette « %s » introuvable.", texte))
        return tag

    def _cible(self, type_texte, fiche_texte):
        type_texte, fiche_texte = (type_texte or "").strip(), (fiche_texte or "").strip()
        if not fiche_texte:
            if type_texte:
                raise UserError(_("Un type de fiche sans fiche : remplissez « fiche »."))
            return None
        if not type_texte:
            raise UserError(_("Une fiche sans type : remplissez « type »."))
        gestion = self.env.user.has_group("bf_nfc.group_nfc_manager")
        permis = self.env["bf.nfc.tag"]._modeles_cibles(gestion=gestion)
        IrModel = self.env["ir.model"].sudo()
        modele = None
        for nom in permis:
            if _cle(type_texte) in (_cle(nom), _cle(IrModel._get(nom).name)):
                modele = nom
                break
        if not modele:
            Type = self.env["bf.nfc.target.type"].sudo()
            ligne = Type.search([("model", "in", permis)]).filtered(
                lambda t: _cle(t.name) == _cle(type_texte))[:1]
            modele = ligne.model if ligne else None
        if not modele:
            raise UserError(_("Type de fiche « %s » inconnu ou non permis.", type_texte))
        Modele = self.env[modele]
        if fiche_texte.isdigit():
            fiche = Modele.browse(int(fiche_texte)).exists()
        else:
            fiche = Modele.search([(Modele._rec_name or "name", "=ilike", fiche_texte)], limit=2)
            if len(fiche) > 1:
                raise UserError(_("« %s » désigne plusieurs fiches : donnez son identifiant.",
                                  fiche_texte))
        if not fiche:
            raise UserError(_("Fiche « %(f)s » introuvable dans « %(t)s ».",
                              f=fiche_texte, t=type_texte))
        return fiche

    def _geste(self, texte, cible, adresse):
        Geste = self.env["bf.nfc.gesture"].sudo()
        code = GESTES_ALIAS.get(_cle(texte), (texte or "").strip())
        if not code:
            code = "url" if adresse and not cible else "open"
        geste = Geste.search([("code", "=", code)], limit=1)
        if not geste or geste.kind == "vierge":
            raise UserError(_("Geste « %s » inconnu.", texte))
        if adresse and cible:
            raise UserError(_("Une fiche OU une adresse, pas les deux."))
        return geste

    # ------------------------------------------------------------------
    # Application
    # ------------------------------------------------------------------
    def action_appliquer(self):
        self.ensure_one()
        if self.count_erreur and not self.ignorer_erreurs:
            raise UserError(_("%s ligne(s) en erreur. Corrigez le tableur, ou cochez « Appliquer "
                              "les lignes prêtes ».", self.count_erreur))
        pretes = self.ligne_ids.filtered(lambda l: l.statut == "ok")
        if not pretes:
            raise UserError(_("Aucune ligne prête."))
        for ligne in pretes:
            cible = self.env[ligne.res_model].browse(ligne.res_id) if ligne.res_model else None
            ligne.tag_ids._associer(ligne.gesture_id, cible=cible, url=ligne.url,
                                    libelle=ligne.libelle, place=ligne.place, public=ligne.public)
        nombre = len(pretes.tag_ids)
        return {"type": "ir.actions.client", "tag": "display_notification", "params": {
            "type": "success",
            "message": _("%s étiquettes associées.", nombre),
            "next": {"type": "ir.actions.act_window_close"}}}

    def action_retour(self):
        self.ensure_one()
        self.ligne_ids.unlink()
        self.state = "saisie"
        return self._rouvrir()

    def _rouvrir(self):
        return {"type": "ir.actions.act_window", "res_model": self._name, "res_id": self.id,
                "view_mode": "form", "target": "new", "name": _("Associer par tableur")}


class BfQrImportLigne(models.TransientModel):
    _name = "bf.qr.import.ligne"
    _description = "Ligne d'un tableur d'association"
    _order = "rang"

    import_id = fields.Many2one("bf.qr.import", required=True, ondelete="cascade")
    rang = fields.Integer(string="Ligne")
    resume = fields.Char(string="Contenu")
    statut = fields.Selection([("ok", "Prête"), ("erreur", "Erreur")], required=True)
    message = fields.Char()
    tag_ids = fields.Many2many("bf.nfc.tag", string="Étiquettes")
    gesture_id = fields.Many2one("bf.nfc.gesture")
    res_model = fields.Char()
    res_id = fields.Integer()
    url = fields.Char()
    libelle = fields.Char()
    place = fields.Char()
    public = fields.Boolean()
