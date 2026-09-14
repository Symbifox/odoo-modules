"""18.0.2.2.0 : les libellés passent d'une source française à une source anglaise.

Odoo ne traduit jamais vers en_US, la langue source : tant que la source était
française, un usager réglé en anglais lisait ce module en français.

🔴 Une mise à jour n'écrase jamais une traduction existante : le catalogue du
module est rechargé en écrasant (le catalogue d'avant ne traduisait rien, ou
recopiait le français).

Les données `noupdate` gardent leur valeur en_US française après la mise à jour.
On bascule chaque champ SEULEMENT s'il porte encore le texte livré; l'anglais est
lu dans le fichier de données du module. Une valeur retouchée est laissée.

⚠️ En `end` : Odoo charge les traductions du module juste après le `post`.
"""

import logging

from lxml import etree

from odoo import SUPERUSER_ID, api
from odoo.tools import file_open

_logger = logging.getLogger(__name__)

MODULE = "daily_todo_digest"
# (fichier, identifiant, champ) -> français livré avant 18.0.2.2.0
LIVRES_FR = {('data/daily_digest_cron.xml', 'ir_cron_daily_todo_digest', 'name'): 'Daily To-Do Digest: Envoi '
                                                                      'quotidien',
 ('data/inspirational_quotes.xml', 'quote_01', 'author'): 'Pierre-Joseph Proudhon',
 ('data/inspirational_quotes.xml', 'quote_01', 'quote'): "La propriété, c'est le vol.",
 ('data/inspirational_quotes.xml', 'quote_02', 'author'): 'Pierre-Joseph Proudhon',
 ('data/inspirational_quotes.xml', 'quote_02', 'quote'): "L'anarchie, c'est l'ordre sans le "
                                                         'pouvoir.',
 ('data/inspirational_quotes.xml', 'quote_03', 'author'): 'Pierre Kropotkine',
 ('data/inspirational_quotes.xml', 'quote_03', 'quote'): "L'entraide est le facteur le plus "
                                                         "important de l'évolution.",
 ('data/inspirational_quotes.xml', 'quote_04', 'author'): 'Pierre Kropotkine',
 ('data/inspirational_quotes.xml', 'quote_04', 'quote'): 'Le bonheur des autres est indispensable '
                                                         'à notre propre bonheur.',
 ('data/inspirational_quotes.xml', 'quote_05', 'author'): 'Emma Goldman',
 ('data/inspirational_quotes.xml', 'quote_05', 'quote'): "Si je ne peux pas danser, ce n'est pas "
                                                         'ma révolution.',
 ('data/inspirational_quotes.xml', 'quote_06', 'author'): 'Emma Goldman',
 ('data/inspirational_quotes.xml', 'quote_06', 'quote'): 'La vraie émancipation commence dans '
                                                         "l'âme de la femme.",
 ('data/inspirational_quotes.xml', 'quote_07', 'author'): 'Michael Bakunin',
 ('data/inspirational_quotes.xml', 'quote_07', 'quote'): "Personne n'est libre tant que tout le "
                                                         "monde ne l'est pas.",
 ('data/inspirational_quotes.xml', 'quote_08', 'author'): 'Arthur Rimbaud',
 ('data/inspirational_quotes.xml', 'quote_08', 'quote'): "On n'est pas sérieux quand on a dix-sept "
                                                         'ans.',
 ('data/inspirational_quotes.xml', 'quote_09', 'author'): 'Arthur Rimbaud',
 ('data/inspirational_quotes.xml', 'quote_09', 'quote'): 'Il faut être absolument moderne.',
 ('data/inspirational_quotes.xml', 'quote_10', 'author'): 'Victor Hugo',
 ('data/inspirational_quotes.xml', 'quote_10', 'quote'): 'Ceux qui vivent, ce sont ceux qui '
                                                         'luttent.',
 ('data/inspirational_quotes.xml', 'quote_100', 'author'): 'Sénèque',
 ('data/inspirational_quotes.xml', 'quote_100', 'quote'): "Il n'est pas de vent favorable pour "
                                                          'celui qui ne sait où il va.',
 ('data/inspirational_quotes.xml', 'quote_101', 'author'): 'Proverbe africain',
 ('data/inspirational_quotes.xml', 'quote_101', 'quote'): 'Si tu veux aller vite, marche seul. Si '
                                                          'tu veux aller loin, marchons ensemble.',
 ('data/inspirational_quotes.xml', 'quote_102', 'author'): 'Proverbe persan',
 ('data/inspirational_quotes.xml', 'quote_102', 'quote'): "L'océan est fait de gouttes d'eau.",
 ('data/inspirational_quotes.xml', 'quote_103', 'author'): 'Proverbe chinois',
 ('data/inspirational_quotes.xml', 'quote_103', 'quote'): 'Celui qui pose une question est bête '
                                                          "cinq minutes, celui qui n'en pose pas "
                                                          'le reste toute sa vie.',
 ('data/inspirational_quotes.xml', 'quote_104', 'author'): 'Proverbe adapté',
 ('data/inspirational_quotes.xml', 'quote_104', 'quote'): "La parole est d'argent, mais le silence "
                                                          "est d'or... sauf face à l'injustice.",
 ('data/inspirational_quotes.xml', 'quote_105', 'author'): 'Proverbe moderne',
 ('data/inspirational_quotes.xml', 'quote_105', 'quote'): 'Les murs ont été construits par ceux '
                                                          'qui avaient peur de ceux qui sont '
                                                          'différents.',
 ('data/inspirational_quotes.xml', 'quote_106', 'author'): 'Proverbe japonais',
 ('data/inspirational_quotes.xml', 'quote_106', 'quote'): "Aucun de nous n'est aussi intelligent "
                                                          'que nous tous ensemble.',
 ('data/inspirational_quotes.xml', 'quote_107', 'author'): 'Greta Thunberg',
 ('data/inspirational_quotes.xml', 'quote_107', 'quote'): 'Notre maison est en feu. Je veux que '
                                                          'vous paniquiez.',
 ('data/inspirational_quotes.xml', 'quote_108', 'author'): 'Greta Thunberg',
 ('data/inspirational_quotes.xml', 'quote_108', 'quote'): "Vous n'êtes jamais trop petit pour "
                                                          'faire la différence.',
 ('data/inspirational_quotes.xml', 'quote_109', 'author'): 'Paulo Freire',
 ('data/inspirational_quotes.xml', 'quote_109', 'quote'): "Quand l'éducation n'est pas "
                                                          "libératrice, le rêve de l'opprimé est "
                                                          "de devenir l'oppresseur.",
 ('data/inspirational_quotes.xml', 'quote_11', 'author'): 'Victor Hugo',
 ('data/inspirational_quotes.xml', 'quote_11', 'quote'): "La mélancolie, c'est le bonheur d'être "
                                                         'triste.',
 ('data/inspirational_quotes.xml', 'quote_110', 'author'): 'Paulo Freire',
 ('data/inspirational_quotes.xml', 'quote_110', 'quote'): "Personne n'éduque autrui, personne ne "
                                                          "s'éduque seul, les hommes s'éduquent "
                                                          "ensemble par l'intermédiaire du monde.",
 ('data/inspirational_quotes.xml', 'quote_111', 'author'): 'Christian Lous Lange',
 ('data/inspirational_quotes.xml', 'quote_111', 'quote'): 'La technologie est un serviteur utile '
                                                          'mais un maître dangereux.',
 ('data/inspirational_quotes.xml', 'quote_112', 'author'): 'Aaron Swartz',
 ('data/inspirational_quotes.xml', 'quote_112', 'quote'): 'Internet doit rester un espace de '
                                                          'liberté et de partage du savoir.',
 ('data/inspirational_quotes.xml', 'quote_113', 'author'): 'Stewart Brand',
 ('data/inspirational_quotes.xml', 'quote_113', 'quote'): "L'information veut être libre.",
 ('data/inspirational_quotes.xml', 'quote_114', 'author'): 'Martin Luther King Jr.',
 ('data/inspirational_quotes.xml', 'quote_114', 'quote'): "La vraie mesure d'un homme n'est pas "
                                                          'comment il se comporte dans les moments '
                                                          'de confort, mais comment il se tient '
                                                          'dans les moments de défi et de '
                                                          'controverse.',
 ('data/inspirational_quotes.xml', 'quote_115', 'author'): 'Frantz Fanon',
 ('data/inspirational_quotes.xml', 'quote_115', 'quote'): 'Chaque génération doit, dans une '
                                                          'relative opacité, découvrir sa mission, '
                                                          'la remplir ou la trahir.',
 ('data/inspirational_quotes.xml', 'quote_116', 'author'): 'Aimé Césaire',
 ('data/inspirational_quotes.xml', 'quote_116', 'quote'): "Je parle de millions d'hommes à qui on "
                                                          'a inculqué savamment la peur, le '
                                                          "complexe d'infériorité, le tremblement, "
                                                          "l'agenouillement, le désespoir, le "
                                                          'larbinisme.',
 ('data/inspirational_quotes.xml', 'quote_117', 'author'): 'Aimé Césaire',
 ('data/inspirational_quotes.xml', 'quote_117', 'quote'): 'Ma bouche sera la bouche des malheurs '
                                                          "qui n'ont point de bouche.",
 ('data/inspirational_quotes.xml', 'quote_118', 'author'): 'Albert Einstein',
 ('data/inspirational_quotes.xml', 'quote_118', 'quote'): 'Le monde ne sera pas détruit par ceux '
                                                          'qui font le mal, mais par ceux qui les '
                                                          'regardent sans rien faire.',
 ('data/inspirational_quotes.xml', 'quote_119', 'author'): 'Vincent van Gogh',
 ('data/inspirational_quotes.xml', 'quote_119', 'quote'): 'La normalité est une route pavée : on y '
                                                          "marche aisément mais les fleurs n'y "
                                                          'poussent pas.',
 ('data/inspirational_quotes.xml', 'quote_12', 'author'): 'Pablo Neruda',
 ('data/inspirational_quotes.xml', 'quote_12', 'quote'): 'Je voudrais faire avec toi ce que le '
                                                         'printemps fait avec les cerisiers.',
 ('data/inspirational_quotes.xml', 'quote_120', 'author'): 'Proverbe moderne',
 ('data/inspirational_quotes.xml', 'quote_120', 'quote'): "Dans un monde où tu peux être n'importe "
                                                          'quoi, sois gentil.',
 ('data/inspirational_quotes.xml', 'quote_13', 'author'): 'Pablo Neruda',
 ('data/inspirational_quotes.xml', 'quote_13', 'quote'): 'Vous pouvez couper toutes les fleurs, '
                                                         'mais vous ne pouvez pas empêcher le '
                                                         'printemps de revenir.',
 ('data/inspirational_quotes.xml', 'quote_14', 'author'): 'Pablo Neruda',
 ('data/inspirational_quotes.xml', 'quote_14', 'quote'): 'La poésie est un acte de paix.',
 ('data/inspirational_quotes.xml', 'quote_15', 'author'): 'Federico García Lorca',
 ('data/inspirational_quotes.xml', 'quote_15', 'quote'): 'Je vais rêver, mais non dormir.',
 ('data/inspirational_quotes.xml', 'quote_16', 'author'): 'Federico García Lorca',
 ('data/inspirational_quotes.xml', 'quote_16', 'quote'): 'Comme je ne me suis pas soucié de '
                                                         'naître, je ne me soucie pas de mourir.',
 ('data/inspirational_quotes.xml', 'quote_17', 'author'): 'Albert Camus',
 ('data/inspirational_quotes.xml', 'quote_17', 'quote'): "En plein milieu de l'hiver, j'apprenais "
                                                         "enfin qu'il y avait en moi un été "
                                                         'invincible.',
 ('data/inspirational_quotes.xml', 'quote_18', 'author'): 'Albert Camus',
 ('data/inspirational_quotes.xml', 'quote_18', 'quote'): 'Il faut imaginer Sisyphe heureux.',
 ('data/inspirational_quotes.xml', 'quote_19', 'author'): 'Albert Camus',
 ('data/inspirational_quotes.xml', 'quote_19', 'quote'): "La vraie générosité envers l'avenir "
                                                         'consiste à tout donner au présent.',
 ('data/inspirational_quotes.xml', 'quote_20', 'author'): 'Albert Camus',
 ('data/inspirational_quotes.xml', 'quote_20', 'quote'): 'Je me révolte, donc nous sommes.',
 ('data/inspirational_quotes.xml', 'quote_21', 'author'): 'Simone de Beauvoir',
 ('data/inspirational_quotes.xml', 'quote_21', 'quote'): 'On ne naît pas femme, on le devient.',
 ('data/inspirational_quotes.xml', 'quote_22', 'author'): 'Simone de Beauvoir',
 ('data/inspirational_quotes.xml', 'quote_22', 'quote'): "Le présent n'est pas un passé en "
                                                         'puissance, il est le moment du choix et '
                                                         "de l'action.",
 ('data/inspirational_quotes.xml', 'quote_23', 'author'): 'Guy Debord',
 ('data/inspirational_quotes.xml', 'quote_23', 'quote'): 'La révolution ne consiste pas à montrer '
                                                         'la vie aux gens, mais à les faire vivre.',
 ('data/inspirational_quotes.xml', 'quote_24', 'author'): 'Eduardo Galeano',
 ('data/inspirational_quotes.xml', 'quote_24', 'quote'): "L'utopie est à l'horizon. Je fais deux "
                                                         "pas, elle s'éloigne de deux pas. Je fais "
                                                         "dix pas et l'horizon recule de dix pas. "
                                                         "Alors à quoi sert l'utopie? Elle sert à "
                                                         'ça : à marcher.',
 ('data/inspirational_quotes.xml', 'quote_25', 'author'): 'Eduardo Galeano',
 ('data/inspirational_quotes.xml', 'quote_25', 'quote'): "L'histoire est un prophète qui regarde "
                                                         'en arrière.',
 ('data/inspirational_quotes.xml', 'quote_26', 'author'): 'Rabbi Nachman de Bratslav',
 ('data/inspirational_quotes.xml', 'quote_26', 'quote'): "Ne demande jamais ton chemin à quelqu'un "
                                                         'qui le connaît, car tu ne pourras pas '
                                                         "t'égarer.",
 ('data/inspirational_quotes.xml', 'quote_27', 'author'): 'Frida Kahlo',
 ('data/inspirational_quotes.xml', 'quote_27', 'quote'): "Je peins des fleurs pour qu'elles ne "
                                                         'meurent pas.',
 ('data/inspirational_quotes.xml', 'quote_28', 'author'): 'Frida Kahlo',
 ('data/inspirational_quotes.xml', 'quote_28', 'quote'): "Pieds, pourquoi en ai-je besoin si j'ai "
                                                         'des ailes pour voler?',
 ('data/inspirational_quotes.xml', 'quote_29', 'author'): 'Frida Kahlo',
 ('data/inspirational_quotes.xml', 'quote_29', 'quote'): 'Je ne peins pas des rêves ou des '
                                                         'cauchemars, je peins ma propre réalité.',
 ('data/inspirational_quotes.xml', 'quote_30', 'author'): 'Pablo Picasso',
 ('data/inspirational_quotes.xml', 'quote_30', 'quote'): "L'art lave notre âme de la poussière du "
                                                         'quotidien.',
 ('data/inspirational_quotes.xml', 'quote_31', 'author'): 'Pablo Picasso',
 ('data/inspirational_quotes.xml', 'quote_31', 'quote'): 'Certains peintres transforment le soleil '
                                                         "en un point jaune, d'autres transforment "
                                                         'un point jaune en soleil.',
 ('data/inspirational_quotes.xml', 'quote_32', 'author'): 'Pablo Picasso',
 ('data/inspirational_quotes.xml', 'quote_32', 'quote'): 'Je ne cherche pas, je trouve.',
 ('data/inspirational_quotes.xml', 'quote_33', 'author'): "Augustin d'Hippone",
 ('data/inspirational_quotes.xml', 'quote_33', 'quote'): 'Le monde est un livre et ceux qui ne '
                                                         "voyagent pas n'en lisent qu'une page.",
 ('data/inspirational_quotes.xml', 'quote_34', 'author'): 'Oscar Wilde',
 ('data/inspirational_quotes.xml', 'quote_34', 'quote'): 'Soyez vous-même, tous les autres sont '
                                                         'déjà pris.',
 ('data/inspirational_quotes.xml', 'quote_35', 'author'): 'Oscar Wilde',
 ('data/inspirational_quotes.xml', 'quote_35', 'quote'): 'Nous sommes tous dans le caniveau, mais '
                                                         "certains d'entre nous regardent les "
                                                         'étoiles.',
 ('data/inspirational_quotes.xml', 'quote_36', 'author'): 'Oscar Wilde',
 ('data/inspirational_quotes.xml', 'quote_36', 'quote'): 'La désobéissance est la vertu originelle '
                                                         "de l'homme.",
 ('data/inspirational_quotes.xml', 'quote_37', 'author'): 'Oscar Wilde',
 ('data/inspirational_quotes.xml', 'quote_37', 'quote'): "La carte du monde qui n'inclut pas "
                                                         "l'Utopie ne vaut pas qu'on y jette un "
                                                         "coup d'œil.",
 ('data/inspirational_quotes.xml', 'quote_38', 'author'): 'Khalil Gibran',
 ('data/inspirational_quotes.xml', 'quote_38', 'quote'): "Le travail est l'amour rendu visible.",
 ('data/inspirational_quotes.xml', 'quote_39', 'author'): 'Khalil Gibran',
 ('data/inspirational_quotes.xml', 'quote_39', 'quote'): 'Vos enfants ne sont pas vos enfants. Ils '
                                                         "sont les fils et les filles de l'appel "
                                                         'de la Vie à elle-même.',
 ('data/inspirational_quotes.xml', 'quote_40', 'author'): 'Mai 68',
 ('data/inspirational_quotes.xml', 'quote_40', 'quote'): "Soyez réalistes, demandez l'impossible.",
 ('data/inspirational_quotes.xml', 'quote_41', 'author'): 'Mai 68',
 ('data/inspirational_quotes.xml', 'quote_41', 'quote'): 'Sous les pavés, la plage.',
 ('data/inspirational_quotes.xml', 'quote_42', 'author'): 'Slogan altermondialiste',
 ('data/inspirational_quotes.xml', 'quote_42', 'quote'): 'Un autre monde est possible.',
 ('data/inspirational_quotes.xml', 'quote_43', 'author'): 'Proverbe africain',
 ('data/inspirational_quotes.xml', 'quote_43', 'quote'): "Jusqu'à ce que les lions aient leurs "
                                                         "propres historiens, l'histoire de la "
                                                         'chasse glorifiera toujours le chasseur.',
 ('data/inspirational_quotes.xml', 'quote_44', 'author'): 'Lilla Watson',
 ('data/inspirational_quotes.xml', 'quote_44', 'quote'): "Si tu es venu ici pour m'aider, tu perds "
                                                         'ton temps. Mais si tu es venu parce que '
                                                         'ta libération est liée à la mienne, '
                                                         'alors travaillons ensemble.',
 ('data/inspirational_quotes.xml', 'quote_45', 'author'): 'Martin Niemöller',
 ('data/inspirational_quotes.xml', 'quote_45', 'quote'): 'Quand ils sont venus chercher les '
                                                         "communistes, je n'ai rien dit, je "
                                                         "n'étais pas communiste...",
 ('data/inspirational_quotes.xml', 'quote_46', 'author'): 'Marcel Proust',
 ('data/inspirational_quotes.xml', 'quote_46', 'quote'): 'Le vrai voyage de découverte ne consiste '
                                                         'pas à chercher de nouveaux paysages, '
                                                         'mais à avoir de nouveaux yeux.',
 ('data/inspirational_quotes.xml', 'quote_47', 'author'): 'Pearl S. Buck',
 ('data/inspirational_quotes.xml', 'quote_47', 'quote'): 'Une société se juge à la façon dont elle '
                                                         'traite ses membres les plus vulnérables.',
 ('data/inspirational_quotes.xml', 'quote_48', 'author'): 'Gandhi',
 ('data/inspirational_quotes.xml', 'quote_48', 'quote'): 'On reconnaît le degré de civilisation '
                                                         "d'un peuple à la manière dont il traite "
                                                         'ses animaux.',
 ('data/inspirational_quotes.xml', 'quote_49', 'author'): 'Audre Lorde',
 ('data/inspirational_quotes.xml', 'quote_49', 'quote'): "Je ne suis pas libre tant qu'une seule "
                                                         "femme ne l'est pas, même si ses chaînes "
                                                         'sont très différentes des miennes.',
 ('data/inspirational_quotes.xml', 'quote_50', 'author'): 'Audre Lorde',
 ('data/inspirational_quotes.xml', 'quote_50', 'quote'): 'Les outils du maître ne détruiront '
                                                         'jamais la maison du maître.',
 ('data/inspirational_quotes.xml', 'quote_51', 'author'): 'Audre Lorde',
 ('data/inspirational_quotes.xml', 'quote_51', 'quote'): "Prendre soin de moi n'est pas de "
                                                         "l'auto-complaisance, c'est de "
                                                         "l'auto-préservation, et c'est un acte de "
                                                         'guerre politique.',
 ('data/inspirational_quotes.xml', 'quote_52', 'author'): 'Oscar Wilde',
 ('data/inspirational_quotes.xml', 'quote_52', 'quote'): "Un rêveur est quelqu'un qui ne trouve "
                                                         "son chemin qu'au clair de lune, et sa "
                                                         "punition est de voir l'aube avant le "
                                                         'reste du monde.',
 ('data/inspirational_quotes.xml', 'quote_53', 'author'): 'Ernst Jünger',
 ('data/inspirational_quotes.xml', 'quote_53', 'quote'): 'Les frontières sont les cicatrices de '
                                                         "l'histoire.",
 ('data/inspirational_quotes.xml', 'quote_54', 'author'): 'Victor Hugo',
 ('data/inspirational_quotes.xml', 'quote_54', 'quote'): "La liberté commence où l'ignorance "
                                                         'finit.',
 ('data/inspirational_quotes.xml', 'quote_55', 'author'): 'Émile Chartier (Alain)',
 ('data/inspirational_quotes.xml', 'quote_55', 'quote'): "Rien n'est plus dangereux qu'une idée "
                                                         "quand on n'en a qu'une.",
 ('data/inspirational_quotes.xml', 'quote_56', 'author'): 'Jean-Jacques Rousseau',
 ('data/inspirational_quotes.xml', 'quote_56', 'quote'): "L'homme est né libre, et partout il est "
                                                         'dans les fers.',
 ('data/inspirational_quotes.xml', 'quote_57', 'author'): 'André Malraux',
 ('data/inspirational_quotes.xml', 'quote_57', 'quote'): "La culture ne s'hérite pas, elle se "
                                                         'conquiert.',
 ('data/inspirational_quotes.xml', 'quote_58', 'author'): 'Lewis Carroll',
 ('data/inspirational_quotes.xml', 'quote_58', 'quote'): "La seule façon d'atteindre l'impossible, "
                                                         "c'est de croire que c'est possible.",
 ('data/inspirational_quotes.xml', 'quote_59', 'author'): 'Friedrich Nietzsche (adapté)',
 ('data/inspirational_quotes.xml', 'quote_59', 'quote'): 'Ce qui ne me tue pas me rend plus '
                                                         "fort... mais d'abord, ça fait vraiment "
                                                         'mal.',
 ('data/inspirational_quotes.xml', 'quote_60', 'author'): 'Anaïs Nin',
 ('data/inspirational_quotes.xml', 'quote_60', 'quote'): 'Nous ne voyons pas les choses comme '
                                                         'elles sont, nous les voyons comme nous '
                                                         'sommes.',
 ('data/inspirational_quotes.xml', 'quote_61', 'author'): 'Proverbe africain',
 ('data/inspirational_quotes.xml', 'quote_61', 'quote'): 'Seul on va plus vite, ensemble on va '
                                                         'plus loin.',
 ('data/inspirational_quotes.xml', 'quote_62', 'author'): 'Proverbe chinois',
 ('data/inspirational_quotes.xml', 'quote_62', 'quote'): 'Le meilleur moment pour planter un arbre '
                                                         'était il y a vingt ans. Le deuxième '
                                                         "meilleur moment, c'est maintenant.",
 ('data/inspirational_quotes.xml', 'quote_63', 'author'): 'Proverbe chinois',
 ('data/inspirational_quotes.xml', 'quote_63', 'quote'): 'On ne peut pas empêcher les oiseaux de '
                                                         'la tristesse de voler au-dessus de nos '
                                                         "têtes, mais on peut les empêcher d'y "
                                                         'faire leur nid.',
 ('data/inspirational_quotes.xml', 'quote_64', 'author'): 'Proverbe amérindien (Cree)',
 ('data/inspirational_quotes.xml', 'quote_64', 'quote'): 'Quand le dernier arbre sera abattu, la '
                                                         'dernière rivière empoisonnée, le dernier '
                                                         'poisson capturé, alors seulement vous '
                                                         "réaliserez que l'argent ne se mange pas.",
 ('data/inspirational_quotes.xml', 'quote_65', 'author'): 'Proverbe amérindien',
 ('data/inspirational_quotes.xml', 'quote_65', 'quote'): "Nous n'héritons pas de la terre de nos "
                                                         "ancêtres, nous l'empruntons à nos "
                                                         'enfants.',
 ('data/inspirational_quotes.xml', 'quote_66', 'author'): 'Chef Seattle',
 ('data/inspirational_quotes.xml', 'quote_66', 'quote'): "La terre n'appartient pas à l'homme, "
                                                         "c'est l'homme qui appartient à la terre.",
 ('data/inspirational_quotes.xml', 'quote_67', 'author'): 'Edmund Hillary',
 ('data/inspirational_quotes.xml', 'quote_67', 'quote'): "Ce n'est pas la montagne que nous "
                                                         'conquérons, mais nous-mêmes.',
 ('data/inspirational_quotes.xml', 'quote_68', 'author'): 'Proverbe japonais',
 ('data/inspirational_quotes.xml', 'quote_68', 'quote'): 'Tombe sept fois, relève-toi huit.',
 ('data/inspirational_quotes.xml', 'quote_69', 'author'): 'Proverbe japonais',
 ('data/inspirational_quotes.xml', 'quote_69', 'quote'): 'Le bambou qui plie est plus fort que le '
                                                         'chêne qui résiste.',
 ('data/inspirational_quotes.xml', 'quote_70', 'author'): 'Stanislaw Jerzy Lec',
 ('data/inspirational_quotes.xml', 'quote_70', 'quote'): 'Dans une avalanche, aucun flocon ne se '
                                                         'sent responsable.',
 ('data/inspirational_quotes.xml', 'quote_71', 'author'): 'Martin Luther King Jr.',
 ('data/inspirational_quotes.xml', 'quote_71', 'quote'): "L'injustice quelque part est une menace "
                                                         'pour la justice partout.',
 ('data/inspirational_quotes.xml', 'quote_72', 'author'): 'Martin Luther King Jr.',
 ('data/inspirational_quotes.xml', 'quote_72', 'quote'): "La question n'est pas de savoir si nous "
                                                         'allons être des extrémistes, mais de '
                                                         "savoir quel type d'extrémistes nous "
                                                         'serons.',
 ('data/inspirational_quotes.xml', 'quote_73', 'author'): 'Martin Luther King Jr.',
 ('data/inspirational_quotes.xml', 'quote_73', 'quote'): 'Nos vies commencent à prendre fin le '
                                                         'jour où nous gardons le silence sur les '
                                                         'choses qui comptent.',
 ('data/inspirational_quotes.xml', 'quote_74', 'author'): 'Nelson Mandela',
 ('data/inspirational_quotes.xml', 'quote_74', 'quote'): "L'éducation est l'arme la plus puissante "
                                                         'pour changer le monde.',
 ('data/inspirational_quotes.xml', 'quote_75', 'author'): 'Nelson Mandela',
 ('data/inspirational_quotes.xml', 'quote_75', 'quote'): "Cela semble toujours impossible jusqu'à "
                                                         'ce que ce soit fait.',
 ('data/inspirational_quotes.xml', 'quote_76', 'author'): 'Nelson Mandela',
 ('data/inspirational_quotes.xml', 'quote_76', 'quote'): 'Je ne perds jamais. Soit je gagne, soit '
                                                         "j'apprends.",
 ('data/inspirational_quotes.xml', 'quote_77', 'author'): 'Gandhi',
 ('data/inspirational_quotes.xml', 'quote_77', 'quote'): 'Sois le changement que tu veux voir dans '
                                                         'le monde.',
 ('data/inspirational_quotes.xml', 'quote_78', 'author'): 'Gandhi',
 ('data/inspirational_quotes.xml', 'quote_78', 'quote'): 'La force ne vient pas des capacités '
                                                         "physiques, elle vient d'une volonté "
                                                         'indomptable.',
 ('data/inspirational_quotes.xml', 'quote_79', 'author'): 'Gandhi',
 ('data/inspirational_quotes.xml', 'quote_79', 'quote'): 'Vis comme si tu devais mourir demain. '
                                                         'Apprends comme si tu devais vivre '
                                                         'toujours.',
 ('data/inspirational_quotes.xml', 'quote_80', 'author'): 'Gandhi',
 ('data/inspirational_quotes.xml', 'quote_80', 'quote'): 'Le silence devient lâcheté quand '
                                                         "l'occasion exige de dire toute la vérité "
                                                         "et d'agir en conséquence.",
 ('data/inspirational_quotes.xml', 'quote_81', 'author'): 'Audre Lorde',
 ('data/inspirational_quotes.xml', 'quote_81', 'quote'): 'On ne peut pas démanteler la maison du '
                                                         'maître avec les outils du maître.',
 ('data/inspirational_quotes.xml', 'quote_82', 'author'): 'Maya Angelou',
 ('data/inspirational_quotes.xml', 'quote_82', 'quote'): 'Je suis une femme phénoménale. '
                                                         "Phénoménalement. C'est moi, ça.",
 ('data/inspirational_quotes.xml', 'quote_83', 'author'): 'Maya Angelou',
 ('data/inspirational_quotes.xml', 'quote_83', 'quote'): 'Les gens oublieront ce que tu as dit, '
                                                         'ils oublieront ce que tu as fait, mais '
                                                         "ils n'oublieront jamais ce que tu leur "
                                                         'as fait ressentir.',
 ('data/inspirational_quotes.xml', 'quote_84', 'author'): 'Maya Angelou',
 ('data/inspirational_quotes.xml', 'quote_84', 'quote'): "Il n'y a pas de plus grande agonie que "
                                                         'de porter en soi une histoire jamais '
                                                         'racontée.',
 ('data/inspirational_quotes.xml', 'quote_85', 'author'): 'bell hooks',
 ('data/inspirational_quotes.xml', 'quote_85', 'quote'): "Le féminisme n'a jamais signifié haïr "
                                                         'les hommes. Il signifie lutter pour '
                                                         "l'égalité des sexes.",
 ('data/inspirational_quotes.xml', 'quote_86', 'author'): 'Virginia Woolf',
 ('data/inspirational_quotes.xml', 'quote_86', 'quote'): 'Une chambre à soi et cinq cents livres '
                                                         'de rente.',
 ('data/inspirational_quotes.xml', 'quote_87', 'author'): 'Virginia Woolf',
 ('data/inspirational_quotes.xml', 'quote_87', 'quote'): 'On ne peut trouver la paix en évitant la '
                                                         'vie.',
 ('data/inspirational_quotes.xml', 'quote_88', 'author'): 'Coco Chanel',
 ('data/inspirational_quotes.xml', 'quote_88', 'quote'): 'Le plus courageux des actes est encore '
                                                         'de penser par soi-même. À voix haute.',
 ('data/inspirational_quotes.xml', 'quote_89', 'author'): 'Albert Einstein',
 ('data/inspirational_quotes.xml', 'quote_89', 'quote'): 'La logique vous mènera de A à B. '
                                                         "L'imagination vous mènera partout.",
 ('data/inspirational_quotes.xml', 'quote_90', 'author'): 'Albert Einstein',
 ('data/inspirational_quotes.xml', 'quote_90', 'quote'): 'Tout le monde est un génie. Mais si vous '
                                                         'jugez un poisson sur sa capacité à '
                                                         'grimper à un arbre, il passera sa vie à '
                                                         "croire qu'il est stupide.",
 ('data/inspirational_quotes.xml', 'quote_91', 'author'): 'Albert Einstein',
 ('data/inspirational_quotes.xml', 'quote_91', 'quote'): "La créativité, c'est l'intelligence qui "
                                                         "s'amuse.",
 ('data/inspirational_quotes.xml', 'quote_92', 'author'): 'Bono',
 ('data/inspirational_quotes.xml', 'quote_92', 'quote'): 'La musique peut changer le monde parce '
                                                         "qu'elle peut changer les gens.",
 ('data/inspirational_quotes.xml', 'quote_93', 'author'): 'Bob Marley',
 ('data/inspirational_quotes.xml', 'quote_93', 'quote'): "Émancipez-vous de l'esclavage mental. "
                                                         "Personne d'autre que nous-mêmes ne peut "
                                                         'libérer nos esprits.',
 ('data/inspirational_quotes.xml', 'quote_94', 'author'): 'Proverbe birman',
 ('data/inspirational_quotes.xml', 'quote_94', 'quote'): 'Un bon arbre peut abriter dix mille '
                                                         'oiseaux.',
 ('data/inspirational_quotes.xml', 'quote_95', 'author'): 'Sénèque',
 ('data/inspirational_quotes.xml', 'quote_95', 'quote'): "La vie, ce n'est pas d'attendre que "
                                                         "l'orage passe, c'est d'apprendre à "
                                                         'danser sous la pluie.',
 ('data/inspirational_quotes.xml', 'quote_96', 'author'): 'Socrate',
 ('data/inspirational_quotes.xml', 'quote_96', 'quote'): 'Connais-toi toi-même.',
 ('data/inspirational_quotes.xml', 'quote_97', 'author'): 'Aristote',
 ('data/inspirational_quotes.xml', 'quote_97', 'quote'): 'Le bonheur dépend de nous seuls.',
 ('data/inspirational_quotes.xml', 'quote_98', 'author'): 'Sénèque',
 ('data/inspirational_quotes.xml', 'quote_98', 'quote'): "Ce n'est pas parce que les choses sont "
                                                         "difficiles que nous n'osons pas, c'est "
                                                         "parce que nous n'osons pas qu'elles sont "
                                                         'difficiles.',
 ('data/inspirational_quotes.xml', 'quote_99', 'author'): 'Louis Pasteur',
 ('data/inspirational_quotes.xml', 'quote_99', 'quote'): 'Le hasard ne favorise que les esprits '
                                                         'préparés.'}
