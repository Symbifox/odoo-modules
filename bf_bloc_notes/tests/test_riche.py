"""La mise en forme du bloc-notes (lot serveur).

La barre du widget de bureau écrit du gras, de l'italique, du souligné, du
barré et des puces. Le serveur doit accepter ce sous-ensemble, le rendre dans
une forme STABLE, et dire honnêtement quand une note n'y tient pas — sinon le
premier enregistrement du widget efface ce qu'il ne sait pas écrire.

Quatre promesses, une classe d'essais chacune :

1. ``_rich_assainir`` ne garde que le sous-ensemble, et rend la MÊME chaîne
   pour un même corps (sinon chaque aller-retour « modifie » la note).
2. ``_rich_est_modifiable`` ne dit vrai que si l'aller-retour ne perd RIEN —
   et dit vrai quand il ne perd rien, ce qui est l'autre moitié du contrat.
3. Le contrat (`html`, `rich_editable`, l'écriture) ne touche pas au contrat
   du téléphone (`text`, `editable`), celui de l'application posée.
4. La LECTURE ne lève jamais, et les droits passent avant la forme.
"""

import json
import uuid
from types import SimpleNamespace
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.tests.common import HttpCase, TransactionCase, new_test_user, tagged

from ..models.bf_note_mobile import MAX_HTML, MAX_HTML_LECTURE, MAX_TEXT

BASE = "/bf_bloc_notes/mobile/v1"

#: Ce que Qt rend quand on lui demande le HTML d'un `TextEdit` riche : un
#: document complet, et le gras porté par un `style`, jamais par `<b>`.
QT = """<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.0//EN" "http://www.w3.org/TR/REC-html40/strict.dtd">
<html><head><meta name="qrichtext" content="1" /><meta charset="utf-8" />
<style type="text/css">p, li { white-space: pre-wrap; }</style></head>
<body style=" font-family:'Noto Sans'; font-size:10pt; font-weight:400; font-style:normal;">
<p style=" margin-top:0px; -qt-block-indent:0; text-indent:0px;">Liste de courses</p>
<p style=" margin-top:0px;"><span style=" font-weight:700;">Lait</span> et <span style=" font-style:italic;">pain</span></p>
<p style=" margin-top:0px;"><span style=" text-decoration: underline;">Souligne</span> <span style=" text-decoration: line-through;">barre</span></p>
</body></html>"""

