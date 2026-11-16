import logging

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import AccessError
from odoo.tools import html2plaintext

from ..tools import bridge, citations, identite, matching

_logger = logging.getLogger(__name__)

# Fields whose presence counts toward the completeness score.
# Companies are not expected to have a job title or a parent company.
# x_linkedin_url is deliberately NOT scored: adding a signal nobody has yet
# would drop every existing contact's percentage and flood "Contacts
# incomplets" with contacts that lost nothing.
#: Combien d'un corps de courriel part à l'analyse. Le texte envoyé au pont est
#: de toute façon coupé à 12 000 caractères après assemblage.
_CORPS_LU_MAX = 200000

#: Au-delà, ce n'est plus un nom : on n'en fait pas une consigne au pont.
_NOM_ATTENDU_MAX = 80
_MOTS_D_UN_NOM = 6

#: Signaux FAIBLES de la garde C, et ils ne portent QUE sur l'adresse : à deux,
#: une rue, une ville ou un code postal partagés ne sont plus un voisinage.
#: ⚠️ Le TITRE n'en fait pas partie, et c'est voulu : « Président » est le
#: quatrième titre le plus fréquent d'une base réelle, et « même titre + même
#: ville » arrive dans n'importe quelle PME régionale. Le titre se consigne
#: dans la note quand le retour est condamné par ailleurs, il ne condamne
#: jamais.
_FAIBLES_POUR_CONDAMNER = 2

#: Combien de mots significatifs doivent coïncider pour que deux raisons
#: sociales soient la même maison. ⚠️ L'égalité d'ensembles était trop
#: stricte : « Blue Fox Consultant » n'était pas « Blue Fox Inc. », un mot de
#: plus suffisait à s'échapper.
_MOTS_DE_SOCIETE_COMMUNS = 2


def _mots_de_societe(nom):
    """Les mots significatifs d'une raison sociale, en jeu comparable.

    ⚠️ `identite.nom` garde le suffixe légal : « Maison Essai » ne valait donc
    pas « Maison Essai inc. », et la garde C laissait passer la société de la
    maison écrite sans son « inc. ». `matching.significant_words`, déjà dans le
    module, retire les suffixes légaux et les mots de remplissage.
    """
    return frozenset(matching.significant_words(nom or ""))

_PERSON_CHECKS = ("email", "phone_or_mobile", "function", "company", "address", "website")
_COMPANY_CHECKS = ("email", "phone_or_mobile", "address", "website")


#: Abréviations de pays vues dans des cartes, des signatures et des vCard : codes ISO à trois
#: lettres (Odoo ne garde que les deux) et usages courants. Clé : en majuscules, sans points ni espaces.
_ALIAS_PAYS = {
    "USA": "US", "UK": "GB", "GBR": "GB", "CAN": "CA", "FRA": "FR", "DEU": "DE", "ESP": "ES", "ITA": "IT",
    "MEX": "MX", "NZL": "NZ", "AUS": "AU", "BEL": "BE", "CHE": "CH", "NLD": "NL", "PRT": "PT",
    "IRL": "IE", "JPN": "JP", "CHN": "CN", "IND": "IN", "BRA": "BR",
}


