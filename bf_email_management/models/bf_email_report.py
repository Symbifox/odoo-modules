"""Signaler un pourriel ou un hameçonnage depuis la boîte.

Trois gestes, une seule fenêtre (``bf.email.report.wizard``) :

- **ranger** : le courriel part dans le dossier Indésirables DU SERVEUR, celui
  que le serveur marque ``\\Junk`` (RFC 6154), à défaut un dossier nommé Junk,
  Spam, Indésirables ou Pourriels. ⚠️ Jamais de dossier créé : le moteur de
  renvoi crée la destination qu'il ne trouve pas, et un « Junk » écrit en dur
  fabriquait un dossier de plus sur un compte Gmail au lieu d'utiliser
  ``[Gmail]/Spam``. Sans dossier trouvé, la ligne sort de la boîte et le
  serveur reste tel quel.
- **bloquer** : une règle personnelle range les prochains courriels de la même
  adresse dans ce même dossier, sans avis.
- **porter plainte** : par autorité (``bf.email.report.authority``). Une
  autorité par courriel reçoit une plainte préparée en BROUILLON sur une tâche
  du projet choisi aux réglages : rien ne part au gouvernement sans « Envoyer
  maintenant ». Une autorité par formulaire web (la FTC, qui a retiré
  ``spam@uce.gov``) reçoit le texte à copier dans la fenêtre.

Le signalement d'hameçonnage passe à ``bf_security_awareness`` quand il est
installé (``bf.reported.phish`` : retrait des copies, billet, alerte), sans
dépendance de manifeste.

Le courriel signalé porte ``report_kind`` : c'est ce qui permet au balayage de
renvoi de le ranger dans Indésirables plutôt que dans les archives quand le
déplacement a échoué une première fois, et à « Annuler » de tout défaire.
"""
import html
import json
import logging
import re
from datetime import datetime
from email.utils import getaddresses, parseaddr

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .bf_email_mobile import MAX_BULK_IDS

_logger = logging.getLogger(__name__)

JUNK_SPECIAL_USE = "\\junk"
# Repli par le nom quand le serveur ne marque rien (vieux Dovecot, Exchange).
# Comparé en minuscules au nom COMPLET du dossier.
JUNK_NAMES = (
    "junk", "spam", "indésirables", "indesirables", "pourriels",
    "courrier indésirable", "junk e-mail", "junk email", "[gmail]/spam",
)
# Même sentinelle que les brouillons parqués d'avant ``bf_is_draft`` : la date
# ne décide plus rien (le drapeau tient le cron à distance), mais le noyau
# refuse une date passée.
DRAFT_SENTINEL = datetime(2031, 1, 1)

COUNTRY_NAMES = {
    "US": "the United States", "CA": "Canada", "GB": "the United Kingdom",
    "FR": "France", "DE": "Germany", "NL": "the Netherlands", "RU": "Russia",
    "CN": "China", "IN": "India",
}
DROP_HEADERS = ("ARC-Seal", "ARC-Message-Signature", "ARC-Authentication-Results",
                "DKIM-Signature", "ARC-Authentication")
BODY_MAX = 2000
# Marque des règles créées par « bloquer » : la déduplication ne touche
# qu'elles, jamais une règle écrite à la main (sinon elle
# réactivait une vieille règle de renvoi désactivée).
BLOCK_RECIPE = "bf_report_block"
# Ce qui, dans un objet, ressemble à un domaine mais est un nom de fichier.
FILE_EXTENSIONS = {
    "pdf", "doc", "docx", "xls", "xlsx", "csv", "txt", "jpg", "jpeg", "png",
    "gif", "zip", "rar", "html", "htm", "ppt", "pptx", "odt", "ods", "eml",
    "ics", "xml", "json", "mp3", "mp4", "mov", "exe", "msg",
}
UNSUBSCRIBE_WORDS = re.compile(
    r"unsubscribe|opt[ -]?out|d[ée]sabonn|d[ée]sinscri|se retirer de la liste",
    re.IGNORECASE)
PHONE_RE = re.compile(r"(?:\+?\d[\s().-]?){10,}")
POSTAL_RE = re.compile(
    r"\b[A-CEGHJ-NPR-TVXY]\d[A-CEGHJ-NPR-TV-Z] ?\d[A-CEGHJ-NPR-TV-Z]\d\b"  # Canada
    r"|\b[A-Z]{2} \d{5}(?:-\d{4})?\b"  # États-Unis : « NY 10001 »
    r"|\b\d{1,5} [\w'. -]{2,40} (?:street|st\.|avenue|ave\.|road|rd\.|rue|boulevard|blvd)\b",
    re.IGNORECASE)


def _like_exact(text):
    """La valeur d'un `=ilike` qui ne doit rien attraper d'autre qu'elle-même.

    🔴 `_` et `%` sont des jokers : sans échappement, « jean_paul@exemple.test »
    rapprocherait aussi « jeanXpaul@exemple.test » (recopié de
    bf_membership).
    """
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")



class BfEmailAccountJunk(models.Model):
    _inherit = "bf.email.account"

    def _junk_folder_name(self):
        """Le dossier Indésirables de ce compte, lu au cache, ou ``False``.

        Le dossier marqué ``\\Junk`` d'abord, quel que soit son nom ; sinon un
        nom connu. Aucun appel au serveur : le cron miroir tient
        ``folder_cache`` au chaud (même source que ``_sent_folder_names``).
        """
        self.ensure_one()
        try:
            entrees = json.loads(self.sudo().folder_cache or "[]") or []
        except (TypeError, ValueError):
            entrees = []
        noms = {}
        for entree in entrees:
            if not isinstance(entree, dict) or not entree.get("name"):
                continue
            special = {s.lower() for s in entree.get("special") or ()}
            if JUNK_SPECIAL_USE in special:
                return entree["name"]
            noms.setdefault(entree["name"].lower(), entree["name"])
        for candidat in JUNK_NAMES:
            if candidat in noms:
                return noms[candidat]
        return False