#: Entrée → sortie attendue. Chaque ligne est aussi éprouvée en POINT FIXE :
#: assainir deux fois rend la même chose.
ASSAINIS = [
    ("vide", "", ""),
    ("texte nu", "bonjour", "<p>bonjour</p>"),
    ("un paragraphe", "<p>a</p>", "<p>a</p>"),
    ("deux paragraphes", "<p>a</p><p>b</p>", "<p>a</p><p>b</p>"),
    ("ligne vide au milieu", "<p>a</p><p><br></p><p>b</p>", "<p>a</p><p><br></p><p>b</p>"),
    ("ligne vide a la fin", "<p>a</p><p><br></p>", "<p>a</p>"),
    ("gras", "<p><b>a</b></p>", "<p><b>a</b></p>"),
    ("gras synonyme", "<p><strong>a</strong></p>", "<p><b>a</b></p>"),
    ("italique synonyme", "<p><em>a</em></p>", "<p><i>a</i></p>"),
    ("barre synonymes", "<p><del>a</del><strike>b</strike></p>", "<p><s>a</s><s>b</s></p>"),
    ("gras par style 700", '<p><span style="font-weight:700">a</span></p>', "<p><b>a</b></p>"),
    ("gras par style 600", '<p><span style="font-weight:600">a</span></p>', "<p><b>a</b></p>"),
    ("gras par style bold", '<p><span style="font-weight:bold">a</span></p>', "<p><b>a</b></p>"),
    ("poids normal : rien", '<p><span style="font-weight:400">a</span></p>', "<p>a</p>"),
    ("italique par style", '<p><span style="font-style:italic">a</span></p>', "<p><i>a</i></p>"),
    ("souligne par style", '<p><span style="text-decoration: underline">a</span></p>', "<p><u>a</u></p>"),
    ("barre par style", '<p><span style="text-decoration: line-through">a</span></p>', "<p><s>a</s></p>"),
    ("deux decorations", '<p><span style="text-decoration: underline line-through">a</span></p>',
     "<p><u><s>a</s></u></p>"),
    ("espace dans le style", '<p><span style=" font-weight:700;">a</span></p>', "<p><b>a</b></p>"),
    ("imbrication", "<p><b><i>a</i></b></p>", "<p><b><i>a</i></b></p>"),
    ("effet colle au texte", "<p>a<b>b</b>c</p>", "<p>a<b>b</b>c</p>"),
    ("gras dans gras", '<p><b><span style="font-weight:700">a</span></b></p>', "<p><b>a</b></p>"),
    ("effet herite d un bloc", '<div style="font-weight:700"><p>a</p></div>', "<p>a</p>"),
    # 🔴 Trouvé en relecture : l'assainisseur AJOUTAIT du gras. Un
    # style peut ANNULER l'effet du parent, et ajouter de la mise en forme est
    # pire que d'en perdre — personne ne vérifie ce qu'il n'a pas demandé.
    ("le mot-cle normal annule le gras",
     '<p><b>gras <span style="font-weight:normal">net</span></b></p>',
     "<p><b>gras </b>net</p>"),
    ("le poids 400 annule le gras",
     '<p><b>gras <span style="font-weight:400">net</span></b></p>',
     "<p><b>gras </b>net</p>"),
    ("le mot-cle normal annule l italique",
     '<p><i>pente <span style="font-style:normal">net</span></i></p>',
     "<p><i>pente </i>net</p>"),
    ("decoration none annule le souligne",
     '<p><u>sous <span style="text-decoration:none">net</span></u></p>',
     "<p><u>sous </u>net</p>"),
    ("puces", "<ul><li>a</li><li>b</li></ul>", "<ul><li>a</li><li>b</li></ul>"),
    ("un item imbrique herite de l effet de son parent",
     '<ul><li style="font-weight:700">gras<ul><li>encore</li></ul></li></ul>',
     "<ul><li><b>gras</b></li><li><b>encore</b></li></ul>"),
    ("un item imbrique peut ANNULER l effet de son parent",
     '<ul><li style="font-weight:700">gras'
     '<ul><li style="font-weight:normal">net</li></ul></li></ul>',
     "<ul><li><b>gras</b></li><li>net</li></ul>"),
    ("numerotee", "<ol><li>a</li></ol>", "<ol><li>a</li></ol>"),
    ("puce en gras", "<ul><li><b>a</b></li></ul>", "<ul><li><b>a</b></li></ul>"),
    ("liste imbriquee aplatie", "<ul><li>a<ul><li>b</li></ul></li></ul>",
     "<ul><li>a</li><li>b</li></ul>"),
    ("liste puis texte", "<ul><li>a</li></ul>suite", "<ul><li>a</li></ul><p>suite</p>"),
    ("texte nu puis liste", "avant<ul><li>b</li></ul>", "<p>avant</p><ul><li>b</li></ul>"),
    ("ul sans li", "<ul>texte</ul>", ""),
    ("lien deplie EN LIGNE", '<p>voir <a href="https://x.test">ici</a> merci</p>',
     "<p>voir ici merci</p>"),
    ("image perdue", '<p>a<img src="https://x.test/p.png"/></p>', "<p>a</p>"),
    ("script muet", "<p>a</p><script>alert(1)</script>", "<p>a</p>"),
    ("script muet sous un bloc", "<p>a<script>alert(1)</script>b</p>", "<p>ab</p>"),
    ("style muet sous un bloc", "<p>a<style>p{color:red}</style>b</p>", "<p>ab</p>"),
    # ⚠️ Deux chemins, deux gardes : un muet enfant d'un BLOC passe par
    # `bloc()`, un muet enfant d'un EFFET par `_en_ligne()`. Mesuré par
    # mutation : sans ce cas-ci, retirer la garde du second ne cassait rien.
    ("script muet sous un effet", "<p>a<b>g<script>alert(1)</script></b>b</p>",
     "<p>a<b>g</b>b</p>"),
    ("style muet sous un effet", "<p><b>g<style>p{}</style>h</b></p>",
     "<p><b>g</b><b>h</b></p>"),
    ("onerror tombe", '<p><img src=x onerror="alert(1)">a</p>', "<p>a</p>"),
    ("titre coupe le paragraphe", "<h1>Titre</h1><p>a</p>", "<p>Titre</p><p>a</p>"),
    # 🔴 Le cas doit être IMBRIQUÉ. Au premier niveau, chaque fragment devient
    # un bloc de toute façon : `<center>a</center><p>b</p>` rend `<p>a</p><p>b</p>`
    # même si `center` n'est plus dans `RICH_BLOCS`. Mesuré en mutant la liste —
    # le cas plat survivait. Imbriqué, le mutant colle les trois en `undeuxtrois`.
    ("center coupe le paragraphe (imbrique)",
     "<div>un<center>deux</center>trois</div>",
     "<p>un</p><p>deux</p><p>trois</p>"),
    ("tableau aplati", "<table><tr><td>a</td><td>b</td></tr></table>", "<p>a</p><p>b</p>"),
    ("couleur perdue", '<p><span style="color:red">a</span></p>', "<p>a</p>"),
    ("attributs tombes", '<p class="x" id="y" onclick="z()">a</p>', "<p>a</p>"),
    ("entites gardees", "<p>a &lt; b &amp; c</p>", "<p>a &lt; b &amp; c</p>"),
    ("chevron brut echappe", "<p>5 < 6</p>", "<p>5 &lt; 6</p>"),
    ("entite numerique echappee", "<p>&#60;script&#62;</p>", "<p>&lt;script&gt;</p>"),
    ("commentaire perdu", "<p>a</p><!-- secret --><p>b</p>", "<p>a</p><p>b</p>"),
    ("saut de ligne garde", "<p>a<br>b</p>", "<p>a<br>b</p>"),
    ("balise non fermee", "<p>a<b>b", "<p>a<b>b</b></p>"),
    ("document Qt", QT,
     "<p>Liste de courses</p><p><b>Lait</b> et <i>pain</i></p>"
     "<p><u>Souligne</u> <s>barre</s></p>"),
]

