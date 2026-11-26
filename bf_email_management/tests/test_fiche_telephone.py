"""Un courriel ouvert doit rester LISIBLE sous 768 px.

Le défaut que voici existait DÉJÀ dans le code livré, et non dans
un diff — mesuré sur une base d'essai à 390 x 844,
réglages d'usine, un message sélectionné :

    ruban d'actions .......... 14 rangées, 822 px, et 3 de ses 15 commandes
                               seulement atteignables au doigt
    iframe du corps .......... posée à y = 1 580 dans une fenêtre de 844
    corps du message VISIBLE . 0 px
    ancêtres capables de défiler pour le ramener : AUCUN

On voyait l'objet, quatorze rangées de boutons, et pas une ligne du message.

La correction reprend le motif déjà retenu dans Gen — **un panneau à la fois** — et,
comme là-bas, c'est le CSS qui décide à quel pixel : le JS ne porte qu'une classe
d'état et **ne connaît aucune largeur**. Une première version de ce lot avait un
détecteur de largeur en JS ; on a constaté qu'il se trompait sur le panneau
du systray, au zoom du navigateur, et au premier rendu. Il n'y en a plus.

⛔ **Le panneau du systray est HORS de ce lot** (choix retenu) :
il garde le comportement exact du code livré. Son corps de message à 0 px est un
défaut PRÉ-EXISTANT, diagnostiqué et mesuré, qui part au dossier pour un lot qui
pourra être relu. `test_le_getter_du_ruban_est_celui_de_la_production` garde la porte.

Les invariants ci-dessous, chacun contre une panne possible :

1. la borne du CSS doit être CELLE de la coquille mobile — sinon le volet et la barre du haut se
   replient à des pixels différents ;
2. les `display: none` des quatre volets doivent porter `!important` : la liste porte
   `d-flex`, et l'utilitaire de Bootstrap est `display: flex !important`. Mesuré dans
   Gen : sans `!important`, la règle s'applique et les deux panneaux restent là ;
3. la fiche doit battre un `style=` EN LIGNE (`paneStyles()` y écrit
   `flex: 0 0 50%`, `min-width: 320px`, `overflow: hidden`) : sans `!important` elle
   garde la moitié de la hauteur, sort de 190 px hors écran en « aperçu à droite »,
   et son corps reste coupé sans recours ;
4. la portée doit exclure le panneau du systray ET le Navigateur IMAP. Les deux
   versions précédentes de ce lot AFFIRMAIENT la même chose et c'était faux : la
   première atteignait le panneau par la classe racine, la seconde atteignait le
   Navigateur, qui fabrique les mêmes classes de ruban dans `.o_action_manager` ;
5. la classe d'état et le bouton de retour doivent dépendre de la MÊME condition,
   sinon l'écran peut cacher la liste sans offrir le retour — c'est l'invariant n° 1
   de Gen, pour la même raison ;
6. le retour ne doit vivre que sous la borne ET hors du panneau : `d-md-none` est une
   borne de FENÊTRE, et dans le panneau la liste n'a jamais disparu ;
7. toute classe dont le SCSS dépend doit EXISTER dans le gabarit : six mutants
   défaisaient le lot en la renommant d'un côté ou en la retirant de l'autre, et les
   sept essais restaient verts ;
8. la demande d'aperçu doit porter un jeton de séquence, et la fermeture doit
   l'invalider : sans quoi une réponse en retard repeint l'en-tête du courriel fermé,
   sans sélection donc sans bouton de retour.

⚠️ **Ce que ces essais ne peuvent pas voir.** Ils lisent le TEXTE du SCSS, du gabarit
et du JS. Aucun ne voit un écran, aucun ne sait combien de pixels du corps sont
visibles. La preuve se fait au navigateur, par un parcours qui mesure la
hauteur VISIBLE du corps — et non ce qui dépasse la fenêtre, parce qu'un
`overflow: hidden` rend le rognage silencieux et qu'une sonde fondée sur le
débordement rendait « rien n'est coupé » au moment même où tout l'était.
"""

