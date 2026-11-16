"""Les trois gardes de la lecture de signature : citation, destinataire, maison.

Le défaut, mesuré sur une base réelle le 2026-11-11 : deux fiches sur 23
enrichies ont reçu la signature du propriétaire de la boîte — titre, mobile,
société — parce que l'expéditeur n'avait pas signé sa réponse. La seule
signature du texte envoyé au pont était celle citée en historique, et le modèle
a rendu celle qu'il voyait, avec 88 et 90 de confiance : le seuil de 60 ne
filtrait rien. Exposition sur le même corpus : 39 des 528 expéditeurs (7,4 %)
n'ont que des réponses et portent la signature d'un tiers dans leur citation.

Trois gardes, donc trois objets d'essai :

A. le texte envoyé au pont n'a plus d'historique cité — et ce qui ENTOURE la
   citation survit, parce qu'une réponse Outlook signe parfois en dessous ;
B. la charge dit au pont de qui il attend la signature ;
C. un retour qui porte l'identité de la maison est refusé sur la fiche d'un
   tiers, consigné au fil, et laissé passer sur la fiche d'un des siens.

Le pont est simulé : ce qui s'éprouve ici n'est pas le modèle, c'est ce que le
module lui envoie et ce qu'il fait de sa réponse.
"""
from unittest.mock import patch

from odoo import fields, tools
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

BRIDGE = "odoo.addons.bf_contact_enrichment.tools.bridge.call_bridge"
_IMAP = "odoo.addons.bf_email_management.models.bf_email_imap"
ENTETES = _IMAP + ".fetch_headers_bulk"
DOSSIERS = _IMAP + ".list_folders"
SELECT = _IMAP + ".select_folder"
FETCH = _IMAP + ".fetch_rfc822"

#: Le numéro et la société de la maison, tels qu'une citation les porte.
TEL_MAISON = "418 555-0188"
MOBILE_MAISON = "418 555-0199"
NOM_MAISON = "Maison Essai inc."
DOMAINE_MAISON = "maison-essai.test"

#: La signature qui traîne dans l'historique cité : celle de la maison.
SIGNATURE_CITEE = (
    "Camille Lavoie<br/>Président, %s<br/>"
    "Bureau %s | Cell. %s<br/>%s"
) % (NOM_MAISON, TEL_MAISON, MOBILE_MAISON, DOMAINE_MAISON)

#: Ce que le pont rendait le 2026-11-11 : la signature de la maison, avec une
#: belle confiance, sur la fiche de quelqu'un d'autre.
RETOUR_CONTAMINE = {
    "full_name": "Camille Lavoie",
    "function": "Président",
    "company": NOM_MAISON,
    "phone": TEL_MAISON,
    "mobile": MOBILE_MAISON,
    "website": DOMAINE_MAISON,
    "confidence": 90,
}

#: Ce que le pont rend quand la signature attendue est bien là.
RETOUR_PROPRE = {
    "full_name": "Thibault Delacroix",
    "function": "Directeur général",
    "company": "Delacroix Conseil",
    "phone": "514 555-0123",
    "mobile": "514 555-0124",
    "website": "delacroix-conseil.test",
    "city": "Rimouski",
    "confidence": 88,
}

#: Ce que le pont doit rendre quand il ne trouve que la signature d'un tiers :
#: tout à null, confiance basse (la garde B lui demande exactement ça).
RETOUR_VIDE = {
    "full_name": None, "function": None, "company": None, "phone": None,
    "mobile": None, "website": None, "confidence": 10,
}


