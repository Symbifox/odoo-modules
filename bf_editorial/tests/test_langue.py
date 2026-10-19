# -*- coding: utf-8 -*-
"""Ce que l'atelier écrit en base se lit dans la langue de qui le lira.

La source du module est passée au français à l'anglais ; le français vit dans
`i18n/fr_CA.po`. Au-delà des libellés, plusieurs textes sont écrits en base ou
comparés mot pour mot, et c'est là que la langue de l'acteur faisait des dégâts :

* la dérogation fige ses motifs, puis les compare au texte du jour. Figés dans
  la langue du signataire, ils ne couvraient plus rien dès qu'une personne d'une
  autre langue, ou le travail planifié, relisait l'entrée. Ils se figent et se
  comparent maintenant dans la langue canonique (celle de la société) ;
* les constats de QA servent d'empreinte à la dérogation : repassés dans une
  autre langue, ils la périmaient. Ils se calculent dans la langue canonique ;
* les gabarits de liste de contrôle sont copiés dans un champ non traduisible et
  dédoublonnés par nom : copiés dans la langue canonique ;
* l'état des langues, le rapport de réparation et le ratio du calendrier étaient
  assemblés en dur en français ;
* la note du travail planifié part au responsable de l'entrée, dans sa langue.

Les essais épinglent fr_CA et en_CA, qu'ils activent, et la langue de la société
(la langue canonique) : sur une base réelle, OdooBot et l'admin sont en fr_CA.
"""

