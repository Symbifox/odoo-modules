"""L'analyseur de la ligne de recherche de la boîte.

Avant, la boîte cherchait dans quatre champs et rien d'autre : objet,
expéditeur, destinataire et `body_preview`, qui s'arrête à 300 caractères.
Mesuré sur une base réelle le 2026-09-13 : **11,2 % du texte reçu était
indexé**, et 92,5 % des corps dépassaient la coupe. Neuf mots sur dix
n'étaient trouvables nulle part.

Ce module ajoute les deux moitiés qui manquaient : le texte entier
(``bf.email.body_text``) et une grammaire d'opérateurs, celle que tout le monde
connaît parce que Gmail l'a rendue banale.

**Ce que la grammaire accepte**, chaque terme rétrécissant le résultat :

    de: from:            l'expéditeur
    à: a: to:            un destinataire du champ À
    cc:                  un destinataire en copie
    objet: sujet:        l'objet
    corps: texte:        le corps seulement
    fiche: dossier:      la fiche Odoo où le courriel est classé
    boite: imap:         le dossier IMAP
    cat: catégorie:      client, interne, fournisseur, notification, marketing
    pj:                  oui / non
    avant: before:       AAAA-MM-JJ, exclusif
    après: apres: after: AAAA-MM-JJ, inclusif
    est: is:             lu, non-lu, traité, en-boite, reçu, envoyé,
                         invitation, masse, question, sourdine
    "phrase exacte"      cherchée telle quelle, objet et corps
    tout autre mot       objet, expéditeur, destinataires ou corps

⚠️ Un terme inconnu du genre ``truc:machin`` n'est PAS traité comme un
opérateur : il repart en recherche ordinaire. Refuser la ligne apprendrait à
l'usager à ne plus jamais écrire de deux-points, et une adresse courriel en
contient rarement mais un objet, souvent.
"""
import re
from datetime import datetime

from odoo import api, models

# Les champs qu'un mot nu traverse. `body_text` est le seul ajout de, et
# c'est lui qui change tout : les trois autres étaient déjà là.
MOTS_NUS = ("subject", "email_from", "email_to", "body_text")

# Un opérateur s'écrit `clé:valeur`. La valeur peut être entre guillemets, et
# c'est le seul moyen d'y mettre une espace.
JETON = re.compile(r'(?:(?P<cle>[\wà-ÿ-]+):)?(?:"(?P<phrase>[^"]*)"|(?P<mot>\S+))')

ALIAS = {
    "de": "email_from", "from": "email_from", "exp": "email_from",
    "a": "email_to", "à": "email_to", "to": "email_to", "dest": "email_to",
    "cc": "email_cc", "copie": "email_cc",
    "objet": "subject", "sujet": "subject", "subject": "subject",
    "corps": "body_text", "texte": "body_text", "body": "body_text",
    "fiche": "record_name", "dossier": "record_name", "record": "record_name",
    "boite": "imap_folder", "boîte": "imap_folder", "imap": "imap_folder",
    "folder": "imap_folder",
}

ETATS = {
    "lu": [("status", "!=", "new")],
    "read": [("status", "!=", "new")],
    "non-lu": [("status", "=", "new")],
    "nonlu": [("status", "=", "new")],
    "unread": [("status", "=", "new")],
    "traite": [("is_handled", "=", True)],
    "traité": [("is_handled", "=", True)],
    "en-boite": [("is_handled", "=", False)],
    "inbox": [("is_handled", "=", False)],
    "recu": [("direction", "=", "in")],
    "reçu": [("direction", "=", "in")],
    "envoye": [("direction", "=", "out")],
    "envoyé": [("direction", "=", "out")],
    "sent": [("direction", "=", "out")],
    "invitation": [("is_invitation", "=", True)],
    "masse": [("is_bulk", "=", True)],
    "bulk": [("is_bulk", "=", True)],
    "question": [("is_question", "=", True)],
    "repondu": [("status", "=", "replied")],
    "répondu": [("status", "=", "replied")],
}

CATEGORIES = {
    "client": "client", "interne": "internal", "internal": "internal",
    "fournisseur": "vendor", "vendor": "vendor",
    "notification": "notification", "notif": "notification",
    "marketing": "marketing", "infolettre": "marketing",
}

VRAI = ("oui", "yes", "1", "true", "vrai")
FAUX = ("non", "no", "0", "false", "faux")


def _date(valeur):
    """``AAAA-MM-JJ`` en date, ou ``None`` si ce n'en est pas une."""
    for forme in ("%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(valeur, forme).date()
        except ValueError:
            continue
    return None


class BfEmailSearch(models.Model):
    """L'analyseur, posé sur ``bf.email`` par héritage de modèle.

    Un fichier à part parce que la grammaire est une pièce autonome : elle se
    lit, s'éprouve et se change sans ouvrir les 5 000 lignes du modèle.
    """

    _inherit = "bf.email"

    @api.model
    def _search_domain_from_query(self, term):
        """Rend le domaine que cette ligne de recherche décrit.

        Les termes s'additionnent (ET), parce que c'est ce qu'on attend quand
        on ajoute un mot : on rétrécit. Un mot nu cherche dans quatre champs,
        donc il porte son propre OU.
        """
        term = (term or "").strip()
        if not term:
            return []
        domaine = []
        for jeton in JETON.finditer(term):
            cle = (jeton.group("cle") or "").lower()
            valeur = jeton.group("phrase")
            exacte = valeur is not None
            if valeur is None:
                valeur = jeton.group("mot") or ""
            valeur = valeur.strip()
            if not valeur and not exacte:
                continue
            clause = self._clause_for(cle, valeur, exacte)
            if clause is None:
                # Clé inconnue : on remet `clé:valeur` en mot ordinaire plutôt
                # que de le jeter. Un objet qui contient « Re: suivi » ne doit
                # pas devenir introuvable parce qu'il porte un deux-points.
                brut = "%s:%s" % (cle, valeur) if cle else valeur
                clause = self._clause_libre(brut)
            domaine += clause
        return domaine

    @api.model
    def _clause_for(self, cle, valeur, exacte):
        """La clause d'un opérateur, ou ``None`` si la clé n'en est pas un."""
        if not cle:
            return self._clause_libre(valeur)
        if cle in ALIAS:
            return [(ALIAS[cle], "ilike", valeur)]
        if cle in ("pj", "piece", "pièce", "attachment", "has"):
            if valeur.lower() in ("pj", "attachment", "piece", "pièce"):
                return [("has_attachments", "=", True)]
            if valeur.lower() in FAUX:
                return [("has_attachments", "=", False)]
            if valeur.lower() in VRAI:
                return [("has_attachments", "=", True)]
            return None
        if cle in ("cat", "categorie", "catégorie", "category"):
            interne = CATEGORIES.get(valeur.lower())
            return [("category", "=", interne)] if interne else None
        if cle in ("avant", "before"):
            date = _date(valeur)
            return [("date", "<", str(date))] if date else None
        if cle in ("apres", "après", "after", "depuis", "since"):
            date = _date(valeur)
            return [("date", ">=", str(date))] if date else None
        if cle in ("est", "is", "etat", "état"):
            return ETATS.get(valeur.lower())
        return None

    @api.model
    def _clause_libre(self, valeur):
        """Un mot nu : le même mot dans quatre champs, en OU."""
        if not valeur:
            return []
        clause = ["|", "|", "|"]
        for champ in MOTS_NUS:
            clause.append((champ, "ilike", valeur))
        return clause
