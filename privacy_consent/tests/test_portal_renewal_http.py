"""Les routes portail de renouvellement, exercées en HTTP RÉEL.

⚠ C'est le fichier que ce module n'avait pas, et c'est la raison de fond pour
laquelle une série de défauts a survécu des semaines : ``tests/`` ne contenait
que des ``TransactionCase``, et ``test_privacy_portal.py`` — malgré son nom —
n'exerce que ``action_renew()``, la méthode dorsale. **Aucune route n'était
testée.** Or les trois lots de fin juillet et début août ont tous eu leurs
défauts dans les contrôleurs :

* la preuve d'envoi détruite en cascade par ``auto_delete`` ;
* le renouvellement qui épinglait l'avis PÉRIMÉ (``notice_version_id`` est
  ``copy=True``) ;
* la preuve scellée AVANT l'octroi, donc portant « brouillon, accordé le :
  néant » ;
* l'écran de confirmation qui demandait « j'ai lu l'avis ci-dessus » devant un
  cadre vide.

Chacun de ces cas a ici son test. Un test qui redevient vert après une refonte
du contrôleur n'est pas une formalité : c'est le seul filet.

⚠ Les POST passent par le jeton CSRF réellement émis par le gabarit. C'est
délibéré : cela vérifie du même coup que le formulaire le porte, sans quoi le
bouton serait mort en production alors que les tests passeraient.
"""

import re
from datetime import timedelta

from odoo import fields
from odoo.tests import HttpCase, tagged

MARQUEUR_COURANT = "MARQUEUR-AVIS-EN-VIGUEUR"
MARQUEUR_PERIME = "MARQUEUR-AVIS-PERIME"


