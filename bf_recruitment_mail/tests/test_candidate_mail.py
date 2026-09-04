# Part of bf_recruitment_mail. Voir LICENSE.
"""Ce qu'on prouve : le candidat comprend ce qu'il reçoit.

Les gabarits du coeur échouaient sur trois points mesurables, et ces contrôles
tombent si quelqu'un les réintroduit :
  * un sujet identique pour les quatre messages ;
  * un refus qui ne dit pas qu'il est un refus ;
  * l'adresse courriel interne du recruteur publiée au candidat.
"""

import re

from odoo.tests import TransactionCase, tagged

_XMLIDS = {
    "accuse": "hr_recruitment.email_template_data_applicant_congratulations",
    "invitation": "hr_recruitment.email_template_data_applicant_interest",
    "refus": "hr_recruitment.email_template_data_applicant_refuse",
    "retrait": "hr_recruitment.email_template_data_applicant_not_interested",
}


@tagged("post_install", "-at_install")
class TestCandidateMail(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.ref("base.main_company")
        cls.company.write({"email": "rh@exemple.invalid"})
        cls.recruteur = cls.env["res.users"].create({
            "name": "Anouk Lemieux", "login": "recruteur_mail",
            "email": "interne.a.ne.pas.publier@exemple.invalid",
            "groups_id": [(6, 0, [
                cls.env.ref("base.group_user").id,
                cls.env.ref("hr_recruitment.group_hr_recruitment_user").id,
            ])],
        })
        cls.job = cls.env["hr.job"].create({
            "name": "Conseiller TI", "company_id": cls.company.id,
        })
        cls.candidate = cls.env["hr.candidate"].create({
            "partner_name": "Camille Sanschagrin",
            "email_from": "camille@exemple.invalid", "company_id": cls.company.id,
        })
        cls.applicant = cls.env["hr.applicant"].create({
            "candidate_id": cls.candidate.id, "job_id": cls.job.id,
            "company_id": cls.company.id, "user_id": cls.recruteur.id,
        })

    @staticmethod
    def _plat(html):
        """Aplatir les espaces avant de chercher une phrase.

        ⚠️ Un corps HTML indenté coupe les phrases sur plusieurs lignes :
        « Votre dossier est\n        clos » ne contient PAS la sous-chaîne
        « dossier est clos ». Un contrôle littéral échoue alors sur un texte
        parfaitement correct.
        """
        return re.sub(r"\s+", " ", html or "")

    def _rendu(self, cle, applicant=None):
        template = self.env.ref(_XMLIDS[cle])
        rec = applicant or self.applicant
        valeurs = template._generate_template(
            rec.ids, ("subject", "body_html", "email_from"))[rec.id]
        return valeurs

    def test_each_message_has_its_own_subject(self):
        """🔴 Les quatre gabarits du coeur portaient le MÊME sujet.

        « Your Job Application: <poste> » pour l'accusé, l'invitation, le refus
        et le retrait. Dans la boîte du candidat, rien ne les distingue.
        """
        sujets = {cle: self._rendu(cle)["subject"] for cle in _XMLIDS}
        self.assertEqual(
            len(set(sujets.values())), 4,
            "Deux messages au moins partagent un sujet : %s" % sujets,
        )
        for sujet in sujets.values():
            self.assertIn(
                self.job.name, sujet,
                "Le sujet doit nommer le poste : le candidat postule souvent à plusieurs.",
            )

    def test_the_refusal_says_it_is_a_refusal(self):
        """🔴 Le refus du coeur ne contenait aucun mot de refus.

        Son corps entier : « Nous tenons à vous remercier de votre intérêt et
        de votre temps. Nous vous souhaitons le meilleur dans vos projets
        futurs. » Une personne pressée peut le lire deux fois sans comprendre.
        """
        corps = self._plat(self._rendu("refus")["body_html"])
        self.assertIn(
            "ne retiendrons pas", corps,
            "La décision doit être écrite en clair, pas laissée à deviner.",
        )
        avant_remerciement = corps.split("Merci")[0]
        self.assertIn(
            "ne retiendrons pas", avant_remerciement,
            "La décision doit venir AVANT les remerciements, pas après.",
        )

    def test_no_internal_address_reaches_the_candidate(self):
        """🔴 Le corps du coeur publiait l'adresse interne du recruteur."""
        for cle in _XMLIDS:
            valeurs = self._rendu(cle)
            self.assertNotIn(
                self.recruteur.email, valeurs["body_html"],
                "%s publie l'adresse interne du recruteur au candidat." % cle,
            )
            self.assertIn(
                self.company.email, valeurs["email_from"],
                "%s doit partir de l'adresse de la société : sans expéditeur, "
                "Odoo retombe sur odoobot@example.com, que les relais rejettent."
                % cle,
            )

    def test_the_recruiter_is_named_without_being_exposed(self):
        """Écrire à quelqu'un de nommé vaut mieux qu'écrire à une équipe."""
        corps = self._plat(self._rendu("invitation")["body_html"])
        self.assertIn(self.recruteur.name, corps)
        self.assertNotIn(self.recruteur.email, corps)

    def test_the_message_follows_the_real_path(self):
        """Une personne rencontrée n'est pas remerciée comme une autre."""
        sans_entrevue = self._plat(self._rendu("refus")["body_html"])
        self.assertNotIn("nous rencontrer", sans_entrevue)

        guide = self.env["bf.interview.guide"].create({
            "name": "Grille", "round_type": "technique", "scale_max": 5,
            "company_id": self.company.id,
            "criterion_ids": [(0, 0, {"name": "Critere", "weight": 1.0})],
        })
        guide.action_publish()
        interview = self.env["bf.interview"].create({
            "applicant_id": self.applicant.id, "guide_id": guide.id,
            "company_id": self.company.id,
            "interviewer_ids": [(6, 0, [self.recruteur.id])],
        })
        interview.action_mark_held()
        self.applicant.invalidate_recordset()
        self.assertEqual(self.applicant.held_interview_count, 1)

        avec_entrevue = self._plat(self._rendu("refus")["body_html"])
        self.assertIn("nous rencontrer", avec_entrevue)
        self.assertIn(
            "appréciations écrites en entrevue", avec_entrevue,
            "Après une entrevue, le droit d'accès porte aussi sur les "
            "appréciations : il doit être nommé.",
        )

    def test_every_template_wears_the_brand(self):
        """Sans `email_layout_xmlid`, le corps part nu.

        ⚠️ `bf_mail_layout` n'est PAS une surcharge du gabarit de notification
        d'Odoo : il ne s'applique QUE s'il est nommé. Un gabarit qui perd ce
        champ continue de partir, sans en-tête, sans logo et sans pied, et
        aucun autre contrôle ne le voit.
        """
        for cle, xmlid in _XMLIDS.items():
            self.assertEqual(
                self.env.ref(xmlid).email_layout_xmlid,
                "bluefox_branding.bf_mail_layout",
                "%s ne porte plus la mise en page brandée." % cle,
            )

    def test_the_templates_stay_open_to_upgrades(self):
        """🔴 Le no-op silencieux qui ignorait toute correction ultérieure.

        Les gabarits de `hr_recruitment` sont `noupdate="1"` chez eux. Odoo
        garde ce drapeau sur `ir_model_data` et refuse alors toute réécriture
        lors d'une mise à niveau, même depuis un autre module. Ce module
        écrivait donc ses textes à l'installation, une seule fois, et chaque
        correction ensuite était ignorée sans un mot.
        """
        self.env.cr.execute(
            """
            SELECT name, noupdate FROM ir_model_data
             WHERE model = 'mail.template' AND module = 'hr_recruitment'
               AND name IN %s
            """,
            (tuple(x.split(".", 1)[1] for x in _XMLIDS.values()),),
        )
        bloques = [nom for nom, noupdate in self.env.cr.fetchall() if noupdate]
        self.assertFalse(
            bloques,
            "Ces gabarits refuseront toute correction future : %s" % bloques,
        )

    def test_the_access_right_is_named(self):
        """Le droit de consulter ce qu'on détient ne se sous-entend pas."""
        for cle in ("accuse", "refus", "retrait"):
            corps = self._plat(self._rendu(cle)["body_html"])
            self.assertIn("consulter ce qu", corps, cle)

    def test_the_message_speaks_to_a_person(self):
        """Le prénom, pas le nom complet : c'est ce qui rend un courriel humain."""
        for cle in _XMLIDS:
            corps = self._plat(self._rendu(cle)["body_html"])
            self.assertIn("Camille", corps, cle)
            self.assertNotIn(
                "Camille Sanschagrin", corps,
                "%s s'adresse au nom complet ; on écrit à un prénom." % cle,
            )

    def test_the_privacy_paragraph_is_there(self):
        """Dire ce qu'on garde et pourquoi fait partie du message."""
        for cle in ("accuse", "refus", "retrait"):
            corps = self._plat(self._rendu(cle)["body_html"])
            self.assertIn("le temps prévu par notre politique", corps, cle)
            self.assertIn("supprim", corps, cle)

    def test_no_stale_translation_masks_the_rewrite(self):
        """🔴 Le défaut qui annulait tout le module en silence.

        Sur un locataire installé en français, les gabarits de `hr_recruitment`
        portent une valeur `fr_CA` traduite depuis l'anglais d'origine. Récrire
        le champ dans un fichier de données ne touche que la SOURCE (`en_US`) :
        la valeur `fr_CA` survit et c'est elle qui est rendue. Le module
        s'installe, les identifiants XML sont bien repris, et le candidat reçoit
        exactement ce qu'il recevait avant.

        Constaté sur la démo : `subject->>'en_US'` portait le nouveau texte,
        `subject->>'fr_CA'` l'ancien, et l'instance tourne en `fr_CA`.
        """
        for cle, xmlid in _XMLIDS.items():
            gabarit = self.env.ref(xmlid)
            for champ in ("subject", "body_html"):
                self.env.cr.execute(
                    "SELECT jsonb_object_keys(%s) FROM mail_template WHERE id = %%s"
                    % champ, (gabarit.id,))
                langues = {ligne[0] for ligne in self.env.cr.fetchall()}
                self.assertEqual(
                    langues, {"en_US"},
                    "%s.%s garde une traduction périmée (%s) qui masquerait le "
                    "texte de ce module." % (cle, champ, sorted(langues - {"en_US"})),
                )

    def test_the_withdrawal_acknowledges_the_withdrawal(self):
        """Le gabarit de retrait du coeur ne disait pas de quoi il parlait.

        « Nous tenons à vous remercier de votre intérêt et de votre temps. Nous
        vous souhaitons le meilleur dans vos projets futurs. » Envoyé quand
        c'est le CANDIDAT qui se désiste, il n'accuse même pas le désistement.

        ⚠️ C'est ce message, et non le refus, qui a été critiqué à tort dans une
        première version : le harnais de QA prenait le premier motif de refus
        par `sequence`, et le plus bas est rattaché à ce gabarit-ci.
        """
        corps = self._plat(self._rendu("retrait")["body_html"])
        self.assertIn("ne souhaitez pas poursuivre", corps)
        self.assertIn("dossier est clos", corps)
        self.assertIn("ne vous relancera pas", corps)
        self.assertIn("C'est noté", corps)

    def test_the_refusal_makes_no_retention_promise(self):
        """Le refus du coeur promettait de garder le CV « for future opportunities ».

        Une promesse qui contredit un calendrier de conservation, et que peu
        d'entreprises tiennent. On invite à repostuler, on ne promet pas de
        garder.
        """
        corps = self._plat(self._rendu("refus")["body_html"])
        self.assertNotIn("garderons", corps)
        self.assertIn("autres affichages", corps)

    def test_the_portal_link_is_optional(self):
        """⚠️ Couplage SOUPLE avec le portail, et le garde qui le rend sûr.

        Ce module ne dépend pas de `bf_recruitment_portal`. Le lien n'apparaît
        que si le portail est installé, et le `t-if` sur `object._fields` est
        ce qui empêche un `AttributeError` au rendu chez tous les locataires
        qui ne l'ont pas. Ce contrôle vaut dans les deux sens : il exige le
        lien quand le champ existe, et son absence sinon.
        """
        installe = "access_url" in self.env["hr.applicant"]._fields
        for cle in ("accuse", "refus"):
            corps = self._plat(self._rendu(cle)["body_html"])
            if installe:
                self.assertIn("/my/candidature/", corps,
                              "%s devrait porter le lien du portail" % cle)
                self.assertIn("access_token=", corps,
                              "le lien doit porter le jeton, sinon il n'ouvre rien")
            else:
                self.assertNotIn("/my/candidature/", corps)