class BfEmailReportAuthority(models.Model):
    _name = "bf.email.report.authority"
    _description = "Autorité qui reçoit les signalements de pourriel"
    _order = "sequence, id"

    name = fields.Char(string="Autorité", required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company", string="Société",
        help="Vide : offerte à toutes les sociétés.")
    country_id = fields.Many2one(
        "res.country", string="Pays",
        help="Le pays dont l'autorité applique la loi. Sert de rappel dans la "
             "fenêtre, rien n'est filtré dessus.")
    channel = fields.Selection(
        [("email", "Courriel"), ("form", "Formulaire web")],
        string="Canal", required=True, default="email",
        help="Courriel : la plainte est préparée en brouillon sur une tâche, "
             "avec le courriel d'origine joint. Formulaire : la fenêtre donne "
             "le lien et le texte à copier, rien n'est préparé.")
    email = fields.Char(string="Adresse")
    url = fields.Char(string="Lien du formulaire")
    for_spam = fields.Boolean(string="Pourriel", default=True)
    for_phish = fields.Boolean(string="Hameçonnage")
    template = fields.Selection(
        [("casl", "Plainte LCAP (Canada)"), ("generic", "Signalement simple")],
        string="Texte", required=True, default="generic",
        help="Plainte LCAP : les motifs de la Loi canadienne anti-pourriel, "
             "établis à partir du courriel (le motif du désabonnement n'est "
             "retenu que si aucun lien de désabonnement n'existe). "
             "Signalement simple : l'expéditeur, la date, l'objet et les "
             "en-têtes, en anglais.")
    note = fields.Text(string="Notes")

    @api.constrains("channel", "email", "url")
    def _check_canal(self):
        for rec in self:
            if rec.channel == "email" and not (rec.email and "@" in rec.email):
                raise UserError(_("Une autorité par courriel demande une adresse."))
            if rec.channel == "form" and not rec.url:
                raise UserError(_("Une autorité par formulaire demande un lien."))
            # Le lien est servi dans la fenêtre de signalement : jamais un
            # schéma qui s'exécute (javascript:, data:).
            if rec.url and not re.match(r"^https?://", rec.url.strip(), re.IGNORECASE):
                raise UserError(_("Le lien d'une autorité commence par https:// "
                                  "ou http://."))

    def _destinataire(self):
        """Le contact qui reçoit la plainte, retrouvé par l'adresse ou créé."""
        self.ensure_one()
        Partner = self.env["res.partner"].sudo()
        adresse = (self.email or "").strip().lower()
        partner = Partner.search([("email", "=ilike", _like_exact(adresse))], limit=1)
        return partner or Partner.create({"name": self.name, "email": adresse})