import pathlib
import re

from odoo.tests.common import TransactionCase, tagged

MODULE = pathlib.Path(__file__).resolve().parent.parent
SCSS = (MODULE / "static/src/scss/bf_email_ui.scss").read_text()
XML = (MODULE / "static/src/xml/bf_email_inbox.xml").read_text()
JS = (MODULE / "static/src/js/bf_email_inbox.js").read_text()

# Les quatre volets que la règle doit FERMER, et qui portent tous un utilitaire
# Bootstrap ou un `style=` en ligne : sans `!important`, rien ne se ferme.
VOLETS_A_FERMER = ("o_bf_email_liste", "o_bf_email_splitter",
                   "o_bf_email_folders", "o_bf_email_fiche")


def sans_commentaires(texte):
    """Le SCSS débarrassé de ses commentaires.

    🔴 Deux mutants passaient par là : un `}` laissé en commentaire tronquait la
    lecture du bloc en silence, et un `// .o_action_manager` en commentaire
    faisait croire à une ancre qui n'existait plus.
    """
    texte = re.sub(r"/\*.*?\*/", "", texte, flags=re.S)
    return re.sub(r"//[^\n]*", "", texte)


def bloc_media(texte=None):
    """Le corps de la requête média du lot, accolades équilibrées."""
    texte = sans_commentaires(SCSS if texte is None else texte)
    i = texte.find("@media (max-width:")
    if i < 0:
        return None
    j = texte.find("{", i)
    niveau, k = 0, j
    while k < len(texte):
        if texte[k] == "{":
            niveau += 1
        elif texte[k] == "}":
            niveau -= 1
            if niveau == 0:
                return texte[j + 1:k]
        k += 1
    return None


def _decoupe(texte):
    """(déclarations propres au bloc, [(sélecteur, corps), …]) — un seul niveau."""
    decls, blocs, tampon, i = "", [], "", 0
    while i < len(texte):
        ch = texte[i]
        # ⚠️ `#{…}` est une INTERPOLATION, pas un bloc : la lire comme une
        # accolade ouvrante coupait le sélecteur en deux et l'essai de portée
        # rougissait sur une forme qui compile au même CSS (mutant M22). Elle est
        # recopiée telle quelle dans le sélecteur ; une interpolation qui cacherait
        # l'ancre derrière une VARIABLE ne s'y lira pas, et l'essai la refusera —
        # ce qui est le bon verdict : ce qu'on ne peut pas vérifier ne passe pas.
        if ch == "#" and texte[i + 1:i + 2] == "{":
            j = texte.find("}", i)
            j = len(texte) if j < 0 else j + 1
            tampon += texte[i:j]
            i = j
            continue
        if ch == "{":
            niveau, j = 1, i + 1
            while j < len(texte) and niveau:
                if texte[j] == "{":
                    niveau += 1
                elif texte[j] == "}":
                    niveau -= 1
                j += 1
            blocs.append((tampon.strip(), texte[i + 1:j - 1]))
            tampon, i = "", j
            continue
        if ch == ";":
            decls += tampon + ";"
            tampon = ""
        else:
            tampon += ch
        i += 1
    return decls, blocs


def regles(texte=None, prefixe=()):
    """[(chemin de sélecteurs, déclarations propres)] pour chaque bloc, imbriqués compris.

    ⚠️ Un sélecteur imbriqué hérite de l'ancre de son parent : c'est le CHEMIN qui
    se juge, jamais le sélecteur seul. Une première version de l'essai de portée
    rougissait sur `.o_bf_ribbon_label`, imbriqué — un faux rouge qui ne mesurait rien.
    """
    if texte is None:
        texte = bloc_media()
    out = []
    _, blocs = _decoupe(texte or "")
    for sel, corps in blocs:
        chemin = prefixe + (sel,)
        decls, _sous = _decoupe(corps)
        out.append((chemin, decls))
        out.extend(regles(corps, chemin))
    return out


