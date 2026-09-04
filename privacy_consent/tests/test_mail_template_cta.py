"""Aucun bouton de courriel ne doit mener à un écran de connexion.

Trois gabarits — ``reminder_1``, ``reminder_2`` et ``expiring`` — envoyaient le
destinataire vers ``/my/privacy/consent/<id>``, une route ``auth="user"``. Or la
personne qu'on relance n'a pas nécessairement de compte portail : le courriel de
demande, lui, a toujours utilisé le lien public à jeton. **Un rappel dont le
bouton mène à un écran de connexion est un rappel qui ne sert à rien**, et c'est
précisément la population qu'on relance qui n'a pas de compte.

⚠ Ces gabarits sont en ``noupdate="1"`` : un ``-u`` ne réécrit PAS les
enregistrements déjà en base. Le correctif a donc dû passer par une migration
versionnée, et rien dans le dépôt ne garantit que la base VIVANTE l'a reçue. Ce
fichier contrôle les enregistrements réels, pas le XML — c'est le seul contrôle
qui ait du sens ici.

⚠ Le piège de l'assertion, et il est sérieux : ``/my/privacy/consent/`` CONTIENT
``/privacy/consent/``. Un test qui se contenterait de chercher la sous-chaîne
publique passerait au vert sur la version DÉFECTUEUSE. Il faut donc les deux
assertions : absence de la forme authentifiée, ET présence d'une forme publique
qui ne soit pas précédée de ``/my``.

⚠ Divergence DÉLIBÉRÉE entre CQ et les locataires, que ce fichier accepte des
deux côtés : sur CQ le bouton de ``expiring`` vise ``…/renew`` (la 4.9.0 y a
ajouté un GET) ; chez les locataires ``public_consent_renew`` est en
``methods=["POST"]``, donc un GET y répondrait 405 — leurs boutons visent la page
publique du consentement, qui porte déjà le formulaire.

Le lien ``/my/privacy/preferences`` du pied de page n'est PAS visé : gérer ses
préférences suppose légitimement un compte, et ce n'est pas l'appel à l'action.
"""

import re

from odoo.tests import TransactionCase, tagged

MODULES = ("cq_consent", "privacy_consent")

# Gabarits qui demandent un GESTE au destinataire : leur bouton doit être
# atteignable sans compte.
GABARITS_ACTIONNABLES = (
    "mail_template_consent_request",
    "mail_template_consent_reminder_1",
    "mail_template_consent_reminder_2",
    "mail_template_consent_expiring",
)

ROUTE_AUTHENTIFIEE = "/my/privacy/consent/"
ROUTE_PUBLIQUE = re.compile(r"(?<!/my)/privacy/consent/")


@tagged("privacy_consent", "privacy_mail_cta")
class TestMailTemplateCta(TransactionCase):

    def _gabarit(self, code):
        for module in MODULES:
            enregistrement = self.env.ref(f"{module}.{code}", raise_if_not_found=False)
            if enregistrement:
                return enregistrement
        return None

    def _corps_par_langue(self, gabarit):
        """Tous les corps réellement présents, langue par langue.

        ⚠ On lit les clés jsonb RÉELLEMENT présentes, jamais une liste de
        langues en dur : un locataire peut n'avoir que ``en_US`` (c'est le cas de
        certaines instances), et supposer ``fr_CA`` y ferait passer le test à côté du
        seul corps existant.
        """
        self.env.flush_all()
        self.env.cr.execute(
            "SELECT body_html FROM mail_template WHERE id = %s", (gabarit.id,)
        )
        ligne = self.env.cr.fetchone()
        brut = ligne[0] if ligne else None
        if isinstance(brut, dict):
            return {k: v or "" for k, v in brut.items()}
        return {"?": brut or ""}

    def test_actionable_templates_have_no_login_wall(self):
        """RÉGRESSION — aucun bouton d'action ne pointe vers une route
        ``auth="user"``."""
        controles = 0
        for code in GABARITS_ACTIONNABLES:
            gabarit = self._gabarit(code)
            if not gabarit:
                continue
            for langue, corps in self._corps_par_langue(gabarit).items():
                controles += 1
                self.assertNotIn(
                    ROUTE_AUTHENTIFIEE, corps,
                    f"{code} [{langue}] renvoie vers {ROUTE_AUTHENTIFIEE}, "
                    "c'est-à-dire vers un écran de connexion, pour une "
                    "population qui n'a pas de compte.",
                )
        self.assertTrue(
            controles,
            "Aucun gabarit actionnable trouvé : le test ne contrôle rien. "
            "Vérifier les xmlid avant de croire ce vert.",
        )

    def test_actionable_templates_carry_a_public_link(self):
        """Le pendant du test précédent : retirer le lien mort ne suffit pas, il
        faut qu'un lien PUBLIC le remplace. Sans cette seconde assertion, vider
        le bouton ferait passer le premier test au vert."""
        for code in GABARITS_ACTIONNABLES:
            gabarit = self._gabarit(code)
            if not gabarit:
                continue
            for langue, corps in self._corps_par_langue(gabarit).items():
                self.assertTrue(
                    ROUTE_PUBLIQUE.search(corps),
                    f"{code} [{langue}] ne porte aucun lien public à jeton : "
                    "le destinataire n'a aucun moyen d'agir.",
                )

    def test_public_link_carries_the_access_token(self):
        """Un lien public sans jeton rendrait « Lien invalide » : le bouton
        serait mort d'une autre façon."""
        for code in GABARITS_ACTIONNABLES:
            gabarit = self._gabarit(code)
            if not gabarit:
                continue
            for langue, corps in self._corps_par_langue(gabarit).items():
                self.assertIn(
                    "access_token", corps,
                    f"{code} [{langue}] construit une URL publique sans jeton.",
                )