from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestLangue(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Lang = cls.env["res.lang"]
        for code in ("fr_CA", "en_CA"):
            Lang._activate_lang(code)
        cls.env["ir.module.module"]._load_module_terms(
            ["bf_editorial"], ["fr_CA"], overwrite=True)
        cls.fr = Lang._lang_get("fr_CA")
        cls.en = Lang._lang_get("en_CA")
        cls.brouillon = cls.env.ref("bf_editorial.stage_draft")

    # ------------------------------------------------------------------
    # Outils
    # ------------------------------------------------------------------
    def _canon(self, code):
        """Épingler la langue canonique : celle de la société de l'entrée."""
        self.env.company.partner_id.lang = code

    def _calendrier(self, **valeurs):
        base = {"name": "Essai de langue", "require_all_langs": "no",
                "word_floor": 0}
        base.update(valeurs)
        return self.env["bf.editorial.calendar"].create(base)

    def _entree(self, calendrier, **valeurs):
        base = {"name": "Entrée essai", "calendar_id": calendrier.id,
                "stage_id": self.brouillon.id}
        base.update(valeurs)
        entree = self.env["bf.editorial.entry"].create(base)
        entree.checklist_ids.unlink()
        entree.invalidate_recordset()
        return entree

    def _entree_sous_constats(self):
        """Deux motifs signables : des constats de QA, et un plancher de mots
        manqué (1500 contre 1900)."""
        entree = self._entree(
            self._calendrier(word_floor=1900),
            qa_state="findings", qa_findings="[fr_CA] 2 tiret(s) cadratin.",
        )
        self.env["bf.editorial.version"].create({
            "entry_id": entree.id, "lang_id": self.fr.id, "is_source": True,
            "state": "reviewed", "word_count": 1500,
        })
        entree.invalidate_recordset()
        return entree

    def _lu_en(self, entree, code):
        entree.invalidate_recordset()
        return entree.with_context(lang=code)

    def _billet(self, contenu):
        blogue = self.env["blog.blog"].create({"name": "Essai de langue"})
        return self.env["blog.post"].create({
            "name": "Billet essai", "blog_id": blogue.id, "content": contenu,
        })

    # ------------------------------------------------------------------
    # La dérogation fige et compare dans la langue canonique
    # ------------------------------------------------------------------
    def test_derogation_signee_en_anglais_figee_dans_la_langue_canonique(self):
        """Société en fr_CA, signature par un anglophone : les motifs figés
        sont les français, et la dérogation couvre encore pour les deux langues."""
        self._canon("fr_CA")
        entree = self._entree_sous_constats()
        self.assertEqual(len(self._lu_en(entree, "en_CA")._waivable_problems()), 2)

        entree.with_context(lang="en_CA")._sign_waiver("Citation d'un texte de loi.")
        entree.invalidate_recordset()
        figes = entree.qa_waiver_problems
        self.assertIn("La QA éditoriale a laissé des constats ouverts.", figes)
        self.assertIn("Plancher de mots non atteint : 1500 contre 1900.", figes)
        self.assertNotIn("Word floor", figes)

        for code in ("en_CA", "fr_CA"):
            self.assertEqual(
                self._lu_en(entree, code)._preflight_problems(), [],
                "la dérogation doit couvrir encore quand on relit en %s" % code)

    def test_derogation_signee_en_francais_couvre_pour_l_anglophone_et_le_cron(self):
        """Société en en_CA, signature par un francophone. Un anglophone relit,
        puis le travail planifié publie au nom d'un responsable francophone."""
        self._canon("en_CA")
        entree = self._entree_sous_constats()
        entree.with_context(lang="fr_CA")._sign_waiver("Citation d'un texte de loi.")
        entree.invalidate_recordset()
        self.assertIn("Word floor not reached: 1500 against 1900.",
                      entree.qa_waiver_problems)
        self.assertNotIn("Plancher", entree.qa_waiver_problems)
        for code in ("fr_CA", "en_CA"):
            self.assertEqual(
                self._lu_en(entree, code)._preflight_problems(), [],
                "la dérogation doit couvrir encore quand on relit en %s" % code)

        Entree = self.env["bf.editorial.entry"]
        maintenant = fields.Datetime.now()
        # Les entrées copiées de la prod ne doivent pas partir avec la nôtre.
        Entree.search([("scheduled_publish_date", "<=", maintenant),
                       ("published_date", "=", False)]).write(
            {"scheduled_publish_date": False})
        responsable = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Responsable francophone", "login": "essai-resp-fr",
            "email": "essai-resp-fr@example.com", "lang": "fr_CA",
        })
        entree.write({"user_id": responsable.id,
                      "scheduled_publish_date": maintenant - timedelta(hours=1)})
        Entree.with_context(lang="en_CA")._cron_publish_scheduled()
        entree.invalidate_recordset()
        self.assertTrue(entree.published_date,
                        "le travail planifié a refusé une entrée sous dérogation")
        self.assertFalse(entree.activity_ids)

    def _refus_du_cron(self, lang_responsable, lang_cron, lang_canon):
        """Faire refuser une entrée planifiée par le travail planifié, et rendre
        ce qu'il a écrit : (résumé de l'activité, note, message au chatter)."""
        self._canon(lang_canon)
        Entree = self.env["bf.editorial.entry"]
        maintenant = fields.Datetime.now()
        # Les entrées copiées de la prod ne doivent pas partir avec la nôtre.
        Entree.search([("scheduled_publish_date", "<=", maintenant),
                       ("published_date", "=", False)]).write(
            {"scheduled_publish_date": False})
        responsable = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Responsable %s" % lang_responsable,
            "login": "essai-resp-%s" % lang_responsable,
            "email": "essai-resp-%s@example.com" % lang_responsable,
            "lang": lang_responsable,
        })
        entree = self._entree(self._calendrier(), user_id=responsable.id,
                              scheduled_publish_date=maintenant - timedelta(hours=1))
        self.assertEqual(entree.qa_state, "todo")
        avant = set(entree.message_ids.ids)

        Entree.with_context(lang=lang_cron)._cron_publish_scheduled()
        entree.invalidate_recordset()
        self.assertFalse(entree.published_date)
        activite = entree.activity_ids.filtered(lambda a: a.user_id == responsable)
        self.assertEqual(len(activite), 1)
        messages = entree.message_ids.filtered(lambda m: m.id not in avant)
        self.assertEqual(len(messages), 1, "le refus doit laisser un message au chatter")
        return activite.summary, str(activite.note), str(messages.body)

    def test_refus_du_cron_dans_la_langue_du_responsable(self):
        """Le travail planifié porte une autre langue (fr_CA) que le responsable
        (en_CA) : le résumé, la note et le message au chatter partent dans la
        langue du responsable, et non dans celle du cron ou de la société."""
        resume, note, message = self._refus_du_cron("en_CA", "fr_CA", "fr_CA")
        self.assertEqual(resume, "Scheduled publication refused")
        self.assertIn("The approved date has passed", note)
        self.assertIn("The editorial QA has not been run.", note)
        self.assertIn("1 blocking point(s)", message)
        for texte in (resume, note, message):
            for francais in ("refusée", "La date approuvée", "point(s) bloquant(s)",
                             "Publication différée", "La QA éditoriale"):
                self.assertNotIn(francais, texte)

    def test_refus_du_cron_dans_la_langue_du_responsable_francophone(self):
        """Le cas inverse : cron et société en en_CA, responsable en fr_CA."""
        resume, note, message = self._refus_du_cron("fr_CA", "en_CA", "en_CA")
        self.assertEqual(resume, "Publication différée refusée")
        self.assertIn("La date approuvée est passée", note)
        self.assertIn("La QA éditoriale", note)
        self.assertIn("1 point(s) bloquant(s)", message)
        for texte in (resume, note, message):
            for anglais in ("Scheduled publication refused", "The approved date",
                            "blocking point(s)", "The editorial QA"):
                self.assertNotIn(anglais, texte)

    # ------------------------------------------------------------------
    # La QA se calcule dans la langue canonique
    # ------------------------------------------------------------------
    def test_qa_repassee_par_un_anglophone_ne_perime_pas_la_derogation(self):
        """Les constats de QA sont l'empreinte de la dérogation : repassés dans
        la langue de l'acteur, ils la périmaient sans que le texte ait bougé."""
        self._canon("fr_CA")
        billet = self._billet("<p>Un texte — avec un tiret cadratin.</p>")
        entree = self._entree(self._calendrier(), post_id=billet.id)
        self.env["bf.editorial.version"].create({
            "entry_id": entree.id, "lang_id": self.fr.id, "is_source": True,
            "state": "reviewed", "word_count": 5,
        })
        entree.with_context(lang="fr_CA").action_run_qa()
        entree.invalidate_recordset()
        self.assertEqual(entree.qa_state, "findings")
        self.assertIn("tiret(s) cadratin", entree.qa_findings)
        entree.with_context(lang="fr_CA")._sign_waiver("Citation.")

        entree.with_context(lang="en_CA").action_run_qa()
        entree.invalidate_recordset()
        self.assertIn("tiret(s) cadratin", entree.qa_findings)
        self.assertNotIn("em dash", entree.qa_findings)
        self.assertFalse(entree.qa_waiver_stale,
                         "la QA repassée en anglais a périmé la dérogation")
        self.assertEqual(self._lu_en(entree, "en_CA")._preflight_problems(), [])

    # ------------------------------------------------------------------
    # Les gabarits de liste de contrôle se copient dans la langue canonique
    # ------------------------------------------------------------------
    def test_gabarit_copie_dans_la_langue_canonique_et_pas_en_double(self):
        """Société en fr_CA, entrée créée par un anglophone : la ligne copiée porte
        le nom français du gabarit, et reposer les gabarits dans l'autre langue
        ne la dédouble pas.

        ⚠️ Le nom se pose par `update_field_translations` : sur une base réelle, en_US
        est inactif, et un nom écrit sous fr_CA est recopié en en_US ; les deux
        langues liraient alors le français et l'essai ne prouverait rien."""
        self._canon("fr_CA")
        anglais = "Check the image alt text"
        francais = "Vérifier le texte alternatif des images"
        gabarit = self.env["bf.editorial.checklist.template"].create({"name": anglais})
        gabarit.update_field_translations(
            "name", {"en_US": anglais, "en_CA": anglais, "fr_CA": francais})
        gabarit.invalidate_recordset()
        # Garde de l'essai lui-même : les deux langues doivent lire deux noms.
        self.assertEqual(gabarit.with_context(lang="en_CA").name, anglais)
        self.assertEqual(gabarit.with_context(lang="fr_CA").name, francais)

        calendrier = self._calendrier()
        entree = self.env["bf.editorial.entry"].with_context(lang="en_CA").create({
            "name": "Entrée aux gabarits", "calendar_id": calendrier.id,
        })
        noms = entree.checklist_ids.mapped("name")
        self.assertIn(francais, noms,
                      "la ligne n'a pas été copiée dans la langue canonique")
        self.assertNotIn(anglais, noms)

        for code in ("fr_CA", "en_CA"):
            entree.with_context(lang=code).action_apply_checklist_templates()
            entree.invalidate_recordset()
            lignes = entree.checklist_ids.filtered(lambda l: l.name in (anglais, francais))
            self.assertEqual(
                len(lignes), 1,
                "reposer les gabarits en %s a dédoublé la ligne" % code)

    # ------------------------------------------------------------------
    # L'état des langues se lit dans la langue du lecteur
    # ------------------------------------------------------------------
    def test_etat_des_langues_dans_la_langue_du_lecteur(self):
        calendrier = self._calendrier(
            require_all_langs="yes", lang_ids=[(6, 0, (self.fr | self.en).ids)])
        entree = self._entree(calendrier)
        self.env["bf.editorial.version"].create([
            {"entry_id": entree.id, "lang_id": self.fr.id, "is_source": True,
             "state": "translated", "word_count": 1800},
            {"entry_id": entree.id, "lang_id": self.en.id,
             "state": "reviewed", "word_count": 1700},
        ])
        en = self._lu_en(entree, "en_CA").language_summary
        self.assertIn("Translated (1800 words)", en)
        self.assertIn("Reviewed (1700 words)", en)
        self.assertNotIn("mots", en)
        fr = self._lu_en(entree, "fr_CA").language_summary
        self.assertIn("Traduite (1800 mots)", fr)
        self.assertIn("Relue (1700 mots)", fr)
        self.assertNotIn("words", fr)

    # ------------------------------------------------------------------
    # Le rapport de réparation suit la langue de qui répare
    # ------------------------------------------------------------------
    def test_rapport_de_reparation_dans_la_langue_de_l_acteur(self):
        contenu = ("<h2><br></h2><h3>Un titre</h3>"
                   "<table><thead><tr><th>Colonne</th></tr></thead></table>")
        attendus = {
            "en_CA": ("Mechanical repair applied",
                      "1 empty heading(s) replaced by a spacer",
                      "1 scope(s) set on table headers",
                      "titre(s) vide(s)"),
            "fr_CA": ("Réparation mécanique appliquée",
                      "1 titre(s) vide(s) remplacé(s) par un espacement",
                      "1 portée(s) posée(s) sur des en-têtes de tableau",
                      "empty heading"),
        }
        for code, (phrase, titres, portees, etranger) in attendus.items():
            entree = self._entree(self._calendrier(),
                                  post_id=self._billet(contenu).id)
            entree.with_context(lang=code).action_fix_mechanical()
            entree.invalidate_recordset()
            corps = [b for b in entree.message_ids.mapped("body") if phrase in (b or "")]
            self.assertEqual(len(corps), 1, "pas de trace de réparation en %s" % code)
            self.assertIn(titres, corps[0])
            self.assertIn(portees, corps[0])
            self.assertNotIn(etranger, corps[0])

    # ------------------------------------------------------------------
    # Le ratio du calendrier se lit dans la langue du lecteur
    # ------------------------------------------------------------------
    def test_ratio_du_calendrier_dans_la_langue_du_lecteur(self):
        Pilier = self.env["blog.tag.category"]
        cible = Pilier.create({"name": "Pilier ciblé essai", "is_pillar": True,
                               "target_share": 50})
        libre = Pilier.create({"name": "Pilier libre essai", "is_pillar": True})
        calendrier = self._calendrier(pillar_ids=[(6, 0, (cible | libre).ids)])
        entrees = self.env["bf.editorial.entry"]
        for i, pilier in enumerate((cible, cible, cible, libre)):
            entrees |= self._entree(calendrier, name="Ratio %s" % i,
                                    pillar_id=pilier.id)

        en = calendrier.with_context(lang="en_CA")._build_ratio_summary(entrees)
        self.assertIn("Over the last 4 publications:", en)
        self.assertIn("Pilier ciblé essai: 3 (75%), target 50%, gap +25", en)
        self.assertIn("Pilier libre essai: 1 (25%)", en)
        self.assertNotIn("cible", en)

        fr = calendrier.with_context(lang="fr_CA")._build_ratio_summary(entrees)
        self.assertIn("Sur les 4 dernières publications :", fr)
        self.assertIn("Pilier ciblé essai : 3 (75 %) — cible 50 %, écart +25", fr)
        self.assertIn("Pilier libre essai : 1 (25 %)", fr)
        self.assertNotIn("target", fr)