class BfEmailReport(models.Model):
    _inherit = "bf.email"

    report_kind = fields.Selection(
        [("spam", "Pourriel"), ("phish", "Hameçonnage")],
        string="Signalé comme", index=True, copy=False, readonly=True)
    reported_at = fields.Datetime(string="Signalé le", copy=False, readonly=True)

    # ------------------------------------------------------------------
    # Le geste
    # ------------------------------------------------------------------
    def _report_refus(self):
        """La raison de ne pas signaler ces courriels, ou une chaîne vide.

        🔴 Sortie des gardes de [action_report_spam] pour que le téléphone
        refuse EXACTEMENT ce que la fenêtre refuse : deux listes de
        gardes auraient divergé au premier ajout, et c'est du côté de l'appli
        que le refus manquerait — celui qui ne lève pas d'exception.
        """
        # 🔴 Déjà signalé : rejouer `POST /report` sur le même
        # identifiant — une réponse perdue, un geste retouché sur une liste pas
        # encore rafraîchie — reposait TOUT : une deuxième plainte en brouillon
        # sur la même tâche et, avec `bf_security_awareness`, un deuxième
        # `bf.reported.phish` avec son alerte. Seule la règle de boîte
        # dédupliquait. Rien d'utile ne se ferme au bureau : une ligne signalée
        # est `active=False`, donc hors de toute liste, et « Annuler »
        # (`action_unhandle`) remet `report_kind` à vide — le geste se rouvre.
        deja = self.filtered("report_kind")
        if deja:
            return _(
                "%s courriel(s) sont déjà signalés : « Annuler » les remet en "
                "boîte avant de recommencer.", len(deja))
        # 🔴 Plus dans la boîte : une ligne JETÉE
        # (`action_trash` écrit `active=False`) se signalait encore. Les
        # options la comptaient — un `complaints_enabled` et une liste
        # d'autorités pour un courriel qui n'est plus là — et le geste, lui,
        # demandait au serveur de déplacer depuis un dossier que le message
        # avait déjà quitté, puis « Annuler » l'aurait ramené dans une INBOX
        # dont la personne l'avait sorti. Le refus est le même des deux côtés,
        # et il DIT quoi faire.
        #
        # ⚠️ Archiver, lui, garde `active=True` (seulement `is_handled`) : un
        # courriel rangé dans Archives/AAAA se signale encore, et c'est voulu —
        # la ligne est toujours là, la règle et la plainte ont encore un sens,
        # et `_report_mark` le déplace d'Archives vers Indésirables.
        #
        # Après la garde « déjà signalé » : une ligne signalée est elle aussi
        # inactive, et c'est « Annuler » qu'il faut lui dire.
        hors_boite = self.filtered(lambda r: not r.active)
        if hors_boite:
            return _(
                "%s courriel(s) ne sont plus dans la boîte : la ligne a été "
                "jetée. Remets-les en boîte avant de les signaler.",
                len(hors_boite))
        classees = self.filtered(lambda r: r.res_model and r.res_id)
        if classees:
            return _(
                "%s courriel(s) sont classés sur une fiche et ne se signalent "
                "pas d'ici : le message appartient à son dossier. Utilise "
                "« Re-router… » pour l'en sortir d'abord.", len(classees))
        # Tout ce qui n'est pas reçu : les envois, et les avis automatiques,
        # dont l'expéditeur est notre propre adresse d'envoi.
        envois = self.filtered(lambda r: r.direction != "in")
        if envois:
            # Bloquer ou dénoncer viserait le destinataire, c'est-à-dire un
            # correspondant à soi.
            return _(
                "%s courriel(s) sont des envois : on ne signale que ce qu'on "
                "reçoit.", len(envois))
        return ""

    def action_report_spam(self):
        """Ouvre la fenêtre de signalement sur ces courriels."""
        refus = self._report_refus()
        if refus:
            raise UserError(refus)
        return {
            "type": "ir.actions.act_window",
            "name": _("Signaler"),
            "res_model": "bf.email.report.wizard",
            "view_mode": "form",
            "views": [[False, "form"]],
            "target": "new",
            "context": {"default_email_ids": [(6, 0, self.ids)]},
        }

    def _report_mark(self, kind):
        """Range au dossier Indésirables du serveur et sort la ligne de toute liste.

        Le serveur d'abord, la ligne ensuite : ``_imap_writeback_move`` réécrit
        ``imap_folder`` seulement quand le déplacement a eu lieu, et c'est ce
        dossier que « Annuler » relira pour ramener le message.
        """
        self.check_access("write")
        par_compte = {}
        for rec in self:
            par_compte.setdefault(rec.account_id, self.browse())
            par_compte[rec.account_id] |= rec
        for compte, lignes in par_compte.items():
            if not compte:
                continue
            junk = compte._junk_folder_name()
            if not junk:
                _logger.info(
                    "bf.email signalement (%s) : aucun dossier Indésirables "
                    "connu, %s ligne(s) sorties de la boîte sans bouger au "
                    "serveur", compte.display_name, len(lignes))
                continue
            try:
                lignes._imap_writeback_move(junk, create_missing=False)
            except Exception:
                # Même conduite que la corbeille : la personne a demandé de ne
                # plus le voir. Le balayage horaire réessaiera (vers
                # Indésirables, grâce à `report_kind`).
                _logger.info(
                    "bf.email signalement : déplacement vers %r refusé", junk,
                    exc_info=True)
        maintenant = fields.Datetime.now()
        self.write({
            "report_kind": kind,
            "reported_at": maintenant,
            "is_handled": True,
            "handled_at": maintenant,
            "active": False,
        })
        self._dismiss_awaiting()

    def _report_sender_address(self):
        """L'adresse RÉELLE de l'expéditeur (``parseaddr``), jamais le nom
        affiché : « service@paypal.com » <x@phish.ru> donne x@phish.ru."""
        self.ensure_one()
        return parseaddr(self.email_from or "")[1].strip().lower()

    def _report_block_senders(self):
        """Une règle personnelle par adresse : ses prochains courriels vont
        droit dans le dossier Indésirables de chaque compte (jeton ``{JUNK}``),
        sans avis.

        La condition vise l'adresse exacte, sous ses deux formes d'en-tête :
        « Nom <a@b> » (contient « <a@b> ») ou « a@b » seul (égal). Un simple
        « contient a@b » bloquait aussi jimbob@ en bloquant bob@.

        Rend ``(créées, sans_dossier, sans_adresse)`` : une adresse reçue sur
        un compte sans dossier Indésirables connu n'a pas de règle, plutôt
        qu'une règle qui archiverait, et un courriel dont le ``From`` est vide
        ou illisible n'a aucune adresse à bloquer. Les deux se COMPTENT :
        « demandé et pas fait » se dit dans le compte rendu.
        """
        Rule = self.env["bf.email.rule"]
        par_adresse = {}
        sans_adresse = 0
        for rec in self:
            adresse = rec._report_sender_address()
            if adresse and "@" in adresse:
                par_adresse.setdefault(adresse, rec)
            else:
                # 🔴 `From` vide, ou que `parseaddr` ne lit pas : la boucle le
                # laissait tomber en silence. Zéro règle créée, zéro mot au
                # compte rendu, et `ok:true` — le contraire de ce que le lot
                # vient d'appliquer au hameçonnage.
                sans_adresse += 1
        crees = sans_dossier = 0
        for adresse, rec in par_adresse.items():
            if not (rec.account_id and rec.account_id._junk_folder_name()):
                sans_dossier += 1
                continue
            deja = Rule.with_context(active_test=False).search([
                ("user_id", "=", self.env.uid),
                ("recipe_key", "=", BLOCK_RECIPE),
                ("condition_ids.value", "in", (adresse, "<%s>" % adresse)),
            ], limit=1)
            if deja:
                if not deja.active:
                    deja.active = True
                continue
            Rule.create({
                "name": _("Indésirables : %s", adresse),
                "scope": "user",
                "user_id": self.env.uid,
                "recipe_key": BLOCK_RECIPE,
                "match_type": "any",
                "condition_ids": [
                    (0, 0, {"kind": "condition", "field_name": "email_from",
                            "operator": "contains", "value": "<%s>" % adresse}),
                    (0, 0, {"kind": "condition", "field_name": "email_from",
                            "operator": "equals", "value": adresse}),
                ],
                "set_folder": "{JUNK}",
                "set_handled": True,
                "set_no_popup": True,
                "stop_processing": True,
                "description": _(
                    "Créée en signalant un pourriel depuis la boîte. "
                    "{JUNK} = le dossier Indésirables du compte qui reçoit."),
            })
            crees += 1
        return crees, sans_dossier, sans_adresse

    @api.model
    def _report_phish_available(self):
        """Le signalement d'hameçonnage demande ``bf_security_awareness``.

        Une méthode plutôt qu'un ``"bf.reported.phish" in self.env`` recopié
        trois fois : la fenêtre, les options du téléphone et la garde de
        `mobile_report` doivent répondre LA MÊME chose, et c'est ce qu'un essai
        peut alors mettre à faux pour éprouver la garde.
        """
        return "bf.reported.phish" in self.env

    def _report_to_awareness(self):
        """Hameçonnage : un signalement ``bf.reported.phish`` par courriel, avec
        son .eml, puis le traitement du module (simulation reconnue, billet,
        alerte). Rend le nombre de signalements créés, 0 sans le module."""
        if "bf.reported.phish" not in self.env:
            return 0
        Phish = self.env["bf.reported.phish"].sudo()
        Attachment = self.env["ir.attachment"].sudo()
        crees = 0
        for rec in self:
            report = Phish.create({
                "reporter_id": self.env.user.partner_id.id,
                "category": "phishing",
                "email_from": rec.email_from,
                "subject": rec.subject,
                "details": _("Signalé depuis la boîte de courriels."),
            })
            eml = rec._build_eml_bytes()
            if eml:
                Attachment.create({
                    "name": rec._eml_filename(),
                    "raw": eml,
                    "mimetype": "message/rfc822",
                    "res_model": "bf.reported.phish",
                    "res_id": report.id,
                })
                report.apply_eml_metadata()
            report.process()
            crees += 1
        return crees

    # ------------------------------------------------------------------
    # Plainte
    # ------------------------------------------------------------------
    def _report_stage_complaint(self, authority, kind="spam"):
        """Prépare la plainte en brouillon sur la tâche de l'expéditeur.

        La tâche « <Expéditeur> (spam) » du projet des réglages est retrouvée
        ou créée, comme pour une plainte rédigée à la main. Le brouillon
        porte le .eml : les en-têtes recopiés dans le corps ne suffisent pas à
        qui voudrait refaire l'analyse. Rend le ``mail.scheduled.message``.
        """
        self.ensure_one()
        authority.ensure_one()
        company = self.company_id or self.env.company
        projet = company.bf_email_complaint_project_id
        if not projet:
            raise UserError(_(
                "Aucun projet de plaintes n'est choisi dans les réglages "
                "Courriels de %s.", company.name))
        nom, _adr = self._name_and_address()
        adresse = self._report_sender_address()
        etiquette = _("hameçonnage") if kind == "phish" else "spam"
        # Par l'adresse, pas seulement le nom affiché : dix « Support »
        # différents ne s'empilent pas sur la même tâche.
        nom_tache = "%s (%s)" % (
            ("%s <%s>" % (nom, adresse)) if nom and adresse
            else (adresse or nom or _("Expéditeur inconnu")), etiquette)
        Task = self.env["project.task"]
        tache = Task.search([
            ("project_id", "=", projet.id), ("name", "=", nom_tache),
        ], limit=1)
        if not tache:
            tache = Task.create({"project_id": projet.id, "name": nom_tache})
        objet, corps = self._report_complaint(authority, kind)
        pieces = []
        eml = self._build_eml_bytes()
        if eml:
            pieces = self.env["ir.attachment"].create({
                "name": self._eml_filename(),
                "raw": eml,
                "mimetype": "message/rfc822",
                "res_model": "project.task",
                "res_id": tache.id,
            }).ids
        return self.env["mail.scheduled.message"].create({
            "model": "project.task",
            "res_id": tache.id,
            "author_id": self.env.user.partner_id.id,
            "partner_ids": [(6, 0, authority._destinataire().ids)],
            "subject": objet,
            "body": corps,
            "scheduled_date": DRAFT_SENTINEL,
            "attachment_ids": [(6, 0, pieces)],
            "bf_is_draft": True,
        })

    def _report_evidence(self):
        """Ce que les en-têtes établissent, pour la plainte. Rien n'est inventé :
        un champ introuvable reste vide et la phrase qui le cite disparaît."""
        self.ensure_one()
        brut = self.raw_headers or ""
        nom, adresse = parseaddr(self.email_from or "")
        ip = ""
        trouve = re.search(r"designates\s+([0-9a-fA-F:.]+)\s+as permitted sender", brut)
        if trouve:
            ip = trouve.group(1)
        else:
            trouve = re.search(r"spf=pass[^\n]*?([0-9]{1,3}(?:\.[0-9]{1,3}){3})", brut)
            ip = trouve.group(1) if trouve else ""
        resultats = {}
        for cle in ("dkim", "spf", "dmarc"):
            m = re.search(r"\b%s=(\w+)" % cle, brut)
            if m:
                resultats[cle] = m.group(1).lower()
        auth = ", ".join("%s=%s" % (k.upper(), v) for k, v in resultats.items())
        domaine_dkim = re.search(r"header\.d=([\w.-]+)", brut)
        serveur = (domaine_dkim.group(1) if domaine_dkim
                   else (adresse.split("@", 1)[1] if "@" in adresse else ""))
        pays = re.search(r"X-Migadu-Country:\s*(\S+)", brut)
        score = (re.search(r"X-Spam-Score:\s*([\d.]+)", brut)
                 or re.search(r"X-Migadu-Spam-Score:\s*([\d.]+)", brut))
        vise = ""
        for entete in ("X-Envelope-To", "Delivered-To", "X-Original-To"):
            m = re.search(r"^%s:\s*(.+)$" % entete, brut, re.IGNORECASE | re.MULTILINE)
            if m:
                vise = parseaddr(m.group(1).strip())[1]
                if vise:
                    break
        if not vise:
            destinataires = getaddresses([self.email_to or ""])
            vise = destinataires[0][1] if destinataires else ""
        domaine_vise = vise.split("@", 1)[1].lower() if "@" in vise else ""
        sosie = ""
        for m in re.finditer(r"\b([a-z0-9-]+\.([a-z]{2,}))\b", (self.subject or "").lower()):
            # « facture.pdf » n'est pas un domaine.
            if m.group(2) in FILE_EXTENSIONS or m.group(1) == domaine_vise:
                continue
            sosie = m.group(1)
            break
        entetes = []
        saute = False
        for ligne in brut.replace("\r\n", "\n").split("\n"):
            suite = ligne[:1] in (" ", "\t")
            if not suite:
                saute = any(ligne.startswith(h) for h in DROP_HEADERS)
            if saute or not ligne.strip():
                continue
            propre = re.sub(r"\t+", " ", ligne).rstrip()
            entetes.append(("   " + propre.strip()) if suite else propre)
        texte = self.body_html or ""
        texte = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", texte, flags=re.DOTALL | re.IGNORECASE)
        texte = re.sub(r"<br\s*/?>|</p>|</div>|</tr>", "\n", texte, flags=re.IGNORECASE)
        texte = html.unescape(re.sub(r"<[^>]+>", " ", texte)).replace("\xa0", " ")
        texte = re.sub(r"[ \t]+", " ", texte)
        texte = re.sub(r"\n\s*\n+", "\n\n", texte).strip()
        if len(texte) > BODY_MAX:
            texte = texte[:BODY_MAX].rstrip() + " […]"
        fuseau = pytz.timezone(self.env.user.tz or "America/Montreal")
        date_locale = date_utc = ""
        if self.date:
            utc = pytz.utc.localize(fields.Datetime.to_datetime(self.date))
            local = utc.astimezone(fuseau)
            mois = ["", "January", "February", "March", "April", "May", "June", "July",
                    "August", "September", "October", "November", "December"]
            date_locale = "%s %s %s, %s %s" % (
                local.day, mois[local.month], local.year,
                local.strftime("%H:%M"), local.tzname())
            date_utc = utc.strftime("%a, %d %b %Y %H:%M:%S +0000 UTC")
        return {
            "sender_name": (nom or "").strip(),
            "sender_addr": (adresse or "").strip(),
            "sending_ip": ip,
            "auth": auth,
            "server": serveur,
            "country": pays.group(1) if pays else "",
            "score": score.group(1) if score else "",
            "affected": vise,
            "lookalike": sosie,
            "headers": entetes,
            "body": texte,
            "date_local": date_locale,
            "date_utc": date_utc,
            "auth_all_pass": all(resultats.get(k) == "pass" for k in ("dkim", "spf", "dmarc")),
            # Le motif 6(2)c tombe dès qu'un moyen de désabonnement paraît :
            # l'en-tête, ou un mot de désabonnement dans le corps.
            "unsubscribe": bool(self.unsubscribe_url or self.unsubscribe_mailto
                                or UNSUBSCRIBE_WORDS.search(texte)),
            # Et 6(2)a-b dès qu'une adresse postale ou un téléphone paraît.
            "contact_info": bool(PHONE_RE.search(texte) or POSTAL_RE.search(texte)),
            "message_id": self.message_id_header or "",
        }

    def _report_complaint(self, authority, kind="spam"):
        """(objet, corps HTML) de la plainte, en anglais."""
        self.ensure_one()
        e = self._report_evidence()
        if authority.template == "casl" and kind == "spam":
            return self._report_complaint_casl(e)
        return self._report_complaint_generic(e, kind)

    def _report_complainant(self):
        """(nom, organisation, téléphone) de la personne qui porte plainte."""
        company = self.company_id or self.env.company
        lieu = ", ".join(p for p in (
            company.city, company.state_id.name, company.country_id.name) if p)
        organisation = company.name + (", " + lieu if lieu else "")
        return self.env.user.name, organisation, company.phone or ""

    def _report_complaint_casl(self, e):
        esc = lambda s: html.escape(s or "", quote=False)
        nom, organisation, telephone = self._report_complainant()
        expediteur = e["sender_addr"] or _("an unknown address")
        pays = COUNTRY_NAMES.get(e["country"], e["country"] or "an unknown location")
        if e["auth_all_pass"]:
            phrase_auth = (
                "SPF, DKIM (d=%s) and DMARC all passed, confirming the message "
                "genuinely originated from %s and was not spoofed."
                % (esc(e["server"]), esc(e["server"])))
        else:
            phrase_auth = ("Authentication results recorded by the receiving "
                           "server: %s." % esc(e["auth"] or "not available"))
        motifs = [
            "it was sent without my consent (section 6(1)): I have no prior "
            "business or non-business relationship with the sender or with %s, "
            "I never gave express or implied consent to receive commercial "
            "messages from this sender, and no exemption applies;"
            % esc(e["server"] or "the sending domain"),
        ]
        # Les motifs 6(2) sont ÉTABLIS par le courriel, plus
        # affirmés.
        if not e["contact_info"]:
            motifs.append(
                "it fails the identification and contact-information "
                "requirements (section 6(2)(a) and (b)): it sets out no "
                "mailing address and no telephone number;")
        if not e["unsubscribe"]:
            motifs.append(
                "it provides no unsubscribe mechanism (section 6(2)(c) and "
                "section 11): it carries no List-Unsubscribe header, and its "
                "body offers no unsubscribe link or wording;")
        if e["lookalike"]:
            domaine = e["affected"].split("@", 1)[1] if "@" in e["affected"] else ""
            motifs.append(
                "its subject line advertises %s%s, a pattern consistent with "
                "automated address harvesting and opportunistic solicitation."
                % (esc(e["lookalike"]),
                   (", a string closely modelled on my own business domain %s"
                    % esc(domaine)) if domaine else ""))
        jour = e["date_local"].split(",")[0] if e["date_local"] else "an unknown date"
        corps = (
            "<p>To the Spam Reporting Centre (Canadian Radio-television and "
            "Telecommunications Commission),</p>"
            "<p>I am filing a formal complaint under Canada's Anti-Spam "
            "Legislation (CASL, S.C. 2010, c. 23) concerning an unsolicited "
            "commercial electronic message received at %(vise)s on %(jour)s.</p>"
            "<p><strong>Complainant.</strong> %(nom)s, %(org)s. Affected "
            "address: %(vise)s.%(tel)s</p>"
            "<p><strong>Respondent (sender).</strong> The message was sent by "
            "%(expnom)s from %(exp)s%(serveur)s%(ip)s. %(auth)s</p>"
            "<p><strong>The message.</strong> It was received on %(date)s%(utc)s, "
            "with the subject line \"%(objet)s\"%(mid)s.%(score)s Its body "
            "reads, verbatim: \"%(corps)s\"</p>"
            "<p><strong>Grounds.</strong> The message is a commercial electronic "
            "message within the meaning of section 1(2) of CASL, since its "
            "purpose is to encourage participation in a commercial activity. It "
            "was accessed on a computer system located in Canada, which engages "
            "the Commission's jurisdiction under section 12. I submit that it "
            "contravenes CASL because:</p><ul>%(motifs)s</ul>"
            "<p><strong>Relief requested.</strong> I ask the Commission to "
            "record this report, to investigate the sender (%(exp)s%(ipcourt)s) "
            "for repeated or systematic commercial messaging directed at "
            "Canadian recipients, and to take such enforcement action as it "
            "considers appropriate, including a notice of violation and "
            "administrative monetary penalties under Part 1 of CASL.</p>"
            "<p>The original message is attached (.eml), and its transport and "
            "authentication headers are reproduced below.</p>"
            "<pre style=\"font-family:monospace;font-size:12px;white-space:pre-wrap;\">"
            "%(entetes)s</pre>"
            "<p>Thank you for your attention to this matter.</p>"
        ) % {
            "vise": esc(e["affected"] or "my address"),
            "jour": esc(jour),
            "nom": esc(nom),
            "org": esc(organisation),
            "tel": (" Telephone: %s." % esc(telephone)) if telephone else "",
            "expnom": esc(e["sender_name"]) or "an unnamed sender",
            "exp": esc(expediteur),
            "serveur": (", via the server %s" % esc(e["server"])) if e["server"] else "",
            "ip": (", originating IP %s (geolocated in %s)" % (esc(e["sending_ip"]), esc(pays)))
                  if e["sending_ip"] else "",
            "ipcourt": (" / %s" % esc(e["sending_ip"])) if e["sending_ip"] else "",
            "auth": phrase_auth,
            "date": esc(e["date_local"] or "an unknown date"),
            "utc": (" (%s)" % esc(e["date_utc"])) if e["date_utc"] else "",
            "objet": esc(self.subject),
            "mid": (" and Message-ID %s" % esc(e["message_id"])) if e["message_id"] else "",
            "score": (" The receiving server assigned it a spam score of %s."
                      % esc(e["score"])) if e["score"] else "",
            "corps": esc(e["body"]),
            "motifs": "".join("<li>%s</li>" % m for m in motifs),
            "entetes": "\n".join(esc(ligne) for ligne in e["headers"]),
        }
        objet = "Formal complaint under CASL: unsolicited commercial email from %s (%s)" % (
            e["sender_name"] or expediteur, jour)
        return objet, corps

    def _report_complaint_generic(self, e, kind="spam"):
        esc = lambda s: html.escape(s or "", quote=False)
        nom, organisation, _tel = self._report_complainant()
        quoi = "phishing email" if kind == "phish" else "unsolicited commercial email"
        corps = (
            "<p>Report of a %(quoi)s received at %(vise)s.</p>"
            "<ul><li>From: %(exp)s</li><li>Subject: %(objet)s</li>"
            "<li>Received: %(date)s</li>%(mid)s%(ip)s</ul>"
            "<p>The original message is attached (.eml). Reported by %(nom)s, "
            "%(org)s.</p>"
            "<pre style=\"font-family:monospace;font-size:12px;white-space:pre-wrap;\">"
            "%(entetes)s</pre>"
        ) % {
            "quoi": quoi,
            "vise": esc(e["affected"] or "my address"),
            "exp": esc(self.email_from),
            "objet": esc(self.subject),
            "date": esc(e["date_local"] or "unknown"),
            "mid": ("<li>Message-ID: %s</li>" % esc(e["message_id"])) if e["message_id"] else "",
            "ip": ("<li>Sending IP: %s</li>" % esc(e["sending_ip"])) if e["sending_ip"] else "",
            "nom": esc(nom),
            "org": esc(organisation),
            "entetes": "\n".join(esc(ligne) for ligne in e["headers"]),
        }
        objet = "%s: %s" % ("Phishing report" if kind == "phish" else "Spam report",
                            self.subject or e["sender_addr"])
        return objet, corps

    def _report_complaint_text(self, authority, kind="spam"):
        """Le texte brut à coller dans un formulaire web."""
        _objet, corps = self._report_complaint(authority, kind)
        texte = re.sub(r"<li>", "- ", corps)
        texte = re.sub(r"</p>|</li>|</pre>|<br\s*/?>", "\n", texte)
        texte = html.unescape(re.sub(r"<[^>]+>", "", texte))
        return re.sub(r"\n{3,}", "\n\n", texte).strip()


    # ------------------------------------------------------------------
    # Le même geste au téléphone
    # ------------------------------------------------------------------
    @api.model
    def _report_complaints_enabled(self, company, nombre):
        """La plainte est-elle OFFRABLE sur ``nombre`` courriel(s) de ``company``.

        🔴 Le drapeau de société ne suffit pas : sans projet de
        plaintes — qu'aucune contrainte Python n'exige, seulement le `required`
        de l'écran des réglages — ou sans le droit d'y créer une tâche,
        `_report_stage_complaint` LÈVE, et un refus annule toute la
        transaction : le rangement demandé est perdu avec la plainte. Mieux
        vaut ne pas offrir la plainte que la promettre et faire payer le geste
        entier.

        ⚠️ Ce que la garde des droits NE couvre pas : `has_access` sur un
        ensemble vide n'évalue que l'ACL de modèle — aucune `ir.rule` ne
        s'applique à un enregistrement qui n'existe pas encore. Sur une base
        réelle, l'ACL de modèle peut laisser passer un simple interne et c'est la
        RÈGLE D'ENREGISTREMENT de `project.task` qui refuse la création (vu sur une
        base d'essai). Ce refus ne se mesure qu'après l'INSERT : un
        usager qui n'est pas membre du projet des plaintes peut donc encore
        recevoir l'offre, et son geste entier est annulé avec elle. Le
        refermer demanderait de poser la plainte sous un point de sauvegarde
        et de la rapporter en échec au lieu de lever — hors de ce lot.
        """
        return bool(
            company.bf_email_complaints_enabled
            and company.bf_email_complaint_project_id
            and self.env["project.task"].browse().has_access("create")
            # Une plainte vise UN courriel : sur un lot, on range et on bloque.
            and nombre == 1)

    @api.model
    def mobile_report_options(self, email_ids):
        """Ce que l'appli peut offrir sur ces courriels, avant de les signaler.

        Le téléphone ne devine rien : le dossier Indésirables, l'offre de
        blocage, les plaintes et les autorités sont ANNONCÉS, comme
        ``spawn_kinds`` l'est pour les créations. Une appli qui sonderait la
        route de signalement pour voir si elle existe ne saurait pas
        distinguer un serveur plus ancien d'une panne de réseau.

        Les deux listes d'autorités sont rendues d'un coup, par nature :
        basculer Pourriel/Hameçonnage dans la feuille ne doit pas coûter un
        aller-retour, et c'est ce que la fenêtre du bureau fait en recalculant
        ``available_authority_ids``.
        """
        records = self._mobile_browse(email_ids)
        refus = records._report_refus()
        if refus:
            return {"ok": False, "blocked": refus}
        Authority = self.env["bf.email.report.authority"]
        company = records[:1].company_id or self.env.company
        # Une plainte vise UN courriel : sur un lot, on range et on bloque, on
        # ne fabrique pas quinze plaintes d'un geste (règle de la fenêtre).
        plaintes = self._report_complaints_enabled(company, len(records))

        def autorites(champ):
            if not plaintes:
                return []
            return [{
                "id": a.id,
                "name": a.name or "",
                "channel": a.channel,
                "url": a.url or "",
            } for a in Authority.search([
                (champ, "=", True),
                "|", ("company_id", "=", False), ("company_id", "=", company.id),
            ])]

        dossiers = []
        for compte in records.mapped("account_id"):
            junk = compte._junk_folder_name()
            if junk and junk not in dossiers:
                dossiers.append(junk)
        adresses = []
        for rec in records:
            # 🔴 `_report_sender_address()` (parseaddr), JAMAIS
            # `_external_address()` (la première chaîne qui RESSEMBLE à une
            # adresse dans l'en-tête). Avec
            # `From: "service@paypal.com" <x@phish.ru>`, la feuille proposait
            # « toujours ranger les courriels de service@paypal.com » et le
            # serveur créait la règle sur x@phish.ru : c'est la seule chose que
            # la personne voit avant de consentir. `_report_block_senders` lit
            # cette même méthode — annoncer et bloquer ne peuvent plus diverger.
            adresse = rec._report_sender_address()
            if adresse and "@" in adresse and adresse not in adresses:
                adresses.append(adresse)
        return {
            "ok": True,
            "count": len(records),
            # TOUTES les adresses distinctes, jamais les trois premières :
            # `_report_block_senders` crée une règle PAR adresse du lot, et une
            # troncature muette faisait consentir à moins que ce qui serait
            # posé. La liste est bornée par le nombre de courriels visés,
            # lui-même plafonné (`MAX_BULK_IDS`).
            "senders": adresses,
            # Vide : la ligne sortira de la boîte sans rien bouger au serveur.
            # L'appli le DIT dans la feuille plutôt que de laisser croire à un
            # rangement qui n'aura pas lieu.
            "junk_folder": ", ".join(dossiers),
            "phish_available": self._report_phish_available(),
            # Pas de dossier connu, pas de règle : une règle sans destination
            # archiverait au lieu de ranger (`_report_block_senders`).
            # 🔴 Et pas d'adresse, pas de règle non plus :
            # un `From` vide ou illisible rendait `senders: []` avec
            # `block_offered: true`, c'est-à-dire une case à cocher qui ne
            # pouvait rien poser. L'offre suit ce que `_report_block_senders`
            # fera, pas seulement le dossier.
            "block_offered": bool(dossiers and adresses),
            "complaints_enabled": plaintes,
            "authorities": {"spam": autorites("for_spam"), "phish": autorites("for_phish")},
        }

    @api.model
    def mobile_report(self, email_ids, kind="spam", block_sender=False,
                      authority_ids=None, grouped=True):
        """Signaler depuis l'appli, par la fenêtre du bureau elle-même.

        🔴 La fenêtre (`bf.email.report.wizard`) est CRÉÉE et confirmée, au
        lieu de rejouer ses trois gestes ici : c'est elle qui ordonne les
        pièces (plainte et .eml lus AVANT le rangement), qui refuse une
        autorité hors liste (`authority_ids & available_authority_ids`) et qui
        ne bloque pas sur un hameçonnage. Une deuxième implémentation aurait
        divergé de la première au premier correctif, et le téléphone est le
        chemin qu'on relit le moins.

        Rend le même compte rendu que la fenêtre, plus les textes des autorités
        par formulaire (la FTC n'a plus d'adresse) : au bureau ils s'affichent
        dans la fenêtre, ici ils voyagent dans la réponse.
        """
        records = self._mobile_browse(email_ids)
        refus = records._report_refus()
        if refus:
            raise UserError(refus)
        if kind not in ("spam", "phish"):
            raise UserError(_("Nature de signalement inconnue : %s", kind))
        # 🔴 L'hameçonnage sans `bf_security_awareness` ne signalait RIEN :
        # `_report_to_awareness` rend 0 en silence, le courriel était rangé
        # comme un pourriel et l'appli répondait `ok:true`. Les options
        # annoncent `phish_available` ; la route le vérifie, pour le client qui
        # ne l'a pas lu.
        if kind == "phish" and not self._report_phish_available():
            raise UserError(_(
                "Le signalement d'hameçonnage n'est pas disponible sur cette "
                "instance : signale-le comme pourriel."))
        # 🔴 Un scalaire est UNE autorité, comme dans `_mobile_browse` : la
        # chaîne « 12 » d'un POST form-encodé s'itérait caractère par caractère
        # et portait plainte aux autorités 1 ET 2, que personne n'avait cochées.
        # 🔴 Un booléen N'EST PAS un identifiant :
        # `isinstance(True, int)` vaut vrai en Python, donc `authority_ids:
        # true` passait la garde du scalaire, devenait `[True]`, puis
        # `int(True)` → 1 → un brouillon de plainte à l'autorité n° 1 que
        # personne n'avait cochée. Même trou que la chaîne « 12 », un autre
        # type. Refusé aux deux niveaux : le drapeau nu et le drapeau dans la
        # liste. (`_mobile_browse` porte la même garde pour `email_ids`.)
        if isinstance(authority_ids, bool):
            raise UserError(_("Liste d'autorités invalide."))
        if isinstance(authority_ids, (int, str)):
            authority_ids = [authority_ids]
        if authority_ids and not isinstance(authority_ids, (list, tuple)):
            raise UserError(_("Liste d'autorités invalide."))
        if any(isinstance(a, bool) for a in (authority_ids or [])):
            raise UserError(_("Liste d'autorités invalide."))
        try:
            ids = [int(a) for a in (authority_ids or [])]
        except (TypeError, ValueError):
            raise UserError(_("Liste d'autorités invalide."))
        # Bornée comme `email_ids` : chaque autorité
        # retenue prépare un brouillon de plainte sur une tâche, et rien ne
        # plafonnait la liste.
        if len(ids) > MAX_BULK_IDS:
            raise UserError(
                _("Trop d'autorités d'un coup (%(count)d, maximum %(max)d).")
                % {"count": len(ids), "max": MAX_BULK_IDS})
        # 🔴 Seules les autorités qui EXISTENT : `[(6, 0, [999999])]` lève une
        # IntegrityError au `create`, qui sortait en 500 — et un 500 dit à
        # l'appli de rejouer sans fin. Une autorité inconnue se laisse tomber,
        # comme une autorité hors liste (l'intersection avec
        # `available_authority_ids`).
        autorites = self.env["bf.email.report.authority"].browse(ids).exists()
        wiz = self.env["bf.email.report.wizard"].create({
            "email_ids": [(6, 0, records.ids)],
            "kind": kind,
            "block_sender": bool(block_sender),
            "authority_ids": [(6, 0, autorites.ids)],
        })
        # ⚠️ Les textes des formulaires AVANT `action_confirm` : il range les
        # courriels, et un texte de plainte se compose à partir du courriel.
        formulaires = []
        if len(records) == 1:
            for autorite in (wiz.authority_ids & wiz.available_authority_ids).filtered(
                    lambda a: a.channel == "form"):
                formulaires.append({
                    "name": autorite.name or "",
                    "url": autorite.url or "",
                    "text": records._report_complaint_text(autorite, kind),
                })
        retour = wiz.action_confirm()
        return {
            "ok": True,
            "message": ((retour or {}).get("infos") or {}).get("message", ""),
            # Ce que l'appli devra rendre à « Annuler » : la route /handle avec
            # handled=false repasse par `action_unhandle`, qui défait tout
            # (dossier, `report_kind`, visibilité).
            "ids": records.ids,
            "forms": formulaires,
            # 🔴 `grouped` jusqu'aux pastilles : la route comptait les
            # MESSAGES d'office, et l'appli en vue à plat affichait
            # « Boîte de réception · 6 » au-dessus de cinq lignes — l'incident
            # que MOBILE_API.md nomme déjà pour /counts et les routes de triage.
            "counts": self._mobile_counts(grouped=grouped),
        }


class ResCompanyReport(models.Model):
    _inherit = "res.company"

    bf_email_complaints_enabled = fields.Boolean(
        string="Plaintes de pourriel",
        help="Offre, dans la fenêtre « Signaler », de porter plainte auprès "
             "des autorités de la liste. Une plainte par courriel est préparée "
             "en brouillon sur une tâche du projet choisi ; rien ne part sans "
             "« Envoyer maintenant ».")
    bf_email_complaint_project_id = fields.Many2one(
        "project.project", string="Projet des plaintes",
        help="Chaque expéditeur visé y reçoit sa tâche « <Expéditeur> (spam) », "
             "qui porte la plainte et le courriel d'origine.")