#: Corps → la note est-elle modifiable à la barre ?
#:
#: 🔴 Les sept premiers cas « vrais » sont ceux qui ont fait échouer la
#: première version du témoin. Il comparait un texte tiré par expression
#: régulière du brut à un texte tiré de l'arbre assaini : les deux
#: normalisations n'étaient pas comparables, et UNE ESPACE au bord d'un bloc —
#: ou un simple retour à la ligne entre deux balises, ce que fait tout éditeur
#: — rendait la note NON modifiable. La barre se serait refermée sur les seules
#: notes qu'elle avait elle-même écrites.
MODIFIABLES = [
    ("vide", "", True),
    ("texte nu", "bonjour", True),
    ("paragraphe", "<p>a</p>", True),
    ("espace au bord d un bloc", "<p>a </p><p>b</p>", True),
    ("espace insecable au bord", "<p>a&nbsp;</p><p>b</p>", True),
    ("retour a la ligne entre deux balises", "<p>a</p>\n<p>b</p>", True),
    ("enveloppe de l editeur indentee",
     '<div data-oe-version="1.0">\n  <p>a</p>\n  <p>b</p>\n</div>', True),
    ("espace dans un item", "<ul><li>lait </li><li>pain</li></ul>", True),
    ("espace avant une liste", "<p>Courses </p><ul><li>lait</li></ul>", True),
    ("gras", "<p><b>a</b></p>", True),
    ("synonymes", "<p><strong><em>a</em></strong></p>", True),
    ("style traduisible", '<p><span style="font-weight:700">a</span></p>', True),
    ("style qui annule", '<p><b>g<span style="font-weight:normal">n</span></b></p>', True),
    ("classe heritee", '<p class="o_default">a</p>', True),
    ("puces", "<ul><li>a</li></ul>", True),
    ("saut de ligne dans un bloc", "<p>a<br>b</p>", True),
    ("effet colle au texte", "<p>a<b>b</b>c</p>", True),
    ("couleur", '<p><span style="color:red">a</span></p>', False),
    ("taille", '<p><span style="font-size:20px">a</span></p>', False),
    ("couleur ET gras", '<p><span style="color:red;font-weight:700">a</span></p>', False),
    ("lien", '<p><a href="https://x.test">a</a></p>', False),
    ("image", '<p><img src="x.png"/></p>', False),
    ("tableau", "<table><tr><td>a</td></tr></table>", False),
    ("titre", "<h1>a</h1>", False),
    ("nom de balise a trait d union", "<p><b-x>a</b-x></p>", False),
    ("nom de balise a deux points", "<p><span:x>a</span:x></p>", False),
    # 🔴 Le cas qui a fait resserrer `_NOMS_DE_BALISE` : l'analyseur TRONQUE
    # l'imbrication, l'élément caché n'est donc dans aucun arbre, et il ne
    # porte pas de texte — rien ne le voyait. Le nom entier, lui, est là.
    ("un nom tronque sous une imbrication que l analyseur abandonne",
     "<p>ok</p>" + "<div>" * 400
     + "<div-onclick onclick='alert(1)'></div-onclick>" + "</div>" * 400,
     False),
    ("commentaire", "<p>a</p><!-- x -->", False),
    ("attribut inconnu", '<p data-oe-model="res.partner">a</p>', False),
    ("document Qt brut", QT, False),
    # 🔴 Trois formes que l'assainisseur abîme, trouvées en relecture.
    ("deux paragraphes dans un item", "<ul><li><p>un</p><p>deux</p></li></ul>", False),
    ("texte puis paragraphe dans un item", "<ul><li>Lait<p>2 litres</p></li></ul>", False),
    ("liste enveloppee d un effet", "<b><ul><li>a</li><li>b</li></ul></b>", False),
    ("blocs enveloppes d un span", "<span><p>a</p><p>b</p></span>", False),
    ("li hors liste", "<li>a</li>", False),
    ("br hors bloc", "<p>a</p><br><p>b</p>", False),
    ("liste imbriquee", "<ul><li>a<ul><li>b</li></ul></li></ul>", False),
    # 🔴 La liste blanche dirait OUI : `ul` est permis, aucun attribut, aucune
    # imbrication. C'est le témoin des LIGNES qui refuse — l'assainisseur perd
    # ce « texte » posé hors de tout `<li>`.
    ("ul avec du texte hors li", "<ul>texte</ul>", False),
    # 🔴 Et celui-ci n'est vu QUE par le témoin sans analyseur : libxml2
    # tronque vers 255 niveaux, donc l'`<img>` n'est pas dans l'arbre et la
    # liste blanche ne voit que des `div`. La note revenait « modifiable » avec
    # un `html` vide, et le premier enregistrement l'aurait effacée.
    ("image sous 400 div", "<div>" * 400 + '<img src="/web/image/1"/>' + "</div>" * 400,
     False),
]