class ResPartner(models.Model):
    _inherit = "res.partner"

    x_enrichment_completeness = fields.Integer(
        string="Completeness",
        compute="_compute_enrichment_completeness",
        store=True,
        help="Percentage of contact details filled in (email, phone, job "
             "title, company, address, website).",
    )
    x_bf_enrich_queued = fields.Boolean(
        string="Queued for enrichment", default=False, copy=False,
        help="Flagged for background enrichment from the email signature "
             "(cron).",
    )
    x_linkedin_url = fields.Char(
        string="LinkedIn profile", copy=False,
        help="Profile URL (https://www.linkedin.com/in/…). Found by the "
             "search, and the key to reading the exact profile.",
    )

    @api.depends("email", "phone", "mobile", "function", "parent_id",
                 "company_name", "street", "city", "website", "is_company")
    def _compute_enrichment_completeness(self):
        for p in self:
            signals = {
                "email": bool(p.email),
                "phone_or_mobile": bool(p.phone or p.mobile),
                "function": bool(p.function),
                "company": bool(p.parent_id or p.company_name),
                "address": bool(p.street or p.city),
                "website": bool(p.website),
            }
            checks = _COMPANY_CHECKS if p.is_company else _PERSON_CHECKS
            p.x_enrichment_completeness = round(
                100 * sum(signals[c] for c in checks) / len(checks)
            )

    # ── Central apply path (used by every enrichment flow) ──────────

    def _apply_contact_vals(self, vals, source="enrichment", overwrite=False):
        """Write contact fields, by default only filling blanks.

        ``vals`` maps res.partner field names to values (m2o fields carry an
        id). Returns the list of fields actually changed and posts a chatter
        summary. Never clobbers a populated field unless ``overwrite`` is set.
        """
        self.ensure_one()
        to_write = {}
        applied = []
        for fname, value in vals.items():
            if value in (None, "", False):
                continue
            field = self._fields.get(fname)
            if field is None:
                continue
            current = self[fname]
            if field.type == "many2one":
                current_id = current.id if current else False
                if (overwrite or not current_id) and current_id != value:
                    to_write[fname] = value
                    applied.append(fname)
            else:
                if (overwrite or not current) and current != value:
                    to_write[fname] = value
                    applied.append(fname)
        if to_write:
            self.write(to_write)
            self.message_post(body=self._enrichment_log_html(source, applied))
        return applied

    def _enrichment_log_html(self, source, applied):
        """Le résumé posé au fil, en HTML réellement rendu comme du HTML.

        🔴 Rendre une chaîne ordinaire ici ne produit pas du HTML :
        ``message_post`` échappe ce qu'on lui donne et l'emballe dans un
        paragraphe, donc le fil affiche « <p>Contact enrichi… » en toutes
        lettres. Le défaut a dormi longtemps parce qu'il ne se voit que sur un
        contact enrichi, et qu'il n'y en avait qu'un.

        ``Markup`` échappe encore ce qu'on y substitue : un libellé de champ
        traduit qui porterait un chevron ne peut pas casser le fil.
        """
        labels = []
        for fname in applied:
            field = self._fields.get(fname)
            # 🔴 ``field.string`` est le texte SOURCE (anglais) : le libellé traduit
            # passe par ``_description_string(env)``, dans la langue du contexte.
            labels.append(field._description_string(self.env) if field else fname)
        items = Markup("").join(Markup("<li>%s</li>") % label
                                for label in labels)
        return Markup(_(
            "<p>Contact enriched via <b>%(source)s</b>:</p><ul>%(items)s</ul>"
        )) % {"source": source, "items": items}

    @api.model
    def _enrich_country_id(self, name):
        """Resolve a free-text country name/code to a res.country id (or False)."""
        if not name:
            return False
        # 🔴 Le pays se cherche en anglais D'ABORD, puis dans la langue du lecteur :
        # le pont rend « United States », et ``res.country.name`` se cherche dans
        # la langue du contexte (avec ``lang=fr_CA``, rien ne trouve « États-Unis »).
        # 🔴 ``res.country.code`` est un Char de 2 : Odoo tronque la valeur cherchée à 2
        # caractères, et « États-Unis » trouvait « ÉT » = l'Éthiopie. Le code ne se
        # cherche donc que sur une valeur de deux caractères.
        texte = name.strip()
        # Abréviations courantes qui ne sont ni un nom ni un code à deux lettres :
        # avant la correction du code tronqué, « USA » trouvait « US » par accident.
        texte = _ALIAS_PAYS.get(texte.upper().replace(".", "").replace(" ", ""), texte)
        domaine = [("name", "=ilike", texte)]
        if len(texte) == 2:
            domaine = ["|"] + domaine + [("code", "=ilike", texte)]
        for lang in dict.fromkeys(["en_US", self.env.context.get("lang") or "en_US"]):
            country = self.env["res.country"].with_context(lang=lang).search(
                domaine, limit=1)
            if country:
                return country.id
        return False

    # ── Wizard launchers (header / smart buttons) ───────────────────

    def action_scan_business_card(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Scan a business card"),
            "res_model": "bf.contact.card.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_target_partner_id": self.id},
        }

    def action_enrich_from_emails(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Enrich from emails"),
            "res_model": "bf.contact.enrich.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_partner_id": self.id, "default_source": "signature"},
        }

    def action_enrich_from_web(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Enrich from the website"),
            "res_model": "bf.contact.enrich.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_partner_id": self.id, "default_source": "web"},
        }

    def action_enrich_from_linkedin(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Enrich from LinkedIn"),
            "res_model": "bf.contact.enrich.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_partner_id": self.id, "default_source": "linkedin"},
        }

    # ── Signature enrichment core (shared: wizard, 1-click, batch cron) ──

    def _bf_signature_contexte(self, limit=5):
        """Le texte à lire, les boîtes qui l'ont fourni, et qui on attend.

        Rend ``(texte, sources, attendu)`` :

        - ``texte`` : les corps des derniers courriels REÇUS de ce contact,
          **historique cité retiré** — c'est la garde A, et elle se pose ici
          parce que c'est le seul endroit par lequel les trois chemins (fiche,
          assistant, lot) passent ;
        - ``sources`` : les lignes ``bf.email`` elles-mêmes. La garde C y lit à
          qui appartient la boîte — le compte IMAP quand il y en a un, et
          **toujours** le propriétaire de la ligne, le seul des deux à exister
          pour une ligne née du chatter ou de la passerelle ;
        - ``attendu`` : ``expected_email`` / ``expected_name``, que le pont
          ajoute à son prompt — la garde B.

        ``bf.email`` reflète l'IMAP, la passerelle et le chatter : les
        signatures venues d'IMAP sont donc déjà couvertes.
        """
        self.ensure_one()
        BfEmail = self.env["bf.email"]
        emails = BfEmail.search(
            [("partner_id", "=", self.id), ("direction", "=", "in")],
            order="date desc", limit=limit,
        )
        if not emails and self.email:
            # 🔴 `ilike` est une SOUS-CHAÎNE, et `_` y est un joker SQL : la
            # fiche de « ric@x.test » récupérait les courriels de
            # « patric@x.test », et « info_rh@x.test » ceux de
            # « info_rhXtest@x.test ». Lire le courriel de quelqu'un d'autre
            # est exactement le défaut qu'on corrige, par un autre chemin.
            # `=ilike` compare la valeur entière ; les jokers s'échappent.
            voulue = identite.adresse(self.email) or self.email.strip()
            for joker in ("\\", "%", "_"):
                voulue = voulue.replace(joker, "\\" + joker)
            emails = BfEmail.search(
                [("email_from", "=ilike", voulue), ("direction", "=", "in")],
                order="date desc", limit=limit,
            )
            if not emails:
                # Un en-tête « Nom <adresse> » ne vaut pas l'adresse seule.
                emails = BfEmail.search(
                    [("email_from", "=ilike", "%<" + voulue + ">"),
                     ("direction", "=", "in")],
                    order="date desc", limit=limit,
                )
        blocks = []
        for msg in emails:
            # Borné avant l'analyse : une signature vit dans les premiers
            # milliers de caractères, et un corps de plusieurs mégaoctets ne
            # doit pas se payer en temps d'analyse.
            body = (msg.body_html or "")[:_CORPS_LU_MAX]
            if body:
                # Deux étages, parce que les deux formes se croisent dans un
                # même corps : la structure (blockquote, gmail_quote…) puis les
                # marqueurs de texte (« Le … a écrit : », lignes en « > »).
                text = html2plaintext(citations.sans_citation_html(body))
            else:
                text = msg.body_preview or ""
            text = citations.sans_citation_texte(text)
            if text:
                blocks.append("----- %s -----\n%s" % (msg.date or "", text))
        return "\n\n".join(blocks)[:12000], emails, self._bf_attendu()

    def _bf_attendu(self):
        """``expected_email`` / ``expected_name`` pour le pont (garde B).

        ⚠️ Les deux valeurs se posent AU-DESSUS de messages non fiables, dans
        la partie « consignes » du prompt : elles viennent d'une fiche, donc
        d'un texte qu'un tiers a pu écrire (vCard, carte numérisée, formulaire
        du site). L'adresse est donc dégagée de son enveloppe, et un nom qui
        ressemble à une phrase plutôt qu'à un nom n'est pas envoyé. Le pont
        borne et dépouille encore ce qu'il reçoit.
        """
        self.ensure_one()
        attendu = {}
        courriel = identite.adresse(self.email)
        if courriel:
            attendu["expected_email"] = courriel
        # Un nom encore à l'état d'ébauche (vide, ou égal au courriel) n'apprend
        # rien au pont et pourrait l'induire : on ne l'envoie pas.
        nom = (self.name or "").strip()
        if (nom and nom != (self.email or "").strip()
                and len(nom) <= _NOM_ATTENDU_MAX
                and len(nom.split()) <= _MOTS_D_UN_NOM):
            attendu["expected_name"] = nom
        return attendu

    # ── Garde C : l'identité de la maison ne s'écrit pas sur la fiche d'un tiers ──

    def _bf_identite_maison(self, sources):
        """Ce qui appartient à la maison, pris sur les lignes qui ont fourni le texte.

        ``sources`` est un jeu de ``bf.email``. On en tire **le propriétaire**
        (``user_id``, qui existe toujours : le champ est requis) autant que le
        compte IMAP (``account_id``, vide pour une ligne née du chatter ou de
        la passerelle). 🔴 Ne lire que le compte laissait sans protection
        exactement le cas du défaut — le téléphone du propriétaire — dès que la
        ligne venait du chatter.

        Tout se lit en ``sudo()`` : ``bf.email.account`` est strictement
        propriétaire, sans dérogation pour l'administrateur du courriel, et un
        opérateur qui a le droit de LIRE la ligne tombait en ``AccessError``
        sur le compte. Seuls des champs d'identité sont lus, et aucun n'est
        montré.

        Rend des ensembles normalisés : ``numeros``, ``adresses``,
        ``domaines``, ``noms`` (personnes), ``societes`` (mots significatifs
        d'une raison sociale), et les quatre faibles ``titres``, ``rues``,
        ``villes``, ``cps``.
        """
        maison = {cle: set() for cle in (
            "numeros", "adresses", "domaines", "noms", "personnes", "societes",
            "titres", "rues", "villes", "cps")}
        sources = sources.sudo()
        comptes = sources.mapped("account_id")
        proprios = (sources.mapped("user_id.partner_id")
                    | comptes.mapped("user_id.partner_id"))
        for proprio in proprios:
            for valeur in (proprio.phone, proprio.mobile):
                maison["numeros"] |= identite.numeros(valeur)
            maison["adresses"].add(identite.adresse(proprio.email))
            maison["domaines"].add(identite.domaine(proprio.email))
            maison["noms"].add(identite.nom(proprio.name))
            maison["personnes"].add(identite.nom(proprio.name))
            maison["titres"].add(identite.nom(proprio.function))
            maison["rues"].add(identite.nom(proprio.street))
            maison["villes"].add(identite.nom(proprio.city))
            maison["cps"].add(identite.nom(proprio.zip))
        for compte in comptes:
            maison["adresses"].add(identite.adresse(compte.login))
            maison["domaines"].add(identite.domaine(compte.login))
        societes = (comptes.mapped("company_id")
                    | sources.mapped("user_id.company_id")
                    | self.env.company)
        for societe in societes:
            maison["numeros"] |= identite.numeros(societe.phone)
            maison["adresses"].add(identite.adresse(societe.email))
            maison["domaines"].add(identite.domaine(societe.email))
            maison["domaines"].add(identite.domaine(societe.website))
            maison["noms"].add(identite.nom(societe.name))
            maison["societes"].add(_mots_de_societe(societe.name))
            maison["rues"].add(identite.nom(societe.street))
            maison["villes"].add(identite.nom(societe.city))
            maison["cps"].add(identite.nom(societe.zip))
        return {cle: {v for v in valeurs if v} for cle, valeurs in maison.items()}

    def _bf_est_de_la_maison(self, sources, maison=None):
        """Vrai si CETTE fiche est celle de la maison (ou d'un des siens).

        🔴 Sans cette sortie, la garde C mordrait sur la fiche d'un employé qui
        porte, légitimement, le numéro et la société de la maison.
        """
        self.ensure_one()
        # 🔴 `user_ids` ne suffit pas : un client qui a un accès au portail en a
        # un. Seul un usager INTERNE (`share` à faux) est de la maison, sinon
        # la garde se désarmerait précisément sur les fiches clientes les plus
        # complètes.
        if self.sudo().user_ids.filtered(lambda u: not u.share):
            return True
        maison = maison if maison is not None else self._bf_identite_maison(sources)
        mon_domaine = identite.domaine(self.email)
        if mon_domaine and mon_domaine in maison["domaines"]:
            return True
        if identite.adresse(self.email) in maison["adresses"]:
            return True
        # ⚠️ `personnes` et non `noms` : `noms` porte aussi les raisons sociales,
        # et une fiche cliente homonyme d'une société de la maison aurait perdu
        # la garde C en entier.
        if identite.nom(self.name) in maison["personnes"]:
            return True
        societe = self.parent_id
        if not societe:
            return False
        mots = _mots_de_societe(societe.name)
        return bool(mots and any(
            len(mots & connue) >= min(_MOTS_DE_SOCIETE_COMMUNS,
                                      len(mots), len(connue))
            for connue in maison["societes"]))

    def _bf_ecarter_identite_maison(self, data, sources):
        """Refuse un retour du pont qui porte l'identité de la maison.

        Rend ``(data, ecartes)``. ``ecartes`` nomme les champs qui ont trahi le
        retour ; quand il y en a, ``data`` revient **vide**.

        🔴 Pourquoi tout, et pas seulement le champ fautif : une signature
        appartient à UNE personne. Si le mobile rendu est celui de la maison,
        c'est la signature de la maison qui a été lue, et sauver la ville ou le
        titre laisserait une fiche à moitié fausse. C'est l'état exact des deux
        fiches remises à vide le 2026-11-11 : titre, mobile et société d'une
        autre personne sur des contacts qui n'ont rien à voir.

        Deux niveaux de preuve, parce que les champs ne pèsent pas pareil :

        - **fort** — un seul condamne : numéro (comparé par ENSEMBLES, pour
          survivre à un poste téléphonique), adresse exacte, domaine du site ou
          du courriel, raison sociale (comparée par mots significatifs, donc
          « Maison Essai » vaut « Maison Essai inc. »), nom de la personne ;
        - **faible** — il en faut deux : titre, rue, ville, code postal. Seul,
          chacun est un voisinage ou un titre courant (« Président ») ; à deux,
          c'est une identité. 🔴 Sans ce second niveau, une signature maison
          sans numéro ni URL — « Camille Lavoie, Président, Maison Essai, 12
          rue des Essais » — passait entière.
        """
        self.ensure_one()
        if not data:
            return data, []
        maison = self._bf_identite_maison(sources)
        if self._bf_est_de_la_maison(sources, maison=maison):
            return data, []

        forts = []
        for champ in ("phone", "mobile"):
            if identite.numeros(data.get(champ)) & maison["numeros"]:
                forts.append((champ, data.get(champ)))
        if identite.adresse(data.get("email")) in maison["adresses"]:
            forts.append(("email", data.get("email")))
        for champ in ("website", "email"):
            domaine = identite.domaine(data.get(champ))
            if domaine and domaine in maison["domaines"]:
                forts.append((champ, data.get(champ)))
        mots = _mots_de_societe(data.get("company"))
        if mots and any(
                len(mots & connue) >= min(_MOTS_DE_SOCIETE_COMMUNS,
                                          len(mots), len(connue))
                for connue in maison["societes"]):
            forts.append(("company", data.get("company")))
        nom_rendu = identite.nom(data.get("full_name") or " ".join(
            filter(None, [data.get("first_name"), data.get("last_name")])))
        if nom_rendu and nom_rendu in maison["noms"]:
            forts.append(("full_name", data.get("full_name")))

        faibles = []
        for champ, cle in (("street", "rues"), ("city", "villes"),
                           ("zip", "cps")):
            valeur = identite.nom(data.get(champ))
            if valeur and valeur in maison[cle]:
                faibles.append((champ, data.get(champ)))
        # Le titre ne condamne pas ; il se nomme dans la note s'il y a refus.
        titre = identite.nom(data.get("function"))
        pour_memoire = ([("function", data.get("function"))]
                        if titre and titre in maison["titres"] else [])

        if not forts and len(faibles) < _FAIBLES_POUR_CONDAMNER:
            return data, []
        vus = []
        for champ, valeur in forts + faibles + pour_memoire:
            if champ not in [c for c, _v in vus]:
                vus.append((champ, valeur))
        _logger.warning(
            "Signature enrichment refused for partner %s: the reading carries "
            "the house identity (%s)", self.id,
            ", ".join("%s=%r" % (c, v) for c, v in vus))
        self.message_post(body=self._bf_journal_identite_maison(vus))
        return {}, [champ for champ, _valeur in vus]

    def _bf_journal_identite_maison(self, ecartes):
        """La note au fil : ce qui a été refusé, et pourquoi. Jamais en silence."""
        items = Markup("").join(
            Markup("<li>%s : %s</li>") % (champ, valeur or "")
            for champ, valeur in ecartes)
        return Markup(_(
            "<p>Signature reading <b>refused</b>: it carries the identity of "
            "this Odoo's own mailbox or company, so it belongs to the quoted "
            "history rather than to this contact. Nothing was written.</p>"
            "<ul>%(items)s</ul>"
        )) % {"items": items}

    def _bf_enrich_from_signature(self, min_confidence=60):
        """Fetch this partner's signature data from the bridge and blank-fill it.

        Returns a status: enriched | nothing_new | no_email | low_confidence |
        error. Never clobbers populated fields; logs applied fields to chatter.
        """
        self.ensure_one()
        text, sources, attendu = self._bf_signature_contexte()
        if not text.strip() and self._bf_moisson_permise():
            # La fenêtre ingérée ne porte rien de cette personne : on va
            # chercher un ou deux de ses messages dans la boîte (la correction du 2026-11-11 § 5).
            if self._bf_moissonner_par_adresse():
                text, sources, attendu = self._bf_signature_contexte()
        if not text.strip():
            return "no_email"
        try:
            result = bridge.call_bridge(
                self.env, "/enrich/signature",
                dict(attendu, text=text), timeout=90)
        except Exception:  # noqa: BLE001 — surface as a status, never raise in batch
            _logger.exception("Signature enrichment bridge call failed for partner %s", self.id)
            return "error"
        if result.get("error"):
            return "error"
        data = result.get("data") or {}
        if (data.get("confidence") or 0) < min_confidence:
            return "low_confidence"
        # Garde C : la ceinture. Elle rattrape ce que la coupe de l'historique
        # (A) et la consigne au pont (B) auraient laissé passer.
        data, ecartes = self._bf_ecarter_identite_maison(data, sources)
        if ecartes:
            return "house_identity"

        Partner = self.env["res.partner"]
        vals = {
            "function": data.get("function"),
            "email": data.get("email"),
            "phone": data.get("phone"),
            "mobile": data.get("mobile"),
            "website": data.get("website"),
            "street": data.get("street"),
            "city": data.get("city"),
            "zip": data.get("zip"),
            "country_id": Partner._enrich_country_id(data.get("country")),
        }
        if data.get("company"):
            vals["company_name"] = data["company"]
        vals = {k: v for k, v in vals.items() if v}
        applied = self._apply_contact_vals(vals, source=_("email signature"))

        # Replace a stub name (blank or = email) with the real one.
        proposed = data.get("full_name") or " ".join(
            filter(None, [data.get("first_name"), data.get("last_name")])) or ""
        if proposed:
            new_name = proposed.strip()
            if (self.name or "").strip() in ("", (self.email or "").strip()) \
                    and new_name and self.name != new_name:
                self.name = new_name
                self.message_post(
                    body=_("Name completed from the signature: %s") % new_name)
                applied = list(applied) + ["name"]
        return "enriched" if applied else "nothing_new"

    def action_bf_enrich_signature_direct(self):
        """One-click on the contact form: enrich from signatures (blank-fill)."""
        self.ensure_one()
        status = self._bf_enrich_from_signature()
        msgs = {
            "enriched": _("Contact enriched from email signatures."),
            "nothing_new": _("No empty field to fill in from the signature."),
            "no_email": _("No incoming email found for this contact."),
            "low_confidence": _("Signature not reliable enough. Nothing "
                                "applied."),
            "house_identity": _("The signature read is your own mailbox's, "
                                "quoted in the thread. Nothing applied."),
            "error": _("Enrichment failed (service unavailable)."),
        }
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Enrichment"),
                "message": msgs.get(status, status),
                "type": "success" if status == "enriched" else "warning",
                "sticky": False,
            },
        }

    def action_bf_queue_signature_enrich(self):
        """List action: queue the selected contacts for background enrichment."""
        if not self.env.user.has_group(
                "bf_contact_enrichment.group_bf_contact_enrich"):
            raise AccessError(
                _("Access denied: the “Contact Enrichment” group is required."))
        self.write({"x_bf_enrich_queued": True})
        cron = self.env.ref(
            "bf_contact_enrichment.ir_cron_bf_enrich_signatures",
            raise_if_not_found=False)
        if cron:
            cron.sudo()._trigger()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Batch enrichment"),
                "message": _("%d contact(s) queued; processed in the "
                             "background.")
                % len(self),
                "type": "success",
                "sticky": False,
            },
        }

    @api.model
    def _cron_bf_enrich_signatures(self, chunk=15):
        """Process queued contacts in chunks; re-trigger if any remain."""
        queued = self.search([("x_bf_enrich_queued", "=", True)], limit=chunk)
        if not queued:
            return
        for partner in queued:
            try:
                partner._bf_enrich_from_signature()
            except Exception:  # noqa: BLE001
                _logger.exception(
                    "Batch signature enrichment failed for partner %s", partner.id)
            # Clear the flag even on failure so a bad row can't loop forever.
            partner.x_bf_enrich_queued = False
            self.env.cr.commit()  # checkpoint each contact
        if self.search_count([("x_bf_enrich_queued", "=", True)]):
            cron = self.env.ref(
                "bf_contact_enrichment.ir_cron_bf_enrich_signatures",
                raise_if_not_found=False)
            if cron:
                cron.sudo()._trigger()
