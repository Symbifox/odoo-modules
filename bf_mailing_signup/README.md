# Inscription publique à une liste d'envoi

Une route qui accepte un formulaire HTML ordinaire et inscrit l'adresse à une
`mailing.list`, à double consentement, sans reCaptcha, sans script et sans
ressource tierce.

## Pourquoi pas le formulaire d'Odoo

`/website/form/` fait déjà ce travail, mais `google_recaptcha` le protège dès
qu'il est installé — et il l'est, avec de vraies clés, parce que les formulaires
de contact en ont besoin. Sur symbifox.com, charger le reCaptcha de Google
contredirait la promesse écrite en tête de la configuration du proxy, *aucune
page de ce site ne charge de ressource tierce*, qui est aussi un argument de
vente et que la CSP du domaine impose. Le désactiver globalement affaiblirait des
formulaires en production sur un autre domaine. Troisième voie, recommandée dans
la tâche BF #24557 : ce module.

## Le double consentement n'est pas un raffinement

La page qui porte le formulaire est un fichier statique : pas de session Odoo,
donc **pas de jeton CSRF possible**. La route s'ouvre forcément sans.

Ce que ça laisserait ouvert, le double consentement le referme : l'inscription
naît `opt_out=True` et ne reçoit qu'un seul courriel, celui du lien de
confirmation. Seule la personne qui relève l'adresse peut confirmer. Une adresse
déjà désinscrite ne se réactive pas non plus par une resoumission du formulaire.
Et la confirmation laisse une preuve de consentement exprès datée au fil du
contact, ce dont une entreprise qui vend de la conformité a besoin.

## Le lien ne stocke rien

Le jeton est `HMAC(database.secret, "liste:adresse:quantième")`, tronqué à
32 caractères. La validité de sept jours s'obtient en réessayant les sept
derniers quantièmes : rien à écrire, rien à purger, rien à faire expirer par un
cron. Un lien plus vieux ne confirme plus, il faut refaire une demande.

## Routes

| Méthode | Route | Effet |
|---|---|---|
| POST | `/infolettre` | Crée l'inscription désactivée et envoie le lien. Redirige **toujours** vers la page de remerciement. |
| GET | `/infolettre/confirmer?e=&j=&lang=` | Lève l'`opt_out` si le jeton vaut. Sinon renvoie au formulaire. |

⚠️ Le POST rend la **même** réponse quoi qu'il arrive : adresse connue, adresse
refusée, pot de miel rempli, plafond atteint. Une réponse qui varierait ferait de
la route un oracle : on saurait, en essayant une adresse, si elle est sur la
liste.

## Champs attendus du formulaire

| Champ | Rôle |
|---|---|
| `courriel` | l'adresse. `type="email" required` côté navigateur suffit à écarter les fautes de frappe sans une ligne de script. |
| `lang` | `fr` ou `en`, choisit la langue du courriel et les pages de retour. |
| `site_web` | **pot de miel**. Sorti de l'écran par la feuille de style, `tabindex="-1"`, `aria-hidden`. Rempli, l'envoi est jeté en silence. |

## Le courriel porte la marque

La confirmation est le **premier** courriel que la personne reçoit : s'il arrive
en texte nu, elle doute d'avoir écrit à la bonne enseigne, et un lien qu'on ne
reconnaît pas ne se clique pas. Il part donc dans la même coquille que les
infolettres qui suivront — bandeau marine, sigle, bouton bleu, pied avec
l'adresse postale.

Trois choses, apprises ailleurs, y sont volontaires :

* **Le cadre extérieur est clair.** Plusieurs clients jettent le
  `background-color` de la carte intérieure ; sur un cadre sombre le texte
  foncé devient alors illisible. Les seuls aplats sombres sont posés en
  attribut `bgcolor`, que personne ne retire.
* **Le lien figure aussi en clair**, sous le bouton. Un bouton dont le fond
  disparaît ne se voit plus.