@tagged("post_install", "-at_install")
class EssaiSignatureCitee(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["ir.config_parameter"].sudo().set_param(
            # La moisson a son propre essai : ici, aucun appel IMAP ne doit
            # partir parce qu'un corps s'est trouvé vide.
            "bf_contact_enrichment.moisson_imap", "False")
        cls.maison = cls.env["res.company"].create({
            "name": NOM_MAISON,
            "email": "info@%s" % DOMAINE_MAISON,
            "phone": TEL_MAISON,
            "website": "https://%s" % DOMAINE_MAISON,
        })
        cls.proprio = cls.env["res.users"].create({
            "name": "Camille Lavoie",
            "login": "camille@%s" % DOMAINE_MAISON,
            "company_id": cls.maison.id,
            "company_ids": [(6, 0, [cls.maison.id])],
        })
        cls.proprio.partner_id.write({
            "function": "Président",
            "phone": TEL_MAISON,
            "mobile": MOBILE_MAISON,
            "email": "camille@%s" % DOMAINE_MAISON,
        })
        cls.compte = cls.env["bf.email.account"].create({
            "name": "Boîte de la maison",
            "user_id": cls.proprio.id,
            "company_id": cls.maison.id,
            "host": "imap.exemple.test",
            "port": 993,
            "login": "camille@%s" % DOMAINE_MAISON,
            "auth_mode": "password",
            "password": "mot-de-passe-d-essai",
            # ⛔ Rien ne doit sortir vers un serveur pendant un essai.
            "writeback_archive": False,
        })

    # ── Outils ──────────────────────────────────────────────────────

    def _contact(self, nom="Thibault Delacroix", courriel="info@delacroix-conseil.test"):
        return self.env["res.partner"].create({
            "name": nom, "email": courriel, "is_company": False})

    def _courriel(self, partner, corps_html, sujet="Re: Rencontre devancée ?"):
        return self.env["bf.email"].create({
            "subject": sujet,
            "email_from": "%s <%s>" % (partner.name, partner.email),
            "body_html": corps_html,
            "direction": "in",
            "date": "2026-11-10 14:05:00",
            "user_id": self.proprio.id,
            "account_id": self.compte.id,
            "partner_id": partner.id,
        })

    @staticmethod
    def _corps_reponse_non_signee():
        """Une réponse de deux mots, et la signature d'un tiers dans la citation."""
        return (
            "<div>Oui, 13 h 30 me convient. Merci!</div>"
            '<div class="gmail_quote">'
            "<div>Le lun. 10 nov. 2026 à 09:12, Camille Lavoie "
            "&lt;camille@%s&gt; a écrit :</div>"
            "<blockquote>Bonjour, est-ce qu'on peut devancer à 13 h 30 ?<br/>"
            "%s</blockquote></div>"
        ) % (DOMAINE_MAISON, SIGNATURE_CITEE)

    # ── Garde A ─────────────────────────────────────────────────────

    def test_a_la_citation_ne_part_pas_au_pont(self):
        """🔴 Le défaut d'origine : la signature citée arrivait au pont.

        Elle n'y arrive plus, et ce qui est de l'expéditeur — ses deux mots —
        y arrive toujours.
        """
        partner = self._contact()
        self._courriel(partner, self._corps_reponse_non_signee())
        with patch(BRIDGE, return_value={"data": dict(RETOUR_VIDE)}) as pont:
            statut = partner._bf_enrich_from_signature()
        charge = pont.call_args[0][2]
        self.assertNotIn("555-0199", charge["text"])
        self.assertNotIn("555-0188", charge["text"])
        self.assertNotIn(NOM_MAISON, charge["text"])
        self.assertNotIn("Camille", charge["text"])
        self.assertIn("13 h 30", charge["text"])
        # Confiance 10 : rien n'est appliqué, et surtout rien n'est écrit.
        self.assertEqual(statut, "low_confidence")
        self.assertFalse(partner.phone)
        self.assertFalse(partner.mobile)
        self.assertFalse(partner.function)
        self.assertFalse(partner.company_name)

    def test_a_la_signature_de_l_expediteur_passe(self):
        """Même forme, mais l'expéditeur signe : sa signature doit passer."""
        partner = self._contact()
        self._courriel(partner, (
            "<div>Oui, 13 h 30 me convient. Merci!</div>"
            "<div>Thibault Delacroix<br/>Directeur général, Delacroix Conseil<br/>"
            "514 555-0123</div>"
            '<div class="gmail_quote"><blockquote>Bonjour…<br/>%s'
            "</blockquote></div>"
        ) % SIGNATURE_CITEE)
        with patch(BRIDGE, return_value={"data": dict(RETOUR_PROPRE)}) as pont:
            statut = partner._bf_enrich_from_signature()
        charge = pont.call_args[0][2]
        self.assertIn("Delacroix Conseil", charge["text"])
        self.assertIn("514 555-0123", charge["text"])
        self.assertNotIn("555-0199", charge["text"])
        self.assertEqual(statut, "enriched")
        self.assertEqual(partner.phone, "514 555-0123")
        self.assertEqual(partner.function, "Directeur général")
        self.assertEqual(partner.company_name, "Delacroix Conseil")

    def test_a_signature_sous_la_citation_outlook(self):
        """⚠️ Outlook signe AU-DESSUS, et son corps cité suit un en-tête FRÈRE.

        🔴 Deux pièges dans la même forme. La première version visait
        ``id="appendonly"`` — un identifiant qui n'existe pas : Outlook écrit
        ``appendonsend``, et le corpus réel en portait 25 contre 0. Et retirer
        l'en-tête ``divRplyFwdMsg`` seul laissait le corps cité, qui est son
        FRÈRE, tout en effaçant le seul marqueur que l'étage texte aurait vu.
        Le défaut survivait donc intact sur la forme la plus courante d'OWA.
        """
        partner = self._contact()
        self._courriel(partner, (
            '<div class="elementToProof">C\'est noté.</div>'
            '<div id="Signature">Thibault Delacroix | Directeur général<br/>'
            "Delacroix Conseil | 514 555-0123</div>"
            '<div id="appendonsend"></div><hr>'
            '<div id="divRplyFwdMsg"><b>De :</b> Camille Lavoie '
            "&lt;camille@%s&gt;<br/><b>Objet :</b> Rencontre</div>"
            "<div>%s</div>"
        ) % (DOMAINE_MAISON, SIGNATURE_CITEE))
        with patch(BRIDGE, return_value={"data": dict(RETOUR_PROPRE)}) as pont:
            partner._bf_enrich_from_signature()
        charge = pont.call_args[0][2]
        self.assertIn("514 555-0123", charge["text"])
        self.assertIn("Delacroix Conseil", charge["text"])
        self.assertNotIn("555-0199", charge["text"])
        self.assertNotIn("Camille", charge["text"])

    def test_a_la_forme_QUE_la_base_stocke_vraiment(self):
        """🔴 Le piège qui rendait les 23 essais verts sur un chemin cassé.

        Tous écrivaient du HTML à la main. Or ``bf.email.body_html`` vaut
        ``mail.message.body`` dès qu'il y a un message — 19 % des lignes
        entrantes d'un corpus réel —, c'est-à-dire du HTML passé par
        ``html_sanitize``. Et ``html_sanitize`` marque
        ``data-o-mail-quote="1"`` sur **les signatures** autant que sur les
        citations : ``#Signature``, ``gmail_signature``, le motif ``-- <br>``.
        Un sélecteur sur cet attribut détruisait donc la signature de
        l'expéditeur — mesuré sur un corpus réel : 149 lignes sur 212, et 143
        numéros de téléphone perdus que la version d'avant gardait.
        """
        partner = self._contact()
        self._courriel(partner, tools.html_sanitize(
            "<div dir='ltr'>Oui, c'est bon.<div>--<br>Thibault Delacroix<br>"
            "Directeur général, Delacroix Conseil<br>514 555-0123</div></div>"
            "<div class='gmail_quote'><blockquote>Bonjour…<br/>%s"
            "</blockquote></div>" % SIGNATURE_CITEE))
        texte, _sources, _attendu = partner._bf_signature_contexte()
        self.assertIn("514 555-0123", texte,
                      "la signature de l'expéditeur, marquée par Odoo comme "
                      "une citation, doit rester")
        self.assertIn("Delacroix Conseil", texte)
        self.assertNotIn("555-0199", texte, "la citation doit partir")
        self.assertNotIn("Camille", texte)

    def test_a_citation_en_texte_brut(self):
        """Sans HTML, la citation se coupe aux marqueurs et aux « > »."""
        partner = self._contact()
        self.env["bf.email"].create({
            "subject": "Re: Témoignage",
            "email_from": partner.email,
            "body_html": (
                "<pre>Merci, c'est parfait.\n\n"
                "Le 10 novembre 2026 à 09:12, Camille Lavoie a écrit :\n"
                "&gt; Bonjour,\n&gt; Camille Lavoie\n&gt; Cell. %s\n</pre>"
            ) % MOBILE_MAISON,
            "direction": "in",
            "date": "2026-11-10 15:00:00",
            "user_id": self.proprio.id,
            "account_id": self.compte.id,
            "partner_id": partner.id,
        })
        with patch(BRIDGE, return_value={"data": dict(RETOUR_VIDE)}) as pont:
            partner._bf_enrich_from_signature()
        charge = pont.call_args[0][2]
        self.assertIn("c'est parfait", charge["text"])
        self.assertNotIn("555-0199", charge["text"])
        self.assertNotIn("Camille", charge["text"])

    def test_a_la_citation_seule_a_la_racine(self):
        """🔴 Le cas que la première version laissait passer EN ENTIER.

        ``extract_body`` retire l'enveloppe ``<body>`` avant de stocker : tout
        ``body_html`` venu d'IMAP est un fragment nu. Une réponse sans texte
        propre — la forme même du défaut — n'a donc qu'UN élément de tête, la
        citation, et ``lxml`` rendait cet élément comme racine. Le retirer puis
        sérialiser le même objet rendait la citation intacte.
        """
        partner = self._contact()
        self._courriel(partner, (
            "<blockquote>Bonjour, est-ce qu'on peut devancer à 13 h 30 ?<br/>"
            "%s</blockquote>") % SIGNATURE_CITEE)
        with patch(BRIDGE, return_value={"data": dict(RETOUR_VIDE)}) as pont:
            statut = partner._bf_enrich_from_signature()
        # Rien d'utile ne reste : la citation était tout le corps.
        self.assertEqual(statut, "no_email")
        self.assertFalse(pont.called, "le pont ne doit même pas être appelé")
        self.assertFalse(partner.phone)
        self.assertFalse(partner.function)

    def test_a_la_citation_seule_sous_chaque_forme(self):
        """Les quatre formes de citation, seules à la racine du corps."""
        formes = (
            "<blockquote>x<br/>%s</blockquote>",
            '<blockquote type="cite">x<br/>%s</blockquote>',
            '<div class="gmail_quote">x<br/>%s</div>',
            '<div id="divRplyFwdMsg">De : Camille<br/>%s</div>',
        )
        for gabarit in formes:
            partner = self._contact(courriel="x%d@delacroix-conseil.test"
                                             % formes.index(gabarit))
            self._courriel(partner, gabarit % SIGNATURE_CITEE)
            texte, _sources, _attendu = partner._bf_signature_contexte()
            self.assertNotIn("555-0199", texte, gabarit)
            self.assertNotIn("Camille", texte, gabarit)

    def test_a_un_corps_legitime_garde_sa_signature(self):
        """⚠️ Deux lignes d'en-tête ne font pas une citation.

        « Date : … » puis « Objet : … » au milieu d'un corps ordinaire — un
        accusé de formulaire, un ordre du jour — faisait couper, et c'est la
        signature de l'expéditeur qu'on perdait.
        """
        partner = self._contact()
        self._courriel(partner, (
            "<div>Bonjour,</div><div>Voici les détails :</div>"
            "<div>Date : 20 novembre</div><div>Objet : revue trimestrielle</div>"
            "<div><br/>Thibault Delacroix<br/>Directeur général<br/>"
            "514 555-0123</div>"), sujet="Détails de la revue")
        with patch(BRIDGE, return_value={"data": dict(RETOUR_PROPRE)}) as pont:
            partner._bf_enrich_from_signature()
        charge = pont.call_args[0][2]
        self.assertIn("514 555-0123", charge["text"])
        self.assertIn("Directeur général", charge["text"])

    def test_a_un_homonyme_plus_long_n_est_pas_lu(self):
        """🔴 ``ilike`` est une sous-chaîne, et ``_`` un joker SQL.

        La fiche de « ric@… » lisait les courriels de « patric@… » : lire le
        courriel de quelqu'un d'autre, c'est le défaut d'à côté.
        """
        voisin = self._contact(nom="Patrice Sissoko",
                              courriel="patric@delacroix-conseil.test")
        courriel = self._courriel(voisin, (
            "<div>Voici le document.</div><div>Patrice Sissoko<br/>"
            "Associé<br/>514 555-0001</div>"))
        # La ligne n'est rattachée qu'au voisin : c'est le repli par adresse
        # qui doit discriminer.
        courriel.partner_id = voisin.id
        ric = self._contact(nom="Ric Sissoko", courriel="ric@delacroix-conseil.test")
        texte, sources, _attendu = ric._bf_signature_contexte()
        self.assertEqual(texte, "")
        self.assertFalse(sources)

    # ── Garde B ─────────────────────────────────────────────────────

    def test_b_la_charge_dit_de_qui_on_attend_la_signature(self):
        """Sans ça, « ignorer les citations » n'a plus de candidat à extraire."""
        partner = self._contact()
        self._courriel(partner, self._corps_reponse_non_signee())
        with patch(BRIDGE, return_value={"data": dict(RETOUR_VIDE)}) as pont:
            partner._bf_enrich_from_signature()
        charge = pont.call_args[0][2]
        self.assertEqual(charge["expected_email"], "info@delacroix-conseil.test")
        self.assertEqual(charge["expected_name"], "Thibault Delacroix")

    def test_b_un_nom_d_ebauche_ne_part_pas(self):
        """Un nom encore égal au courriel n'apprend rien au pont."""
        partner = self._contact(nom="info@delacroix-conseil.test")
        self._courriel(partner, self._corps_reponse_non_signee())
        with patch(BRIDGE, return_value={"data": dict(RETOUR_VIDE)}) as pont:
            partner._bf_enrich_from_signature()
        charge = pont.call_args[0][2]
        self.assertEqual(charge["expected_email"], "info@delacroix-conseil.test")
        self.assertNotIn("expected_name", charge)

    # ── Garde C ─────────────────────────────────────────────────────

    def test_c_identite_de_la_maison_refusee_sur_un_tiers(self):
        """🔴 La ceinture : même si A et B avaient laissé passer, rien ne s'écrit.

        C'est cette garde qui aurait évité les deux fiches du 2026-11-11.
        """
        partner = self._contact()
        self._courriel(partner, self._corps_reponse_non_signee())
        avant = len(partner.message_ids)
        with patch(BRIDGE, return_value={"data": dict(RETOUR_CONTAMINE)}):
            statut = partner._bf_enrich_from_signature()
        self.assertEqual(statut, "house_identity")
        self.assertFalse(partner.phone)
        self.assertFalse(partner.mobile)
        self.assertFalse(partner.function)
        self.assertFalse(partner.company_name)
        self.assertFalse(partner.website)
        self.assertEqual(partner.name, "Thibault Delacroix")
        # ⚠️ Et ça se dit : une valeur écartée en silence est une valeur qu'on
        # ne saura jamais avoir refusée.
        self.assertGreater(len(partner.message_ids), avant)
        note = partner.message_ids[0].body
        # ⚠️ Rien sur le texte anglais : la note passe par `_()` et le
        # catalogue fr_CA la traduit, donc chercher « refused » rendait
        # l'essai ROUGE sur une base francophone — c'est-à-dire sur toutes
        # celles qui portent ce module.
        self.assertIn(MOBILE_MAISON, note, "la valeur écartée doit être nommée")
        # 🔴 Et la note doit être du HTML RENDU, pas du balisage échappé :
        # `message_post` échappe ce qu'on lui donne, et le module a déjà
        # livré ce défaut une fois (18.0.2.2.1).
        self.assertTrue(note.strip().startswith("<p>"), note[:60])
        self.assertIn("<li>", note)
        self.assertNotIn("&lt;", note)

    def test_c_ne_mord_pas_sur_la_fiche_d_un_des_siens(self):
        """Un employé porte légitimement le numéro et la société de la maison."""
        employe = self._contact(nom="Jo Tremblay",
                                courriel="jo@%s" % DOMAINE_MAISON)
        self._courriel(employe, self._corps_reponse_non_signee())
        with patch(BRIDGE, return_value={"data": dict(RETOUR_CONTAMINE)}):
            statut = employe._bf_enrich_from_signature()
        self.assertEqual(statut, "enriched")
        self.assertEqual(employe.phone, TEL_MAISON)
        self.assertEqual(employe.company_name, NOM_MAISON)

    def test_c_l_assistant_refuse_aussi(self):
        """Le chemin de l'usager porte les mêmes gardes que le lot."""
        partner = self._contact()
        self._courriel(partner, self._corps_reponse_non_signee())
        assistant = self.env["bf.contact.enrich.wizard"].create({
            "partner_id": partner.id, "source": "signature"})
        with patch(BRIDGE, return_value={"data": dict(RETOUR_CONTAMINE)}):
            with self.assertRaises(UserError):
                assistant.action_analyze()
        self.assertFalse(partner.phone)
        self.assertFalse(partner.company_name)

    def test_c_la_societe_sans_son_inc_condamne_aussi(self):
        """⚠️ « Maison Essai » est « Maison Essai inc. » : le suffixe légal ne
        doit pas sauver un retour contaminé."""
        partner = self._contact()
        self._courriel(partner, self._corps_reponse_non_signee())
        retour = {"company": "Maison Essai", "confidence": 90}
        with patch(BRIDGE, return_value={"data": retour}):
            statut = partner._bf_enrich_from_signature()
        self.assertEqual(statut, "house_identity")
        self.assertFalse(partner.company_name)

    def test_c_un_numero_avec_un_poste_condamne_aussi(self):
        """⚠️ Un poste téléphonique ne doit pas désarmer le signal fort."""
        partner = self._contact()
        self._courriel(partner, self._corps_reponse_non_signee())
        retour = {"mobile": "%s poste 42" % MOBILE_MAISON, "confidence": 90}
        with patch(BRIDGE, return_value={"data": retour}):
            statut = partner._bf_enrich_from_signature()
        self.assertEqual(statut, "house_identity")
        self.assertFalse(partner.mobile)

    def test_c_un_poste_colle_sans_mot_condamne_aussi(self):
        """🔴 Trou trouvé par la mutation : « poste 42 » ne prouvait rien.

        Le mot « poste » coupe la suite de chiffres, donc le numéro survit
        intact et la garde mord sans l'aide de la branche qui garde la TÊTE.
        Un poste écrit **sans mot** — « 418 555-0199 221 » — se lit comme UNE
        suite : la queue devient `5550199221`, et seule la tête ressemble
        encore au numéro de la maison.
        """
        partner = self._contact()
        self._courriel(partner, self._corps_reponse_non_signee())
        retour = {"mobile": "%s 221" % MOBILE_MAISON, "confidence": 90}
        with patch(BRIDGE, return_value={"data": retour}):
            statut = partner._bf_enrich_from_signature()
        self.assertEqual(statut, "house_identity")
        self.assertFalse(partner.mobile)

    def test_c_un_usager_interne_exempte_meme_hors_domaine(self):
        """🔴 Trou trouvé par la mutation : la branche « usager interne »
        n'était prouvée par aucun essai.

        L'essai de l'employé passait par le DOMAINE du courriel, pas par
        l'usager. Ici le domaine est étranger : seule l'appartenance interne
        peut exempter.
        """
        employe = self._contact(nom="Dominique Interne",
                               courriel="dom@delacroix-conseil.test")
        self.env["res.users"].create({
            "name": "Dominique Interne", "login": "dom@delacroix-conseil.test",
            "partner_id": employe.id,
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id])]})
        self._courriel(employe, self._corps_reponse_non_signee())
        with patch(BRIDGE, return_value={"data": dict(RETOUR_CONTAMINE)}):
            statut = employe._bf_enrich_from_signature()
        self.assertEqual(statut, "enriched")
        self.assertEqual(employe.mobile, MOBILE_MAISON)

    def test_c_un_usager_de_portail_n_exempte_pas(self):
        """🔴 Le défaut que j'avais corrigé SANS essai pour le prouver.

        Un client qui a un accès au portail a un `res.users`. Lire `user_ids`
        sans regarder `share` désarmait la garde C sur les fiches clientes les
        mieux garnies — exactement celles qu'un enrichissement vise.
        """
        client = self._contact(nom="Client Portail",
                              courriel="portail@delacroix-conseil.test")
        self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Client Portail", "login": "portail@delacroix-conseil.test",
            "partner_id": client.id,
            "groups_id": [(6, 0, [self.env.ref("base.group_portal").id])]})
        self.assertTrue(client.user_ids.filtered(lambda u: u.share),
                        "le rôle de l'essai doit bien être un portail")
        self._courriel(client, self._corps_reponse_non_signee())
        with patch(BRIDGE, return_value={"data": dict(RETOUR_CONTAMINE)}):
            statut = client._bf_enrich_from_signature()
        self.assertEqual(statut, "house_identity",
                         "un usager de portail ne rend pas la fiche « de la "
                         "maison »")
        self.assertFalse(client.mobile)

    def test_c_titre_et_adresse_de_la_maison_condamnent_a_deux(self):
        """🔴 Une signature maison sans numéro ni URL passait entière.

        Titre + rue + ville de la maison, rien d'autre : aucun signal fort, et
        pourtant c'est bien l'identité de la maison.
        """
        self.maison.write({"street": "12 rue des Essais", "city": "Québec"})
        partner = self._contact()
        self._courriel(partner, self._corps_reponse_non_signee())
        retour = {"function": "Président", "street": "12 rue des Essais",
                  "city": "Québec", "confidence": 90}
        with patch(BRIDGE, return_value={"data": retour}):
            statut = partner._bf_enrich_from_signature()
        self.assertEqual(statut, "house_identity")
        self.assertFalse(partner.function)
        self.assertFalse(partner.street)

    def test_c_un_titre_seul_ne_condamne_pas(self):
        """⚠️ « Président » est un titre courant : seul, il ne prouve rien.

        Le refus doit coûter une fiche contaminée, pas une fiche juste.
        """
        partner = self._contact()
        self._courriel(partner, self._corps_reponse_non_signee())
        retour = {"function": "Président", "phone": "514 555-0123",
                  "company": "Delacroix Conseil", "confidence": 90}
        with patch(BRIDGE, return_value={"data": retour}):
            statut = partner._bf_enrich_from_signature()
        self.assertEqual(statut, "enriched")
        self.assertEqual(partner.function, "Président")
        self.assertEqual(partner.phone, "514 555-0123")

    def test_c_sans_compte_le_proprietaire_compte_quand_meme(self):
        """🔴 Une ligne née du chatter ou de la passerelle n'a pas de compte.

        Ne lire que ``account_id`` laissait sans protection exactement le champ
        qui a fui : le mobile du propriétaire de la boîte.
        """
        partner = self._contact()
        self.env["bf.email"].create({
            "subject": "Re: Témoignage",
            "email_from": partner.email,
            "body_html": self._corps_reponse_non_signee(),
            "direction": "in",
            "date": "2026-11-10 16:00:00",
            "user_id": self.proprio.id,
            "partner_id": partner.id,
            # ⚠️ pas de account_id : c'est tout l'objet de l'essai
        })
        retour = {"mobile": MOBILE_MAISON, "confidence": 90}
        with patch(BRIDGE, return_value={"data": retour}):
            statut = partner._bf_enrich_from_signature()
        self.assertEqual(statut, "house_identity")
        self.assertFalse(partner.mobile)

    def test_c_une_societe_a_un_mot_pres_condamne(self):
        """⚠️ L'égalité d'ensembles était trop stricte : un mot de plus
        suffisait à s'échapper (« Maison Essai Conseil » ≠ « Maison Essai
        inc. »), alors que c'est la même maison."""
        partner = self._contact()
        self._courriel(partner, self._corps_reponse_non_signee())
        retour = {"company": "Maison Essai Conseil", "confidence": 90}
        with patch(BRIDGE, return_value={"data": retour}):
            statut = partner._bf_enrich_from_signature()
        self.assertEqual(statut, "house_identity")

    def test_c_une_societe_qui_ne_partage_qu_un_mot_passe(self):
        """Et l'inverse : un seul mot commun n'est pas la même maison."""
        partner = self._contact()
        self._courriel(partner, self._corps_reponse_non_signee())
        retour = {"company": "Maison Lafleur", "phone": "514 555-0123",
                  "confidence": 90}
        with patch(BRIDGE, return_value={"data": retour}):
            statut = partner._bf_enrich_from_signature()
        self.assertEqual(statut, "enriched")
        self.assertEqual(partner.company_name, "Maison Lafleur")

    def test_c_meme_titre_et_meme_ville_ne_condamnent_pas(self):
        """🔴 Le refus doit coûter une fiche contaminée, pas une fiche juste.

        « Président » est l'un des titres les plus fréquents d'une base
        réelle, et partager la ville d'un propriétaire de boîte arrive dans
        n'importe quelle PME régionale. Le titre ne compte donc jamais dans la
        paire : seuls la rue, la ville et le code postal le font.
        """
        self.proprio.partner_id.city = "Rimouski"
        partner = self._contact()
        self._courriel(partner, self._corps_reponse_non_signee())
        retour = {"function": "Président", "city": "Rimouski",
                  "phone": "514 555-0123", "confidence": 90}
        with patch(BRIDGE, return_value={"data": retour}):
            statut = partner._bf_enrich_from_signature()
        self.assertEqual(statut, "enriched")
        self.assertEqual(partner.city, "Rimouski")
        self.assertEqual(partner.function, "Président")

    # ── La moisson ciblée ───────────────────────────────────────────

    def test_moisson_n_ecrit_rien_qui_puisse_sortir_de_la_boite(self):
        """🔴 Les deux invariants du chemin qui écrit chez un client.

        1. Le contexte ``bf_email_rules_local_only`` est posé : sans lui, le
           moteur de règles joué au ``create`` archive le message sur le
           serveur, **répond une absence** au vieux correspondant, et ingère
           les invitations d'agenda (un CANCEL ancien archive l'événement
           vivant d'une série récurrente).
        2. La ligne moissonnée de l'INBOX n'est **pas** marquée traitée. Une
           ligne traitée encore présente dans l'INBOX du serveur est le
           domaine exact du balayage horaire ``_cron_imap_writeback_sweep``,
           qui apparie par Message-ID et **archive le message** (COPY +
           EXPUNGE). Pour 900 fiches, 900 messages déplacés dans les boîtes de
           clients.
        """
        partner = self._contact()
        vus = {}
        BfEmail = self.env["bf.email"]
        import email.message

        # L'en-tête doit porter l'adresse de la personne : depuis la
        # correction du tour 2, un UID dont le `From` ne vaut pas exactement
        # l'adresse cherchée est écarté.
        entete = email.message.Message()
        entete["From"] = "Thibault Delacroix <%s>" % partner.email
        entete["Subject"] = "Document de référence"

        def faux_ingest(self2, brut, uid, dossier, compte):
            vus["contexte"] = dict(self2.env.context)
            vus["uid"] = self2.env.uid
            self2.create({
                "subject": "Vieux message moissonné",
                "email_from": partner.email,
                "body_html": "<div>Bonjour</div>",
                "direction": "in",
                "date": "2024-03-04 10:00:00",
                "user_id": compte.user_id.id,
                "account_id": compte.id,
                "imap_folder": dossier,
                "imap_uid": str(uid),
            })
            return True

        with patch.object(type(BfEmail), "_ingest_rfc822", faux_ingest), \
                patch.object(type(partner), "_bf_uids_de_l_adresse",
                             staticmethod(lambda conn, adresse: [42])), \
                patch(DOSSIERS, return_value=[{"name": "INBOX"}]), \
                patch(SELECT, return_value=True), \
                patch(FETCH, return_value=b"brut"), \
                patch(ENTETES, return_value={42: (entete, False)}):
            crees = partner._bf_moisson_dans_compte(
                None, self.compte, partner.email, 2)
        self.assertEqual(crees, 1)
        self.assertTrue(vus["contexte"].get("bf_email_rules_local_only"),
                        "sans ce contexte, l'ingestion fait sortir du courrier")
        self.assertEqual(vus["uid"], self.proprio.id,
                         "la ligne doit naître au nom du propriétaire de la boîte")
        # ⚠️ Bornée au compte de l'essai : la base du banc est une copie réelle,
        # et un UID « 42 » y existe déjà ailleurs.
        ligne = BfEmail.search([("imap_uid", "=", "42"),
                                ("account_id", "=", self.compte.id)])
        self.assertEqual(len(ligne), 1)
        self.assertFalse(
            ligne.is_handled,
            "une ligne moissonnée de l'INBOX marquée « traitée » serait "
            "archivée sur le serveur par le balayage horaire")

    def test_moisson_n_ouvre_pas_la_boite_d_une_autre_societe(self):
        """🔴 Le lot élisait les trois comptes les plus récents, boîtes
        PERSONNELLES d'une autre société comprises."""
        autre = self.env["res.company"].create({"name": "Autre Maison inc."})
        usager = self.env["res.users"].create({
            "name": "Lou Privé", "login": "lou@autre-maison.test",
            "company_id": autre.id, "company_ids": [(6, 0, [autre.id])]})
        self.env["bf.email.account"].create({
            "name": "Boîte perso d'ailleurs", "user_id": usager.id,
            "company_id": autre.id, "host": "imap.exemple.test", "port": 993,
            "login": "lou@autre-maison.test", "auth_mode": "password",
            "password": "x", "writeback_archive": False,
            "last_sync_date": fields.Datetime.now()})
        partner = self._contact()
        comptes = partner._bf_comptes_pour_moisson()
        self.assertNotIn(autre, comptes.mapped("company_id"),
                         "aucune boîte d'une autre société")

    def test_moisson_prefere_un_message_qui_n_est_pas_une_reponse(self):
        """🔴 Moissonner une réponse referait exactement le défaut d'origine :
        c'est dans un premier message que l'expéditeur signe."""
        Partner = self.env["res.partner"]
        import email.message

        def entete(sujet, de="info@delacroix-conseil.test"):
            msg = email.message.Message()
            msg["Subject"] = sujet
            msg["From"] = de
            return (msg, False)

        faux = {
            9: entete("Re: Rencontre devancée ?"),
            8: entete("Témoignage — document joint"),
            7: entete("Tr: Suivi"),
        }
        with patch(ENTETES, return_value=faux):
            choisis = Partner._bf_choisir_les_uids(
                None, [9, 8, 7], "info@delacroix-conseil.test", 2)
        self.assertEqual(choisis[0], 8, "le message qui n'est pas une réponse "
                                       "doit passer devant")
        self.assertEqual(len(choisis), 2)

    def test_moisson_eteinte_par_defaut(self):
        """⚠️ Une montée ne doit pas allumer la moisson chez cinq locataires."""
        Partner = self.env["res.partner"]
        ICP = self.env["ir.config_parameter"].sudo()
        ICP.search([("key", "=", "bf_contact_enrichment.moisson_imap")]).unlink()
        self.assertFalse(Partner._bf_moisson_permise(),
                         "le défaut doit être « éteinte »")
        ICP.set_param("bf_contact_enrichment.moisson_imap", "True")
        self.assertTrue(Partner._bf_moisson_permise())
        ICP.set_param("bf_contact_enrichment.moisson_imap", "False")
        self.assertFalse(Partner._bf_moisson_permise())

    def test_moisson_ne_se_rejoue_pas_tout_de_suite(self):
        """🔴 Une recherche sans résultat ne doit pas se refaire à chaque passe."""
        partner = self._contact()
        self.assertTrue(partner._bf_moisson_a_retenter())
        partner.x_bf_moisson_date = fields.Datetime.now()
        self.assertFalse(partner._bf_moisson_a_retenter())
        partner.x_bf_moisson_date = fields.Datetime.subtract(
            fields.Datetime.now(), days=40)
        self.assertTrue(partner._bf_moisson_a_retenter())

    def test_moisson_ecarte_un_expediteur_qui_n_est_pas_la_personne(self):
        """🔴 ``SEARCH FROM`` est une SOUS-CHAÎNE : le serveur rend trop.

        Sans l'égalité exacte, la fiche de « ric@… » moissonnait les messages
        de « patric@… » — ingérés dans la base d'un client, et attribués à qui
        l'appariement voudrait.
        """
        Partner = self.env["res.partner"]
        import email.message

        def entete(de, sujet="Document"):
            msg = email.message.Message()
            msg["From"] = de
            msg["Subject"] = sujet
            return (msg, False)

        faux = {
            9: entete("Patrice Sissoko <patric@delacroix-conseil.test>"),
            8: entete("ric@delacroix-conseil.test"),
            7: entete("Autre <ric@autredomaine.test>"),
        }
        with patch(ENTETES, return_value=faux):
            choisis = Partner._bf_choisir_les_uids(
                None, [9, 8, 7], "ric@delacroix-conseil.test", 2)
        self.assertEqual(choisis, [8])

    def test_moisson_les_dossiers_fouilles(self):
        """INBOX d'abord, puis les archives de la plus récente à la plus vieille."""
        Partner = self.env["res.partner"]
        dossiers = [
            {"name": "INBOX"}, {"name": "Archives/2024"},
            {"name": "Archives/2026"}, {"name": "Archives/2025"},
            {"name": "Sent"}, {"name": "Corbeille", "noselect": True},
        ]
        with patch("odoo.addons.bf_email_management.models.bf_email_imap"
                   ".list_folders", return_value=dossiers):
            vus = Partner._bf_dossiers_a_fouiller(None, self.compte)
        self.assertEqual(vus[:4], ["INBOX", "Archives/2026", "Archives/2025",
                                   "Archives/2024"])
        self.assertNotIn("Sent", vus)