@tagged("post_install", "-at_install", "privacy_consent", "privacy_portal_http")
class TestPortalRenewalHttp(HttpCase):

    def setUp(self):
        super().setUp()
        self.Consent = self.env["privacy.consent"]
        self.Version = self.env["privacy.notice.version"]
        self.Evidence = self.env["privacy.consent.evidence"]

        self.purpose = self.env["privacy.purpose"].create({
            "code": "RENEW_HTTP",
            "name": "Finalité d'essai — renouvellement HTTP",
            "default_validity_days": 365,
            "plain_language_summary": "Résumé de repli de la finalité.",
        })
        self.notice = self.env["privacy.notice"].create({
            "name": "Avis d'essai — renouvellement HTTP",
            "purpose_id": self.purpose.id,
            "body_fr": f"<p>{MARQUEUR_COURANT}</p>",
        })
        hier = fields.Date.today() - timedelta(days=30)
        self.version_perimee = self.Version.create({
            "notice_id": self.notice.id,
            "version": "1.0",
            "body": f"<p>{MARQUEUR_PERIME}</p>",
            "effective_date": hier,
        })
        self.version_courante = self.Version.create({
            "notice_id": self.notice.id,
            "version": "2.0",
            "body": f"<p>{MARQUEUR_COURANT}</p>",
            "effective_date": fields.Date.today(),
        })
        self.notice.invalidate_recordset()
        self.assertEqual(
            self.notice.current_version_id, self.version_courante,
            "Prérequis du fichier : la v2.0 doit être la version en vigueur.",
        )

        self.partner = self.env["res.partner"].create({
            "name": "Sujet renouvellement HTTP",
            "email": "renouvellement@example.invalid",
        })

    # ------------------------------------------------------------------
    # Outillage
    # ------------------------------------------------------------------

    def _consentement(self, status="granted", **extra):
        vals = {
            "subject_partner_id": self.partner.id,
            "purpose_id": self.purpose.id,
            "notice_id": self.notice.id,
            # ⚠ Épinglé sur la version PÉRIMÉE : c'est l'état d'un client qui a
            # consenti avant la publication de la v2.0.
            "notice_version_id": self.version_perimee.id,
            "status": status,
        }
        if status == "granted":
            vals["granted_at"] = fields.Datetime.now() - timedelta(days=300)
            vals["expires_at"] = fields.Datetime.now() + timedelta(days=20)
        vals.update(extra)
        return self.Consent.create(vals)

    def _url_renew(self, consent):
        return f"/privacy/consent/{consent.id}/{consent.access_token}/renew"

    def _url_detail(self, consent):
        return f"/privacy/consent/{consent.id}/{consent.access_token}"

    def _csrf(self, html):
        trouve = re.search(
            r'name="csrf_token"\s+value="([^"]+)"', html
        ) or re.search(r'value="([^"]+)"\s+name="csrf_token"', html)
        self.assertTrue(
            trouve,
            "Le formulaire n'émet aucun jeton CSRF : le bouton serait mort en "
            "production, et un test qui poste sans jeton ne le verrait jamais.",
        )
        return trouve.group(1)

    # ------------------------------------------------------------------
    # GET — l'écran de confirmation
    # ------------------------------------------------------------------

    def test_get_shows_current_notice_and_mandatory_checkbox(self):
        """Le texte affiché doit être l'avis EN VIGUEUR, pas celui épinglé."""
        consent = self._consentement()
        reponse = self.url_open(self._url_renew(consent))

        self.assertEqual(reponse.status_code, 200)
        html = reponse.text
        self.assertIn(MARQUEUR_COURANT, html)
        self.assertNotIn(
            MARQUEUR_PERIME, html,
            "L'écran présente l'ancien texte : la personne reconsentirait à un "
            "avis périmé.",
        )
        self.assertIn('name="notice_read"', html)
        self.assertIn("csrf_token", html)

    def test_get_creates_nothing(self):
        """⚠ Ce GET est ANONYME. Il ne doit faire naître ni version d'avis ni
        consentement : ``_ensure_current_version()`` CRÉE une v1.0 quand il n'y
        en a pas, d'après la langue de l'utilisateur courant. Le déclencher sur
        un GET public figerait le texte juridique d'après la langue d'un
        visiteur anonyme."""
        consent = self._consentement()
        versions_avant = self.Version.search_count([])
        consentements_avant = self.Consent.search_count([])

        self.url_open(self._url_renew(consent))

        self.assertEqual(self.Version.search_count([]), versions_avant)
        self.assertEqual(self.Consent.search_count([]), consentements_avant)

    def test_get_on_notice_without_version_uses_template_body(self):
        """Un modèle sans version publiée doit quand même montrer un texte.

        ⚠ Sans cela, l'écran s'affichait vide avec la case « j'ai lu l'avis
        ci-dessus » et le bouton d'octroi : la preuve scellée ensuite affirmait
        une lecture qui n'avait pas eu lieu.
        """
        notice_nue = self.env["privacy.notice"].create({
            "name": "Avis sans version",
            "purpose_id": self.purpose.id,
            "body_fr": "<p>TEXTE-DU-MODELE-SANS-VERSION</p>",
        })
        consent = self._consentement(
            notice_id=notice_nue.id, notice_version_id=False,
        )
        reponse = self.url_open(self._url_renew(consent))
        self.assertEqual(reponse.status_code, 200)
        self.assertIn("TEXTE-DU-MODELE-SANS-VERSION", reponse.text)
        # …et toujours rien créé au passage.
        self.assertFalse(notice_nue.version_ids)

    def test_invalid_token_renders_200_not_404(self):
        """Un jeton invalide rend une page « lien invalide », pas un 404.

        Ce n'est pas une fuite : la page ne révèle rien du consentement. Le
        vérifier évite qu'on le « corrige » un jour en 404, ce qui distinguerait
        un identifiant existant d'un identifiant inexistant.
        """
        consent = self._consentement()
        reponse = self.url_open(f"/privacy/consent/{consent.id}/jeton-bidon/renew")
        self.assertEqual(reponse.status_code, 200)
        self.assertIn("Lien invalide", reponse.text)
        self.assertNotIn(MARQUEUR_COURANT, reponse.text)

    # ------------------------------------------------------------------
    # POST — le renouvellement lui-même
    # ------------------------------------------------------------------

    def test_post_without_confirmation_creates_nothing(self):
        """⚠ Sans la case cochée, RIEN ne doit naître en base.

        Le contrôle vient avant la résolution en écriture, précisément pour
        qu'un POST forgé ne fasse pas naître une version d'avis.
        """
        consent = self._consentement()
        html = self.url_open(self._url_renew(consent)).text
        csrf = self._csrf(html)

        versions_avant = self.Version.search_count([])
        consentements_avant = self.Consent.search_count([])

        reponse = self.url_open(
            self._url_renew(consent),
            data={"csrf_token": csrf},
            allow_redirects=False,
        )
        self.assertIn(reponse.status_code, (302, 303))
        self.assertIn("error=notice_not_read", reponse.headers.get("Location", ""))
        self.assertEqual(self.Version.search_count([]), versions_avant)
        self.assertEqual(self.Consent.search_count([]), consentements_avant)
        self.assertFalse(consent.renewed_to_id)

    def test_post_grants_and_pins_current_version(self):
        """RÉGRESSION — le renouvellement doit épingler l'avis EN VIGUEUR.

        ⚠ ``notice_version_id`` est ``copy=True`` : sans imposer explicitement
        la version courante, ``consent.copy()`` recopie celle de l'ANCIEN
        consentement, et la garde de ``create()`` ne se déclenche pas puisque la
        copie fournit déjà une valeur. La personne reconsentirait au texte
        périmé sans que rien ne le signale.
        """
        consent = self._consentement()
        csrf = self._csrf(self.url_open(self._url_renew(consent)).text)

        self.url_open(
            self._url_renew(consent),
            data={"csrf_token": csrf, "notice_read": "1"},
        )
        consent.invalidate_recordset()

        nouveau = consent.renewed_to_id
        self.assertTrue(nouveau, "Aucun consentement de renouvellement créé.")
        self.assertEqual(nouveau.status, "granted")
        self.assertEqual(nouveau.renewed_from_id, consent)
        self.assertEqual(
            nouveau.notice_version_id, self.version_courante,
            "Le renouvellement a épinglé l'avis périmé.",
        )

    def test_renewal_evidence_is_sealed_after_grant(self):
        """RÉGRESSION — la preuve doit être scellée APRÈS l'octroi.

        ⚠ Créée avant, l'instantané signé portait « status: draft, granted_at:
        None », et le certificat PDF qu'on oppose disait littéralement
        « brouillon, accordé le : néant ».
        """
        consent = self._consentement()
        csrf = self._csrf(self.url_open(self._url_renew(consent)).text)
        self.url_open(
            self._url_renew(consent),
            data={"csrf_token": csrf, "notice_read": "1"},
        )
        consent.invalidate_recordset()
        nouveau = consent.renewed_to_id

        preuve = self.Evidence.search([
            ("consent_id", "=", nouveau.id),
            ("consent_action", "=", "renew"),
        ], limit=1)
        self.assertTrue(preuve, "Aucune preuve de renouvellement scellée.")
        self.assertEqual(preuve.access_type, "portal_public")
        # L'instantané doit refléter un consentement ACCORDÉ, pas un brouillon.
        instantane = (preuve.consent_snapshot or "") if "consent_snapshot" in preuve._fields else ""
        if instantane:
            self.assertNotIn('"status": "draft"', instantane)
        self.assertIn(
            "après confirmation de lecture", preuve.note or "",
            "La note de preuve doit dire ce qui a été confirmé.",
        )

    def test_renewal_note_cites_the_displayed_version(self):
        """La preuve ne peut affirmer que ce qui a été montré à l'écran."""
        consent = self._consentement()
        csrf = self._csrf(self.url_open(self._url_renew(consent)).text)
        self.url_open(
            self._url_renew(consent),
            data={"csrf_token": csrf, "notice_read": "1"},
        )
        consent.invalidate_recordset()
        preuve = self.Evidence.search([
            ("consent_id", "=", consent.renewed_to_id.id),
            ("consent_action", "=", "renew"),
        ], limit=1)
        self.assertIn(str(self.version_courante.id), preuve.note or "")
        self.assertIn("version en vigueur", preuve.note or "")

    def test_refused_consent_cannot_be_turned_around_from_its_link(self):
        """⚠ Ce test disait le CONTRAIRE, et il était périmé.

        Il portait la décision de fond du 2026-08-02 — « retourner un refus
        reste possible depuis le lien reçu ». La décision de gouvernance du 2026-08-03,
        le lendemain, l'a renversée : « un consentement refusé doit annuler le
        lien d'origine ; impossible de le transformer en accordé, il faut
        renvoyer une nouvelle demande ». Le code applique la décision 50 depuis
        le 2026-08-16 et `tests/test_refusal_closes_link.py` la couvre ; ce
        test-ci est resté rouge, seul contre le reste du module.

        Il vérifie donc maintenant ce que la décision impose : la page reste
        LISIBLE, elle explique le refus, et elle n'offre plus d'accorder.
        Le chemin sanctionné est `action_new_request_after_refusal`.
        """
        consent = self._consentement(status="refused")
        reponse = self.url_open(self._url_renew(consent))
        self.assertEqual(reponse.status_code, 200)
        self.assertNotIn('name="notice_read"', reponse.text)
        self.assertIn("Vous avez refusé cette demande", reponse.text)
        consent.invalidate_recordset()
        self.assertFalse(
            consent.renewed_to_id,
            "Le lien d'origine a produit un renouvellement : la décision 50 "
            "veut qu'il soit consommé.",
        )
        self.assertEqual(consent.status, "refused")

    def test_withdrawn_consent_can_be_turned_around(self):
        consent = self._consentement(status="withdrawn")
        reponse = self.url_open(self._url_renew(consent))
        self.assertEqual(reponse.status_code, 200)
        self.assertIn('name="notice_read"', reponse.text)

    def test_renewal_clears_signature_identifiers(self):
        """⚠ Les identifiants DocuSeal et LibreSign se recopient eux aussi.

        Sans remise à zéro, le renouvellement hérite de la référence de
        signature du consentement PRÉCÉDENT : deux consentements distincts
        pointeraient la même pièce signée.
        """
        champs = self.Consent._fields
        signature = {
            c: "valeur-heritee"
            for c in ("docuseal_submission_id", "libresign_file_uuid")
            if c in champs
        }
        if not signature:
            self.skipTest("Aucun champ de signature sur ce module.")

        consent = self._consentement(**signature)
        csrf = self._csrf(self.url_open(self._url_renew(consent)).text)
        self.url_open(
            self._url_renew(consent),
            data={"csrf_token": csrf, "notice_read": "1"},
        )
        consent.invalidate_recordset()
        nouveau = consent.renewed_to_id
        for champ in signature:
            self.assertFalse(
                nouveau[champ],
                f"{champ} a été hérité du consentement précédent.",
            )

    def test_already_renewed_redirects_to_the_new_consent(self):
        consent = self._consentement()
        csrf = self._csrf(self.url_open(self._url_renew(consent)).text)
        self.url_open(
            self._url_renew(consent),
            data={"csrf_token": csrf, "notice_read": "1"},
        )
        consent.invalidate_recordset()
        nouveau = consent.renewed_to_id
        self.assertTrue(nouveau, "Le renouvellement doit avoir créé un consentement.")

        reponse = self.url_open(self._url_renew(consent), allow_redirects=False)
        self.assertIn(reponse.status_code, (302, 303))

        # ⚠ Suivre la chaîne, et non la seule première étape. Sur un site
        # MULTILINGUE (Blue Fox a en_CA et fr_CA actives, contrairement à CQ et
        # PME Conforme qui n'ont que fr_CA), la première redirection est celle
        # de la langue — /privacy/... devient /en/privacy/... — et le
        # gestionnaire n'a pas encore vu la requête. Lire cette étape comme la
        # redirection métier fait échouer le test là où le produit est correct,
        # et le ferait passer pour une régression du portage.
        finale = self.url_open(self._url_renew(consent), allow_redirects=True)
        self.assertEqual(finale.status_code, 200)
        self.assertIn(
            f"/privacy/consent/{nouveau.id}/", finale.url,
            "Un renouvellement déjà fait doit mener au NOUVEAU consentement.",
        )

    # ------------------------------------------------------------------
    # Preuve d'affichage
    # ------------------------------------------------------------------

    def test_view_evidence_is_deduplicated(self):
        """⚠ Une preuve par affichage remplissait le registre d'entrées qui ne
        correspondent à aucun geste : rechargement, retour arrière,
        préchargement du navigateur, et surtout POST refusé faute de case
        cochée, qui redirige vers ce même GET.

        Le registre est la pièce qu'on oppose : il doit refléter des
        consultations, pas des allers-retours du navigateur.
        """
        consent = self._consentement()
        domaine = [
            ("consent_id", "=", consent.id),
            ("consent_action", "=", "view"),
        ]
        self.url_open(self._url_renew(consent))
        apres_une = self.Evidence.search_count(domaine)
        self.assertEqual(apres_une, 1, "Le premier affichage doit être consigné.")

        for _ in range(3):
            self.url_open(self._url_renew(consent))
        self.assertEqual(
            self.Evidence.search_count(domaine), 1,
            "Les affichages répétés dans la fenêtre de dédoublonnage ne doivent "
            "pas gonfler le registre de preuves.",
        )