@tagged("post_install", "-at_install", "bf_bloc_notes")
class TestRicheAssainisseur(TransactionCase):
    """L'assainisseur et le juge, appelés directement : aucune écriture."""

    def test_l_assainisseur_ramene_au_sous_ensemble(self):
        Note = self.env["bf.note"]
        for nom, entree, attendu in ASSAINIS:
            with self.subTest(cas=nom):
                self.assertEqual(str(Note._mobile_rich_sanitize(entree)), attendu)

    def test_assainir_deux_fois_ne_change_plus_rien(self):
        """Point fixe. Sans lui, le widget renverrait une forme différente de
        celle qu'il a reçue, et chaque ouverture de note l'« modifierait »."""
        Note = self.env["bf.note"]
        for nom, entree, _attendu in ASSAINIS:
            with self.subTest(cas=nom):
                une = str(Note._mobile_rich_sanitize(entree))
                self.assertEqual(str(Note._mobile_rich_sanitize(une)), une)

    def test_ce_qui_est_modifiable_a_la_barre(self):
        Note = self.env["bf.note"]
        for nom, corps, attendu in MODIFIABLES:
            with self.subTest(cas=nom):
                self.assertEqual(Note._mobile_rich_is_safe(corps), attendu)

    def test_une_forme_canonique_est_toujours_modifiable(self):
        """L'autre moitié du contrat : ce que l'assainisseur rend, la barre
        doit pouvoir le renvoyer. Un sous-ensemble dont la sortie n'est pas
        réacceptée ne sert à rien."""
        Note = self.env["bf.note"]
        for nom, entree, _attendu in ASSAINIS:
            with self.subTest(cas=nom):
                propre = str(Note._mobile_rich_sanitize(entree))
                self.assertTrue(Note._mobile_rich_is_safe(propre), propre)

    def test_un_corps_pathologique_ne_promet_jamais_un_html_vide(self):
        """Mille `<div>` imbriqués. Selon la version de libxml2, l'analyseur
        les traverse ou les tronque — on ne choisit pas celle qui tourne en
        production, donc l'essai ne fige pas le verdict. L'invariant, lui, ne
        dépend de personne : un corps dont le texte n'a pas survécu à
        l'assainissement ne doit JAMAIS être annoncé modifiable.
        """
        Note = self.env["bf.note"]
        corps = "<div>" * 1000 + "texte" + "</div>" * 1000
        if "texte" in str(Note._mobile_rich_sanitize(corps)):
            self.assertTrue(Note._mobile_rich_is_safe(corps))
        else:
            self.assertFalse(Note._mobile_rich_is_safe(corps))

    def test_le_temoin_reste_lineaire(self):
        """🔴 Une première version du témoin retirait le CONTENU des muets par
        un `.*?` entre balise ouvrante et fermante : quadratique. Mesuré en
        relecture, 8 000 `<script ` non fermés mettaient 3,4 s de processeur,
        et la ceinture tourne deux fois par note, 200 fois par appel de liste.
        Le corps ci-dessous est précisément celui qui y donnait accès :
        l'analyseur tronque au-delà de ~255 niveaux, donc la liste blanche ne
        voit que des `div` et laisse passer jusqu'au témoin.

        La borne est très large exprès : linéaire, c'est quelques
        millisecondes ; quadratique, c'est une dizaine de secondes.
        """
        import time
        corps = "<div>" * 300 + "<script " * 16_000 + "</div>" * 300
        depart = time.monotonic()
        self.assertFalse(self.env["bf.note"]._mobile_rich_is_safe(corps))
        self.assertLess(time.monotonic() - depart, 5.0)

    def test_un_corps_trop_gros_est_refuse_A_L_ECRITURE(self):
        Note = self.env["bf.note"]
        with self.assertRaises(UserError):
            Note._mobile_rich_sanitize("<p>" + "a" * (MAX_HTML + 1) + "</p>")
        # Témoin positif : juste en dessous du plafond, ça passe.
        self.assertTrue(str(Note._mobile_rich_sanitize(
            "<p>" + "a" * (MAX_HTML - 10) + "</p>")).startswith("<p>a"))

    def test_une_valeur_qui_n_est_pas_une_chaine_est_refusee(self):
        """🔴 Une première version faisait `str(html)`, et un `"html": false`
        écrivait donc le mot « False » dans la note. Le module tranche déjà
        ainsi pour la clé voisine (« Hors Gen » exige un vrai booléen)."""
        Note = self.env["bf.note"]
        for valeur in (None, False, 12, [], {"a": 1}):
            with self.subTest(valeur=valeur), self.assertRaises(UserError):
                Note._mobile_rich_sanitize(valeur)