# Champs devenus traduisibles dans cette version (voir la note sur les retouches).
CONVERTIS = [('daily.digest.quote', 'author'), ('daily.digest.quote', 'quote')]


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    langues = [code for code, _nom in env["res.lang"].get_installed() if code != "en_US"]
    if langues:
        env["ir.module.module"]._load_module_terms([MODULE], langues, overwrite=True)
    arbres = {}
    for (fichier, xmlid, champ), francais in LIVRES_FR.items():
        if fichier not in arbres:
            with file_open(f"{MODULE}/{fichier}", "rb") as f:
                arbres[fichier] = etree.parse(f)
        rec = env.ref(f"{MODULE}.{xmlid}", raise_if_not_found=False)
        noeud = arbres[fichier].find(f".//record[@id='{xmlid}']/field[@name='{champ}']")
        if not rec or noeud is None or not (noeud.text or "").strip():
            continue
        rec_en = rec.with_context(lang="en_US")
        if (rec_en[champ] or "").strip() != francais:
            _logger.info("%s : %s.%s retouché à la main, laissé tel quel", MODULE, xmlid, champ)
            if (rec._name, champ) in CONVERTIS:
                # 🔴 La colonne vient d'être convertie : le catalogue a ajouté le
                # français LIVRÉ en fr_CA, qui masquerait la retouche. On la recopie.
                rec.update_field_translations(champ, {lang: rec_en[champ] for lang in langues})
            continue
        rec_en.write({champ: noeud.text.strip()})