* **Tout est en tableaux et en style en ligne.** Les clients courriel jettent
  les `<style>`, ignorent `max-width` sur un `div` et ne connaissent ni flex ni
  grid.

## Paramètres système

| Clé | Défaut | Rôle |
|---|---|---|
| `bf_mailing_signup.list_id` | *(aucun)* | La `mailing.list` visée. **Sans elle, rien ne s'inscrit** et un avertissement est journalisé. |
| `bf_mailing_signup.base_url` | `https://symbifox.com` | Le domaine écrit dans le lien de confirmation. |
| `bf_mailing_signup.reply_to_fr` | *(aucun)* | Adresse de réponse du courriel français. |
| `bf_mailing_signup.reply_to_en` | *(aucun)* | Adresse de réponse du courriel anglais. |
| `bf_mailing_signup.brand_name` | *(le nom de la société)* | Le nom écrit dans le sujet, l'en-tête et le corps. |
| `bf_mailing_signup.brand_mark_url` | *(aucune)* | URL absolue du sigle, dans le bandeau. |
| `bf_mailing_signup.brand_logo_url` | *(aucune)* | URL absolue du logo de la société, dans le pied. |
| `bf_mailing_signup.notify_email` | *(aucune)* | Qui prévenir d'une inscription. Vide, personne n'est prévenu. Plusieurs adresses séparées par des virgules. |

⚠️ Les deux URL d'images n'ont **aucune valeur par défaut**, et c'est délibéré :
un module publié qui pointerait sur les images de son auteur ferait relever les
ouvertures des abonnés de qui l'installe par le serveur de l'auteur. Sans elles,
l'en-tête garde son libellé et perd la vignette, ce qui ne casse rien.

Le reste de la coquille (palette, police) est en dur : c'est de l'habillage, pas
de la configuration, et sept paramètres de plus pour changer un bleu ne rendent
service à personne.

`email_from` n'est **pas** posé : `mail.mail` retombe sur l'adresse d'envoi par
défaut de l'instance, la seule dont le domaine est aligné en SPF et DKIM. En
poser une ici est le meilleur moyen de faire classer la confirmation en pourriel.

## L'avis interne

Deux moments distincts sont annoncés à `notify_email`, et ils ne disent pas la
même chose :

* **la demande**, quand quelqu'un remplit le formulaire. L'inscription est
  encore désactivée et le courriel de confirmation vient de partir. C'est le
  moment qui révèle une confirmation qui n'arrive jamais, donc un problème de
  délivrabilité — mais c'est aussi celui qu'un robot passé à travers le pot de
  miel peut déclencher ;
* **la confirmation**, quand le lien est suivi. C'est la seule vraie
  inscription, et l'avis ne part **que si l'état a changé** : recharger le lien
  n'est pas une deuxième inscription, et prévenir deux fois pour la même
  personne apprend à ignorer l'avis.

L'avis porte l'adresse, la langue, l'état, la liste visée et un lien direct
vers la fiche du contact dans Odoo.

⚠️ Il est posé **après** le travail utile et tout entier sous `try` : s'il lève,
la personne qui vient de s'inscrire ne doit ni voir d'erreur, ni perdre son
inscription, ni attendre. Un essai le vérifie en le faisant échouer exprès.

## Côté proxy

Le site est servi en statique. Deux réglages sont nécessaires sur l'hôte, et le
formulaire échoue en silence sans eux :

1. un renvoi étroit de `/infolettre` vers Odoo, sur le modèle des blocs `/r/` et
   `/m/` déjà présents ;
2. `form-action 'self'` dans la CSP — elle dit `'none'` par défaut, et un
   navigateur bloque alors l'envoi **sans rien afficher**.

## Pages attendues du site appelant

`/infolettre-merci.html`, `/infolettre-confirme.html`, et leurs jumelles sous
`/en/`. Le module n'ajoute aucune vue : c'est tout son intérêt, la page reste
celle du site, avec sa charte et sa CSP.