def gabarit():
    """Le texte des DEUX fichiers qui peuvent poser une classe, sans commentaires.

    ⚠️ Le ruban ne vient pas du gabarit : `ribbonClass` le fabrique en JS. Et les
    commentaires sont retirés des deux, sans quoi un mutant qui retire la classe
    du gabarit la laisserait « présente » dans la prose d'à côté.
    """
    x = re.sub(r"<!--.*?-->", "", XML, flags=re.S)
    j = re.sub(r"/\*.*?\*/", "", JS, flags=re.S)
    j = "\n".join(l for l in j.splitlines() if not l.lstrip().startswith("//"))
    return x + "\n" + j


def classes(selecteur):
    """Les classes d'un sélecteur, en frontière de classe."""
    return set(re.findall(r"\.([A-Za-z0-9_-]+)", selecteur))


def porte_la_classe(selecteur, nom):
    """`.o_action_manager_autre` ne contient PAS `.o_action_manager`.

    🔴 Un mutant passait exactement par là : la sous-chaîne était là, la classe non.
    """
    return bool(re.search(r"\.%s(?![\w-])" % re.escape(nom), selecteur))


def alternatives(selecteur):
    """Les branches d'une liste à virgules — chacune doit tenir SEULE.

    🔴 Un mutant ajoutait une seconde branche non ancrée à côté de la bonne : le
    sélecteur « contenait » l'ancre, et la règle atteignait quand même le systray.
    """
    return [s.strip() for s in selecteur.split(",") if s.strip()]


def corps_du_getter(nom):
    """Le corps du getter `nom` dans le JS, accolades équilibrées."""
    i = JS.find("get %s()" % nom)
    if i < 0:
        return None
    j = JS.find("{", i)
    niveau, k = 0, j
    while k < len(JS):
        if JS[k] == "{":
            niveau += 1
        elif JS[k] == "}":
            niveau -= 1
            if niveau == 0:
                return JS[j + 1:k]
        k += 1
    return None


def corps_de_methode(nom):
    """Le corps de la DÉFINITION de la méthode `nom` dans le JS.

    ⚠️ Ancré en début de ligne : une première version de cet essai prenait le
    premier `_fetchPreview(` du fichier, qui est un APPEL, et lisait le corps
    d'une tout autre méthode. Il rougissait sur du bon code.
    """
    m = re.search(r"^\s*(?:async\s+)?%s\s*\(" % re.escape(nom), JS, re.M)
    if not m:
        return None
    j = JS.find("{", m.end())
    niveau, k = 0, j
    while k < len(JS):
        if JS[k] == "{":
            niveau += 1
        elif JS[k] == "}":
            niveau -= 1
            if niveau == 0:
                return JS[j + 1:k]
        k += 1
    return None


