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

## Paramètres système

| Clé | Défaut | Rôle |
|---|---|---|
| `bf_mailing_signup.list_id` | *(aucun)* | La `mailing.list` visée. **Sans elle, rien ne s'inscrit** et un avertissement est journalisé. |
| `bf_mailing_signup.base_url` | `https://symbifox.com` | Le domaine écrit dans le lien de confirmation. |
| `bf_mailing_signup.reply_to_fr` | *(aucun)* | Adresse de réponse du courriel français. |
| `bf_mailing_signup.reply_to_en` | *(aucun)* | Adresse de réponse du courriel anglais. |

`email_from` n'est **pas** posé : `mail.mail` retombe sur l'adresse d'envoi par
défaut de l'instance, la seule dont le domaine est aligné en SPF et DKIM. En
poser une ici est le meilleur moyen de faire classer la confirmation en pourriel.

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