@tagged("post_install", "-at_install", "bf_bloc_notes")
class TestRicheContrat(TransactionCase):
    """Ce que la charge porte, et ce que l'écriture accepte."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        groupes = "base.group_user,project.group_project_user"
        cls.alice = new_test_user(cls.env, login="alice_riche", groups=groupes,
                                  email="alice.r@example.com")
        cls.bruno = new_test_user(cls.env, login="bruno_riche", groups=groupes,
                                  email="bruno.r@example.com")
        cls.patron = new_test_user(cls.env, login="patron_riche",
                                   groups=groupes + ",base.group_system",
                                   email="patron.r@example.com")
        cls.Note = cls.env["bf.note"].with_user(cls.alice)
        #: La barre demande explicitement `html` ; le téléphone ne le fait pas.
        cls.Riche = cls.Note.with_context(bf_note_rich=True)

    def _creer(self, **vals):
        vals.setdefault("client_uuid", str(uuid.uuid4()))
        resultat = self.Riche._mobile_create(vals)
        return self.Riche.browse(resultat["note"]["id"]), resultat

    def _corps_brut(self, note, html):
        """Pose un corps SANS passer par l'assainisseur du champ.

        Les notes de la base ont été écrites sur des années par des éditeurs
        différents : ce qu'un `write` laisse passer aujourd'hui ne dit pas ce
        qu'elles portent. L'essai doit donc pouvoir fabriquer n'importe quel
        corps, pas seulement ceux qu'Odoo accepterait ce matin.
        """
        self.env.cr.execute("UPDATE bf_note SET body = %s WHERE id = %s",
                            (html, note.id))
        note.invalidate_recordset(["body"])

    # ── la charge ────────────────────────────────────────────────────
    def test_la_charge_porte_le_html_et_la_permission_sur_demande(self):
        note, resultat = self._creer(text="Simple")
        self.assertEqual(resultat["note"]["html"], "<p>Simple</p>")
        self.assertTrue(resultat["note"]["rich_editable"])

    def test_sans_demande_la_charge_du_telephone_ne_change_pas(self):
        """🔴 Mesuré en relecture : servir `html` à tout le monde doublait la
        part texte de la charge (+117 %) et ajoutait une analyse par note, au
        profit de personne — ni l'application posée ni la page ne lisent cette
        clé. Elle ne sort donc que sur demande."""
        note, _r = self._creer(text="Simple")
        nue = self.Note.browse(note.id)._mobile_payload()
        self.assertNotIn("html", nue)
        self.assertNotIn("rich_editable", nue)
        self.assertIn("text", nue)
        self.assertIn("editable", nue)

    def test_une_note_sans_corps_ne_rend_pas_le_mot_False(self):
        """🔴 `fields.Html` rend `False`, jamais `None`, pour une colonne
        NULL. Une garde écrite `if html is None` laissait donc passer
        `str(False)` : toute note créée sans corps — ce que fait la création
        rapide du kanban comme tout script — se servait avec
        `html: "<p>False</p>"` et `rich_editable: true`. Le widget affichait
        « False » et avait le droit de l'enregistrer.
        """
        note = self.Riche.create({"name": "Sans corps"})
        self.assertFalse(note.body)
        charge = note._mobile_payload()
        self.assertEqual(charge["html"], "")
        self.assertEqual(charge["text"], "")
        self.assertTrue(charge["editable"])

    def test_le_html_servi_est_toujours_assaini(self):
        """Même sur une note riche : le widget ne doit jamais recevoir une
        image distante ni un tableau, qu'il ne sait pas peindre."""
        note, _r = self._creer(text="x")
        self._corps_brut(note, '<p>Avant<img src="https://pisteur.test/p.gif"/></p>'
                               '<table><tr><td>cellule</td></tr></table>')
        charge = note._mobile_payload()
        self.assertNotIn("img", charge["html"])
        self.assertNotIn("table", charge["html"])
        self.assertIn("Avant", charge["html"])
        self.assertFalse(charge["rich_editable"])

    def test_la_mise_en_forme_ne_touche_pas_au_contrat_du_telephone(self):
        """`text` et `editable` sont le contrat de l'application posée. Une
        note mise en forme y devient affichée, pas modifiable — la même règle
        qu'avant ce lot, parce que le téléphone n'écrit que du texte."""
        note, _r = self._creer(text="x")
        note._mobile_update({"html": "<p>Du <b>gras</b></p>"})
        charge = note._mobile_payload()
        self.assertTrue(charge["rich_editable"])
        self.assertFalse(charge["editable"])
        self.assertEqual(charge["text"], "Du gras")

    def test_une_note_sans_mise_en_forme_reste_modifiable_au_telephone(self):
        """Le widget écrira du `html` pour TOUTE note. Sans mise en forme, la
        note doit rester du texte simple, sinon le téléphone perdrait le droit
        de modifier toutes les notes du bureau."""
        note, _r = self._creer(text="x")
        note._mobile_update({"html": "<p>Juste du texte</p><p>deux lignes</p>"})
        charge = note._mobile_payload()
        self.assertTrue(charge["editable"])
        self.assertEqual(charge["text"], "Juste du texte\ndeux lignes")

    def test_les_puces_se_lisent_au_telephone(self):
        """Le téléphone n'a que `text` : une liste doit s'y voir comme une
        liste, pas comme deux lignes nues ni comme du balisage."""
        note, _r = self._creer(text="x")
        note._mobile_update({"html": "<p>Courses</p><ul><li>lait</li><li>pain</li></ul>"})
        self.assertEqual(note._mobile_payload()["text"], "Courses\n• lait\n• pain")
        note._mobile_update({"html": "<ol><li>un</li><li>deux</li></ol>"})
        self.assertEqual(note._mobile_payload()["text"], "1. un\n2. deux")

    def test_un_saut_de_ligne_se_lit_au_telephone(self):
        """⚠️ Il faut de la mise en forme POUR que ce chemin soit pris : une
        note qui n'a que des `<br>` est du texte simple, donc elle passe par
        l'autre branche."""
        note, _r = self._creer(text="x")
        note._mobile_update({"html": "<p>une<br><b>deux</b></p>"})
        charge = note._mobile_payload()
        self.assertFalse(charge["editable"])        # témoin : c'est bien riche
        self.assertEqual(charge["text"], "une\ndeux")

    # ── la LECTURE ne lève jamais ────────────────────────────────────
    def test_un_corps_plus_gros_que_le_plafond_d_ecriture_se_LIT_quand_meme(self):
        """🔴 Le plafond d'écriture appliqué à la lecture était un déni de
        service : `_mobile_payload` assainit chaque note, donc UNE note de plus
        de 400 Ko — un collage long au formulaire du bureau, une image `data:`
        en base64 — faisait rendre 400 à la LISTE ENTIÈRE, sur les deux portes.
        Avec `shared`, la note d'un collègue suffisait à éteindre le widget de
        tout le monde.
        """
        note, _r = self._creer(text="x")
        self._corps_brut(note, "<p>" + "a" * (MAX_HTML + 50_000) + "</p>")
        charge = note._mobile_payload()          # ne doit PAS lever
        self.assertTrue(charge["html"].startswith("<p>a"))
        self.assertTrue(charge["rich_editable"])
        # Et la liste entière tient debout.
        liste = self.Riche._mobile_list(limit=50)
        self.assertIn(note.id, [n["id"] for n in liste["notes"]])

    def test_un_corps_enorme_degrade_au_lieu_de_lever(self):
        note, _r = self._creer(text="x")
        self._corps_brut(note, "<p>" + "a" * (MAX_HTML_LECTURE + 10) + "</p>")
        charge = note._mobile_payload()          # ne doit PAS lever
        self.assertEqual(charge["html"], "")
        self.assertFalse(charge["rich_editable"])
        self.assertIn("a", charge["text"])

    def test_une_note_au_plafond_de_texte_se_cree_ET_se_relit(self):
        """🔴 `_mobile_text_to_html` enveloppe chaque ligne : 100 000
        caractères de lignes courtes font un corps de plus de 600 Ko. Avec le
        plafond d'écriture sur le chemin de lecture, la création elle-même
        levait — le téléphone recevait un refus parlant d'un plafond de HTML
        qu'il n'avait jamais dépassé, et la note était perdue."""
        texte = "a\n\n" * (MAX_TEXT // 3)
        note, resultat = self._creer(text=texte)
        self.assertTrue(resultat["created"])
        self.assertTrue(len(str(note.body)) > MAX_HTML)
        self.assertTrue(note._mobile_payload()["editable"])

    # ── l'écriture ───────────────────────────────────────────────────
    def test_ecrire_du_html_enregistre_la_forme_canonique(self):
        note, _r = self._creer(text="x")
        note._mobile_update({"html": '<p><span style="font-weight:700">Lait</span></p>'})
        self.assertEqual(note._mobile_payload()["html"], "<p><b>Lait</b></p>")

    def test_ecrire_du_html_sur_une_note_riche_est_refuse(self):
        note, _r = self._creer(text="x")
        self._corps_brut(note, '<p>Voir <a href="https://x.test">le dossier</a></p>')
        with self.assertRaises(UserError):
            note._mobile_update({"html": "<p>écrasé</p>"})
        # Témoin positif : la même écriture passe sur une note du sous-ensemble.
        self._corps_brut(note, "<p>simple</p>")
        note._mobile_update({"html": "<p>écrasé</p>"})
        self.assertEqual(note._mobile_payload()["html"], "<p>écrasé</p>")

    def test_une_perte_sur_le_html_RECU_est_annoncee(self):
        """🔴 La garde ne regardait que l'ancien corps. Coller un lien depuis
        un navigateur dans l'éditeur du widget envoie un `<a href>`, que
        l'assainisseur déplie en silence : l'adresse partait pour de bon et le
        serveur répondait « rien n'a été perdu ». On accepte — un refus
        bloquerait un collage légitime sans issue — mais on le DIT."""
        note, _r = self._creer(text="x")
        perdu = note._mobile_update({
            "html": '<p>voir <a href="https://nextcloud.test/dossier">le dossier</a></p>'})
        self.assertTrue(perdu["flattened"])
        self.assertEqual(perdu["note"]["html"], "<p>voir le dossier</p>")
        # Témoin négatif : rien de perdu, rien d'annoncé.
        garde = note._mobile_update({"html": "<p>du <b>gras</b></p>"})
        self.assertFalse(garde["flattened"])
        # Et à la création aussi.
        _n, cree = self._creer(html='<p><a href="https://x.test">a</a></p>')
        self.assertTrue(cree["flattened"])

    def test_texte_et_html_ensemble_sont_refuses(self):
        """L'ordre des clés d'un objet JSON n'engage personne : deviner lequel
        gagne, c'est écraser l'autre en silence."""
        note, _r = self._creer(text="x")
        with self.assertRaises(UserError):
            note._mobile_update({"text": "a", "html": "<p>b</p>"})
        with self.assertRaises(UserError):
            self.Riche._mobile_create({"client_uuid": str(uuid.uuid4()),
                                       "text": "a", "html": "<p>b</p>"})

    def test_creer_une_note_en_html(self):
        note, resultat = self._creer(
            html='<p>Des <span style="font-weight:700">courses</span></p>'
                 "<ul><li>lait</li></ul>")
        attendu = "<p>Des <b>courses</b></p><ul><li>lait</li></ul>"
        self.assertEqual(resultat["note"]["html"], attendu)
        self.assertEqual(resultat["note"]["title"], "Des courses")
        # 🔴 Et c'est la forme CANONIQUE qui est en base, pas celle reçue :
        # assainir seulement à la lecture laisserait du `style` dans le corps.
        self.assertEqual(str(note.body), attendu)

    def test_creer_une_note_vide_en_html_est_refuse(self):
        with self.assertRaises(UserError):
            self.Riche._mobile_create({"client_uuid": str(uuid.uuid4()),
                                       "html": "<p><br></p>"})

    def test_la_puce_ne_va_pas_dans_le_titre(self):
        """🔴 Une liste de courses sans titre s'appelait « • lait » au kanban,
        et ce nom repart dans le résumé de l'activité et dans le nom de la
        tâche qu'on tire de la note."""
        _note, resultat = self._creer(html="<ul><li>lait</li><li>pain</li></ul>")
        self.assertEqual(resultat["note"]["title"], "lait")
        _n2, r2 = self._creer(html="<ol><li>premier</li></ol>")
        self.assertEqual(r2["note"]["title"], "premier")

    def test_le_retrait_de_puce_ne_touche_pas_au_tiret_d_une_personne(self):
        """Le titre ne perd QUE les puces que le serveur fabrique (`• `,
        `1. `). Un tiret, une personne l'a écrit : il reste. Sans cet essai,
        élargir le motif au tiret passait inaperçu, et toute note commençant
        par « - » perdait son tiret dans son nom au kanban."""
        _note, resultat = self._creer(text="- lait\n- pain")
        self.assertEqual(resultat["note"]["title"], "- lait")

    def test_vider_le_titre_le_recompose_depuis_le_html(self):
        note, _r = self._creer(html="<p>Premier</p><p>second</p>")
        note._mobile_update({"html": "<p>Nouveau <b>debut</b></p><p>suite</p>",
                             "title": ""})
        self.assertEqual(note._mobile_payload()["title"], "Nouveau debut")

    def test_le_html_recu_ne_peut_pas_porter_de_script(self):
        note, _r = self._creer(text="x")
        note._mobile_update({"html": '<p>a</p><script>alert(1)</script>'
                                     '<p onclick="vol()">b</p>'})
        self.assertEqual(note._mobile_payload()["html"], "<p>a</p><p>b</p>")
        self.assertNotIn("script", str(note.body))
        self.assertNotIn("onclick", str(note.body))

    def test_un_html_trop_gros_est_refuse_a_l_ecriture(self):
        note, _r = self._creer(text="x")
        with self.assertRaises(UserError):
            note._mobile_update({"html": "<p>" + "a" * (MAX_HTML + 1) + "</p>"})

    def test_une_ligne_vide_du_milieu_survit_a_l_aller_retour_par_le_champ(self):
        """⚠️ Le champ `body` est `sanitize=True` : si Odoo touchait au
        `<p><br></p>`, le `html` servi différerait du corps et chaque ouverture
        de note l'« modifierait »."""
        note, _r = self._creer(text="x")
        envoye = "<p>a</p><p><br></p><p>b</p>"
        note._mobile_update({"html": envoye})
        self.assertEqual(str(note.body), envoye)
        self.assertEqual(note._mobile_payload()["html"], envoye)

    # ── les droits passent avant la forme ────────────────────────────
    def test_ecrire_du_html_sur_la_note_partagee_d_un_collegue_est_refuse(self):
        """Le partage est en LECTURE seule. La clé neuve doit être refusée
        comme l'ancienne, et par le DROIT, pas par la forme."""
        note, _r = self._creer(text="à moi")
        note.is_shared = True
        chez_bruno = self.env["bf.note"].with_user(self.bruno).with_context(
            bf_note_rich=True).browse(note.id)
        with self.assertRaises(AccessError):
            chez_bruno._mobile_update({"html": "<p>piraté</p>"})
        # 🔴 Et même quand le corps sort du sous-ensemble : sinon Bruno
        # recevrait « cette note a une mise en forme que la barre ne sait pas
        # garder, modifiez-la dans Symbifox » — le mauvais motif, et un conseil
        # qui ne mène nulle part.
        self._corps_brut(note, '<p>Voir <a href="https://x.test">ici</a></p>')
        chez_bruno.invalidate_recordset(["body"])
        with self.assertRaises(AccessError):
            chez_bruno._mobile_update({"html": "<p>piraté</p>"})
        self.assertNotIn("piraté", str(note.body))

    def test_un_administrateur_n_ouvre_pas_une_note_privee_par_son_identifiant(self):
        """🔴 Les règles d'un même modèle se combinent en OU, et ce module en a
        une pour `base.group_system` (`[(1, '=', 1)]`) : `check_access` seul
        laissait LIRE et ÉCRIRE n'importe quelle note de la base par son
        identifiant, et les identifiants sont séquentiels. Les trois lectures
        de portée avaient été fermées ; cette quatrième surface ne l'était pas.
        """
        privee, _r = self._creer(text="privé")
        Chez = self.env["bf.note"].with_user(self.patron)
        self.assertIsNone(Chez._mobile_browse(privee.id))
        # Témoins positifs : sa propre note, et une note partagée.
        sienne = Chez.create({"name": "la mienne", "body": "<p>x</p>"})
        self.assertTrue(Chez._mobile_browse(sienne.id))
        privee.is_shared = True
        self.assertTrue(Chez._mobile_browse(privee.id))


@tagged("post_install", "-at_install", "bf_bloc_notes")
class TestRicheHttp(HttpCase):
    """Le sous-ensemble par la porte à JETON : celle du widget de bureau."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.alice = new_test_user(
            cls.env, login="alice_riche_http", password="alice_riche_http",
            email="alice.h@example.com",
            groups="base.group_user,project.group_project_user")

    def _authentifie(self):
        return patch(
            "odoo.addons.bf_bloc_notes.controllers.mobile_api._device",
            lambda: SimpleNamespace(user_id=self.alice, _fields={}),
        )

    def _post(self, route, charge, riche=True):
        entetes = {"Content-Type": "application/json"}
        if riche:
            entetes["X-Symbifox-Rich"] = "1"
        reponse = self.url_open(route, data=json.dumps(charge), headers=entetes)
        return reponse.status_code, reponse.json()

    def _get(self, route, riche=True):
        entetes = {"X-Symbifox-Rich": "1"} if riche else {}
        reponse = self.url_open(route, headers=entetes)
        return reponse.status_code, reponse.json()

    def test_le_ping_annonce_la_mise_en_forme(self):
        self.assertIn("rich_text", self.url_open(f"{BASE}/ping").json()["features"])

    def test_aller_retour_complet_par_la_porte_a_jeton(self):
        """Ce que le widget fera : créer, relire, renvoyer la même chaîne."""
        with self._authentifie():
            code, cree = self._post(f"{BASE}/notes", {
                "client_uuid": str(uuid.uuid4()),
                "html": '<p>Des <span style="font-weight:700">courses</span></p>'
                        "<ul><li>lait</li><li>pain</li></ul>"})
            self.assertEqual(code, 201, cree)
            attendu = "<p>Des <b>courses</b></p><ul><li>lait</li><li>pain</li></ul>"
            self.assertEqual(cree["note"]["html"], attendu)
            self.assertTrue(cree["note"]["rich_editable"])
            note_id = cree["note"]["id"]
            # Renvoyer TEL QUEL ce qu'on a reçu ne doit rien changer.
            code, modifie = self._post(f"{BASE}/notes/{note_id}",
                                       {"html": cree["note"]["html"]})
            self.assertEqual(code, 200, modifie)
            self.assertEqual(modifie["note"]["html"], attendu)
            # Et la liste le porte aussi.
            code, liste = self._get(f"{BASE}/notes")
            self.assertEqual(code, 200)
            mienne = [n for n in liste["notes"] if n["id"] == note_id]
            self.assertEqual(len(mienne), 1)
            self.assertEqual(mienne[0]["html"], attendu)

    def test_la_perte_est_annoncee_PAR_LA_ROUTE(self):
        """🔴 L'essai de la perte ne portait que sur le modèle, et la route de
        modification ne rendait que `note` : l'annonce mourait au contrôleur et
        le widget n'aurait jamais pu la dire."""
        with self._authentifie():
            _code, cree = self._post(f"{BASE}/notes", {
                "client_uuid": str(uuid.uuid4()), "html": "<p>a</p>"})
            self.assertFalse(cree["flattened"])
            note_id = cree["note"]["id"]
            _code, perdu = self._post(f"{BASE}/notes/{note_id}", {
                "html": '<p>voir <a href="https://x.test">ici</a></p>'})
            self.assertTrue(perdu["flattened"])
            self.assertEqual(perdu["note"]["html"], "<p>voir ici</p>")

    def test_sans_l_entete_la_charge_ne_porte_pas_le_html(self):
        with self._authentifie():
            _code, cree = self._post(f"{BASE}/notes", {
                "client_uuid": str(uuid.uuid4()), "text": "x"}, riche=False)
            self.assertNotIn("html", cree["note"])
            self.assertNotIn("rich_editable", cree["note"])
            _code, liste = self._get(f"{BASE}/notes", riche=False)
            self.assertTrue(liste["notes"])
            for note in liste["notes"]:
                self.assertNotIn("html", note)

    def test_le_conflit_se_detecte_aussi_sur_une_ecriture_html(self):
        """⚠️ Par HTTP et pas au modèle : `write_date` vaut l'horodatage de la
        TRANSACTION. Deux écritures dans le même essai le partagent, et le
        conflit ne pourrait pas se produire — l'essai passerait par
        construction au lieu de prouver quoi que ce soit."""
        with self._authentifie():
            _code, cree = self._post(f"{BASE}/notes", {
                "client_uuid": str(uuid.uuid4()), "html": "<p>v1</p>"})
            note_id = cree["note"]["id"]
            self._post(f"{BASE}/notes/{note_id}", {"html": "<p>v2</p>"})
            code, refus = self._post(f"{BASE}/notes/{note_id}", {
                "html": "<p>v3</p>", "write_date": cree["note"]["write_date"]})
            self.assertEqual(code, 409, refus)
            self.assertEqual(refus["note"]["html"], "<p>v2</p>")