@tagged("post_install", "-at_install")
class TestFicheTelephone(TransactionCase):

    # ------------------------------------------------------------------
    # 1. La borne, et l'accord avec la coquille mobile
    # ------------------------------------------------------------------
    def test_la_borne_est_celle_du_lot_1(self):
        bornes = set(re.findall(r"@media \(max-width:\s*([\d.]+)px\)",
                                sans_commentaires(SCSS)))
        self.assertTrue(bornes, "le module n'a plus de requête média")
        self.assertEqual(
            bornes, {"767.98"},
            f"borne(s) inattendue(s) : {sorted(bornes)} — le choix retenu "
            f"est 767,98, parce que 768 px pile prend un pixel de trop")

    def test_la_borne_du_lot_1_n_a_pas_bouge(self):
        """L'invariant tient un accord entre DEUX modules : il se lit chez l'autre.

        🔴 Cet essai `skipTest`ait quand le fichier manquait — et Odoo ne montre pas
        les skips dans son « of N tests » : un mutant qui EFFACE le socle de la coquille mobile
        passait donc pour vert. Il échoue, maintenant.
        """
        from odoo.modules.module import get_module_path
        chemin = get_module_path("bluefox_branding", display_warning=False)
        self.assertTrue(
            chemin, "bluefox_branding est absent du chemin des modules : l'accord de "
                    "borne entre la coquille mobile et ce correctif n'est plus vérifiable")
        f = pathlib.Path(chemin) / "static/src/scss/mobile_shell.scss"
        self.assertTrue(
            f.exists(), f"{f} a disparu : la coquille mobile portait la borne "
                        f"que ce lot reprend, et l'accord n'est plus vérifiable")
        # ⚠️ La REQUÊTE MÉDIA, pas la sous-chaîne « 767.98 » : un mutant portait la
        # borne à 900 px et laissait « 767.98 » survivre dans un commentaire.
        self.assertIn(
            "@media (max-width: 767.98px)", sans_commentaires(f.read_text()),
            "la borne de la coquille mobile a changé : le volet des courriels et la barre du haut "
            "se replieraient à des pixels différents")

    # ------------------------------------------------------------------
    # 2 et 3. Ce qui bat Bootstrap et le `style=` en ligne
    # ------------------------------------------------------------------
    def test_les_fermetures_portent_important(self):
        """TOUTES les fermetures des volets, pas la première.

        🔴 L'essai d'avant se contentait d'un `or "display: none !important" in bloc` :
        un mutant retirait le `!important` de la VRAIE fermeture et en posait un
        leurre sur `.o_bf_ribbon_label`, et l'essai restait vert.
        """
        fermes = set()
        for chemin, decls in regles():
            vise = set()
            for s in chemin:
                vise |= classes(s)
            for d in re.findall(r"display\s*:\s*none[^;]*;", decls):
                touche = vise & set(VOLETS_A_FERMER)
                if touche:
                    fermes |= touche
                    self.assertIn(
                        "!important", d,
                        f"fermeture sans `!important` sur {sorted(touche)} "
                        f"({' '.join(chemin)}) : la liste porte `d-flex`, donc "
                        f"`display: flex !important`, et la fiche porte sa taille "
                        f"dans un `style=` en ligne — rien ne se fermerait")
        self.assertEqual(
            fermes, set(VOLETS_A_FERMER),
            f"volets jamais fermés : {sorted(set(VOLETS_A_FERMER) - fermes)} — "
            f"mesuré sur une base d'essai, chacun vole sa part de l'écran à un courriel ouvert")

    def test_la_fiche_bat_le_style_en_ligne(self):
        """`paneStyles()` écrit la taille de la fiche EN LIGNE : il faut `!important`."""
        bloc = bloc_media()
        self.assertRegex(
            bloc, r"flex:\s*1 1 100%\s*!important",
            "la fiche ne force pas sa taille : `paneStyles()` lui impose "
            "`flex: 0 0 50%` dans un `style=` en ligne, qu'une classe ne bat pas")
        self.assertRegex(
            bloc, r"min-width:\s*0\s*!important",
            "la fiche ne défait pas le `min-width: 320px` du style en ligne : mesuré "
            "sur une base d'essai, en « aperçu à droite » à 390 px elle sortait de 190 px hors "
            "écran, rognés sans recours")
        # ⚠️ `overflow: hidden auto`, et non `overflow-y: auto` : un axe `visible`
        # face à un axe `auto` devient `auto`. Si le style en ligne cessait un jour
        # d'écrire `overflow: hidden`, `overflow-y: auto` seul laisserait apparaître
        # une barre HORIZONTALE. La règle doit tenir seule.
        self.assertRegex(
            bloc, r"overflow:\s*hidden auto\s*!important",
            "la fiche ne force pas ses DEUX axes de débordement : `paneStyles()` lui "
            "impose `overflow: hidden` en ligne, donc un en-tête plus haut qu'elle "
            "rend le corps du message INATTEIGNABLE — le défaut même que ce lot "
            "corrige — et un seul axe forcé laisse l'autre dériver")
        # Et le style en ligne est bien toujours là, sinon ces !important mentent.
        commun = (MODULE / "static/src/js/bf_email_ui_common.js").read_text()
        for forme in ("flex: 0 0 ${size}%", "min-width: 320px", "overflow: hidden"):
            self.assertIn(
                forme, commun,
                f"`paneStyles()` n'écrit plus `{forme}` en ligne : le `!important` "
                f"qui le bat n'a plus de raison d'être et doit être relu")

    # ------------------------------------------------------------------
    # 4. La portée : ni le panneau du systray, ni le Navigateur IMAP
    # ------------------------------------------------------------------
    def test_la_portee_exclut_le_panneau_du_systray(self):
        """Le panneau vit dans une superposition, hors de `.o_action_manager`.

        🔴 Deux fois de suite, ce lot a AFFIRMÉ ne pas toucher au panneau alors que
        sa règle l'atteignait. L'ancre `.o_action_manager` est ce qui le tient, et
        elle se juge en frontière de classe, branche par branche.
        """
        premiers = [chemin[0] for chemin, _ in regles() if len(chemin) == 1]
        self.assertTrue(premiers, "aucun sélecteur de premier niveau")
        for s in premiers:
            for branche in alternatives(s):
                with self.subTest(branche=branche):
                    self.assertTrue(
                        porte_la_classe(branche, "o_action_manager"),
                        "branche non ancrée sur l'action pleine page : elle "
                        "atteindrait le panneau du systray, que ce lot déclare "
                        "intouché (⚠️ `.o_action_manager_autre` n'est PAS l'ancre, "
                        "et une ancre cachée dans une interpolation ne se lit pas)")

    def test_la_portee_exclut_le_navigateur_imap(self):
        """`bf_email_browser.js` fabrique les MÊMES classes de ruban, dans `.o_action_manager`.

        🔴 Sous la borne, le Navigateur perdait ses
        libellés de force sans rien recevoir — dont un badge « Déjà dans bf.email »
        dont le libellé EST tout le contenu. Seul `.o_bf_email_duo`, que le gabarit
        du Navigateur ne porte pas, sépare les deux écrans.
        """
        for chemin, decls in regles():
            vise = set()
            for s in chemin:
                vise |= classes(s)
            if "o_bf_email_ribbon" not in vise and "o_bf_ribbon_label" not in vise:
                continue
            complet = " ".join(chemin)
            with self.subTest(regle=complet):
                self.assertIn(
                    "o_bf_email_duo", vise,
                    "une règle de ruban qui ne passe pas par `.o_bf_email_duo` : le "
                    "Navigateur IMAP la recevrait, et il n'a ni liste à cacher ni "
                    "fiche à agrandir")
                self.assertIn(
                    "o_bf_email_fiche_ouverte", vise,
                    "une règle de ruban qui ne dépend pas de la classe d'état : elle "
                    "replierait le ruban même sans courriel ouvert")

    # ------------------------------------------------------------------
    # 5, 6. L'état, le retour, le témoin de sélection
    # ------------------------------------------------------------------
    def test_la_classe_d_etat_et_le_retour_ont_la_meme_condition(self):
        """Sinon l'écran peut cacher la liste sans offrir le retour."""
        pose = re.findall(r"state\.selectedId \? '\s?o_bf_email_fiche_ouverte'", XML)
        self.assertEqual(
            len(pose), 2,
            "la classe d'état doit être posée sur DEUX nœuds par la MÊME condition : "
            "le couple liste/fiche (pour la liste, le séparateur et la fiche) et la "
            "racine (pour le volet des dossiers, qui est leur FRÈRE et qu'aucun "
            f"sélecteur descendant n'atteint). Trouvé : {len(pose)}")
        self.assertIn(
            '<div t-if="state.selectedId and !props.inPanel"', XML,
            "le bouton de retour ne dépend plus de la même condition que la classe "
            "d'état : la liste pourrait se cacher sans qu'aucun retour soit rendu")

    def test_le_retour_ne_vit_que_sous_la_borne_et_hors_du_panneau(self):
        """Deux bornes, pas une : la fenêtre ET la surface.

        🔴 `d-md-none` est une borne de FENÊTRE. Dans le
        panneau du systray, la liste n'a jamais disparu, et le bouton y prenait 46 px
        (14 %) d'une fiche affamée pour ne rien ramener. L'essai d'avant CIMENTAIT
        l'erreur en n'exigeant que `d-md-none`.
        """
        j = XML.find("fermerFiche")
        self.assertGreater(j, 0, "le bouton de retour a disparu du gabarit")
        autour = XML[max(0, j - 700):j]
        self.assertIn(
            "d-md-none", autour,
            "le bouton de retour n'est pas borné à l'écran étroit : au-dessus de "
            "768 px les deux panneaux cohabitent et il n'a rien à faire là. "
            "`d-md-none` est la convention d'Odoo lui-même")
        self.assertIn(
            "!props.inPanel", autour,
            "le bouton de retour n'est pas borné à la PLEINE PAGE : dans le panneau "
            "du systray la liste n'a jamais disparu, et il y volerait 46 px pour ne "
            "rien ramener")

    def test_le_temoin_de_selection_survit_a_la_liste_cachee(self):
        """Une action de masse ne doit pas viser des lignes qu'on ne voit plus.

        Le choix est le moins risqué des deux : on REND le compte dans la fiche, au
        lieu de vider la sélection — vider écrirait un état que personne n'a demandé,
        et le bandeau de la liste, avec son « Effacer la sélection », revient avec
        elle. Le témoin vit DANS la barre de retour, qui est déjà `d-md-none` : rien
        ne change au-dessus de la borne.
        """
        j = XML.find("fermerFiche")
        self.assertGreater(j, 0)
        # la barre de retour : du `t-if` qui la porte à la fin de son `</div>`
        i = XML.rfind('<div t-if="state.selectedId and !props.inPanel"', 0, j)
        self.assertGreater(i, 0, "la barre de retour a changé de condition")
        fin = XML.find("</div>", XML.find("</button>", j))
        barre = XML[i:fin]
        # ⚠️ Le RENDU, pas la mention : un mutant mettait la condition du témoin à
        # `false` et le mot `selectedCount` survivait dans son infobulle.
        self.assertIn(
            't-if="selectedCount"', barre,
            "le témoin de sélection n'est plus conditionné au compte : il se "
            "peindrait à vide, ou pas du tout")
        self.assertIn(
            't-esc="selectedCount"', barre,
            "la barre de retour ne REND pas le compte de la sélection : sous la "
            "borne, la liste cachée emporte son bandeau « N sélectionné(s) » et une "
            "action de masse viserait des lignes cochées devenues invisibles")
        self.assertIn(
            "d-md-none", barre,
            "le témoin de sélection n'est pas borné à l'écran étroit")

    # ------------------------------------------------------------------
    # 7. Le lien entre le SCSS et le gabarit — l'essai le plus rentable
    # ------------------------------------------------------------------
    def test_les_classes_dont_le_scss_depend_existent_dans_le_gabarit(self):
        """Toute la règle repose sur six classes : si l'une manque d'un côté, rien.

        🔴 Six mutants sur onze passaient par là, les sept essais au vert : renommer
        la classe dans le SCSS, ou la retirer du gabarit, défaisait le lot en
        silence. Aucun essai ne reliait les deux fichiers.
        """
        attendues = set()
        for chemin, _ in regles():
            for s in chemin:
                attendues |= {c for c in classes(s)
                              if c.startswith(("o_bf_email", "o_bf_ribbon"))}
        self.assertGreaterEqual(
            len(attendues), 6,
            f"le SCSS ne s'appuie plus que sur {sorted(attendues)} : la règle a "
            f"perdu un de ses points d'accroche")
        for nom in ("o_bf_email_duo", "o_bf_email_liste", "o_bf_email_fiche",
                    "o_bf_email_fiche_ouverte", "o_bf_email_ribbon",
                    "o_bf_email_folders", "o_bf_email_splitter"):
            self.assertIn(
                nom, attendues,
                f"le SCSS ne s'appuie plus sur `{nom}` : la règle que cette classe "
                f"portait ne s'applique plus à rien")
        pose = gabarit()
        for nom in sorted(attendues):
            with self.subTest(classe=nom):
                self.assertRegex(
                    pose, r"[\s\"']%s(?=[\s\"'])" % re.escape(nom),
                    f"`{nom}` est dans le SCSS et PAS dans le gabarit : la règle qui "
                    f"en dépend ne s'applique à aucun nœud, et l'écran reste celui "
                    f"d'avant le lot")

    def test_le_volet_des_dossiers_se_cache_sous_la_borne(self):
        """Choix retenu : sa place va au courriel, et elle lui revient.

        ⚠️ Le volet est le FRÈRE du couple liste/fiche, pas son descendant : la règle
        passe par la classe d'état de la RACINE. Et elle ne touche ni au rail ni au
        repli d'office (remis) : le retour à la liste ramène les dossiers.
        """
        trouve = [(chemin, d) for chemin, d in regles()
                  if "o_bf_email_folders" in set().union(*[classes(s) for s in chemin])]
        self.assertTrue(
            trouve, "le volet des dossiers ne se cache plus : mesuré sur une base d'essai, il "
                    "gardait ses 260 px des 390 de l'écran et la fiche n'en avait "
                    "que 130, où le courriel était rogné horizontalement en silence")
        for chemin, decls in trouve:
            with self.subTest(regle=" ".join(chemin)):
                vise = set().union(*[classes(s) for s in chemin])
                self.assertIn(
                    "o_bf_email_fiche_ouverte", vise,
                    "le volet se cacherait même sans courriel ouvert : il n'y aurait "
                    "plus aucun moyen de changer de dossier sur un téléphone")
                self.assertRegex(
                    decls, r"display\s*:\s*none\s*!important",
                    "le volet ne se ferme pas vraiment")
        # ⛔ Et rien ne touche au rail ni au repli d'office : ils restent tels quels.
        self.assertNotIn(
            "o_bf_email_rail", bloc_media(),
            "la requête média touche au rail des dossiers : il est REMIS "
            "(arbitrage), avec sa condition — rendre au rail les 58 dossiers sur 71 "
            "qu'il laisse tomber")
        self.assertIn(
            "get foldersCollapsed()", JS,
            "le repli d'office a disparu : ce lot n'y touche pas")

    def test_la_regle_miroir_rend_la_liste_quand_rien_n_est_choisi(self):
        """Sans sélection, la fiche vide ne doit pas manger la liste.

        🔴 À 390 px la liste n'avait que
        394 px de haut et la fiche VIDE en prenait 399, pour y montrer un
        pense-bête de treize touches de raccourci — sur un appareil sans clavier.
        """
        miroir = [(chemin, d) for chemin, d in regles()
                  if any(":not(.o_bf_email_fiche_ouverte)" in s for s in chemin)]
        self.assertTrue(
            miroir, "la règle miroir n'existe pas : rien de sélectionné, la fiche "
                    "vide garde sa moitié d'écran et la liste n'a que la sienne")
        for chemin, decls in miroir:
            vise = set().union(*[classes(s) for s in chemin])
            if "o_bf_email_fiche" in vise or "o_bf_email_splitter" in vise:
                self.assertRegex(
                    decls, r"display\s*:\s*none\s*!important",
                    f"la règle miroir ne ferme pas vraiment ({' '.join(chemin)})")

    # ------------------------------------------------------------------
    # 8. Le JS : le getter de production, et le jeton de séquence
    # ------------------------------------------------------------------
    def test_le_getter_du_ruban_est_celui_de_la_production(self):
        """⛔ Le panneau du systray SORT du lot (choix retenu).

        🔴 La forme qu'épinglait l'essai d'avant était un BLOQUANT : `toggleRibbon()`
        écrit `!this.ribbonCollapsed`, la négation du getter FORCÉ et non de la
        préférence. Dans un panneau étroit, un clic sur « Déplier le ruban » ne
        dépliait rien et faisait tomber la préférence de `true` à `false`, sans
        retour — et la clé est PARTAGÉE avec la pleine page et le Navigateur.
        """
        corps = corps_du_getter("ribbonCollapsed")
        self.assertIsNotNone(corps, "le getter `ribbonCollapsed` a disparu")
        self.assertEqual(
            corps.strip(), "return !!this.state.settings.ribbonCollapsed;",
            "le getter du ruban n'est plus celui de la production : ce lot ne touche "
            "PAS au panneau du systray, et toute garde posée ici y entre par "
            "`toggleRibbon()`, qui nie le getter et non la préférence")
        for interdit in ("inPanel", "autoCollapsed", "matchMedia"):
            self.assertNotIn(
                interdit, corps,
                f"`{interdit}` est revenu dans le getter du ruban")
        # ⚠️ Mais le signal du rail doit rester : il sert au RAIL des dossiers.
        self.assertIn(
            "this.state.autoCollapsed = width < PANEL_RAIL_BELOW_PX", JS,
            "le `ResizeObserver` du rail a disparu : ce lot ne devait rien lui "
            "prendre — c'est lui qui replie le volet dans le panneau du systray")
        # Et aucun détecteur de largeur en JS, nulle part : reproche n° 1 de la première version.
        self.assertNotIn(
            "matchMedia", JS,
            "un détecteur de largeur est revenu en JS : on a constaté qu'il "
            "se trompait au zoom, au premier rendu, et sur le panneau")

    def test_l_apercu_porte_un_jeton_de_sequence(self):
        """🔴 La course.

        J'ouvre, je ferme, la réponse arrive : l'en-tête du courriel fermé repeignait
        sans sélection, donc SANS bouton de retour, dans une fiche où le corps est à
        0 px. Piégé jusqu'à changer de dossier — deux taps, sur le geste même que ce
        lot installe. `loadMessages` portait déjà ce jeton (`_loadSeq`).
        """
        corps = corps_de_methode("_fetchPreview")
        self.assertIsNotNone(corps, "`_fetchPreview` a disparu")
        self.assertRegex(
            corps, r"this\._previewSeq\s*=\s*\(this\._previewSeq\s*\|\|\s*0\)\s*\+\s*1",
            "`_fetchPreview` ne prend pas de jeton de séquence : une réponse en "
            "retard écrit par-dessus l'état d'un autre message, ou d'aucun")
        gardes = re.findall(r"jeton\s*!==\s*this\._previewSeq", corps)
        self.assertGreaterEqual(
            len(gardes), 3,
            f"seulement {len(gardes)} garde(s) de jeton dans `_fetchPreview` : il "
            f"faut en couvrir CHAQUE écriture — le brouillon, l'aperçu, l'échec — et "
            f"le `finally` qui éteint le témoin de chargement")
        self.assertRegex(
            corps, r"finally\s*\{\s*if\s*\(jeton === this\._previewSeq\)",
            "le `finally` éteint `loadingPreview` sans regarder le jeton : une "
            "réponse en retard éteindrait le témoin d'une demande encore en vol")

    def test_fermer_la_fiche_invalide_ce_qui_est_en_vol(self):
        corps = corps_de_methode("fermerFiche")
        self.assertIsNotNone(corps, "`fermerFiche` a disparu")
        for forme, pourquoi in (
            (r"this\._previewSeq\s*=\s*\(this\._previewSeq\s*\|\|\s*0\)\s*\+\s*1",
             "la demande d'aperçu en vol n'est pas invalidée : sa réponse repeint "
             "l'en-tête du courriel fermé"),
            (r"this\.state\.selectedId = null",
             "la sélection n'est pas défaite : la classe d'état reste posée et la "
             "liste cachée"),
            (r"this\.state\.preview = null", "l'aperçu reste affiché"),
            (r"this\.state\.loadingPreview = false",
             "le témoin de chargement reste allumé : un tourniquet seul dans une "
             "fiche vide, et la liste cachée derrière"),
            (r"this\.state\.genText = null", "le résumé de Gen survit au courriel"),
            (r"this\.state\.genKind = null", "le genre de résumé survit au courriel"),
        ):
            with self.subTest(forme=forme):
                self.assertRegex(corps, forme, pourquoi)
