# Symbifox Tokens OTP

Un gestionnaire de codes à usage unique (TOTP et HOTP) rattaché à Odoo, **dont le
serveur ne détient aucune graine lisible**.

## Le principe, en une phrase

Un code TOTP exige sa graine en clair au moment où on le produit : qui produit,
détient. Ce module fait donc en sorte que ce moment **n'arrive jamais sur le
serveur**.

* La graine est chiffrée **dans le navigateur** (AES-GCM 256), avec une clé
  dérivée par PBKDF2-SHA256 (600 000 itérations) d'une phrase de passe qui n'est
  jamais envoyée.
* Odoo ne garde que le chiffré, son vecteur, le sel, le nombre d'itérations et un
  témoin, un texte connu chiffré, qui permet de dire « mauvaise phrase » sans
  rien savoir de la bonne.
* **Aucun condensat de la phrase n'est stocké** : il donnerait à qui lit la base
  un meilleur point de départ hors ligne que le chiffré lui-même.
* Les codes se calculent dans la page, conformément aux RFC 4226 et 6238.

> ⚠️ **Phrase de passe perdue, tokens perdus** si rien d'autre n'a été prévu.
> C'est le prix de ne pas les détenir. Notez votre phrase ailleurs avant de
> créer votre coffre, et posez un **code de relève** (plus bas) : c'est
> exactement ce à quoi il sert.

## Ce que le module fait

* Coffre personnel, ouvert par phrase de passe ou par **clé d'accès**
  (WebAuthn, extension PRF : Touch ID, Windows Hello, clé matérielle), refermé
  seul après cinq minutes sans activité
* Tokens TOTP et HOTP, SHA-1 / SHA-256 / SHA-512, 6 à 8 chiffres
* Rattachement à un **client** et à un **projet** ; sans étiquette de
  regroupement, le client sert de regroupement, et à défaut l'**émetteur**
  quand il porte plus d'un token
* Icône de marque pour une trentaine de services courants, **embarquée** dans
  le module : aucune favicon n'est récupérée, parce que la requête révélerait
  au service, et à qui regarde le réseau, la liste des comptes protégés. Ce
  qui n'est pas reconnu garde une pastille de couleur calculée du nom.
  Voir `THIRD_PARTY.md`
* Favoris épinglés en tête, tri par dernière utilisation, recherche au clavier
* Ajout par adresse `otpauth://` ou à la main
* **Import** d'un export Symbifox, d'un export **Google Authenticator**
  (`otpauth-migration://`, le contenu de son code QR) ou d'un export du
  gestionnaire OTP de Nextcloud, chiffré ou non. La provenance est reconnue au
  contenu, sans rien choisir, et le déchiffrement se fait toujours dans la page
* **Export chiffré** du coffre, sous une phrase distincte de celle du coffre
* **Codes de relève** : une seconde porte, rangée hors ligne
* **Corbeille** : retirer un token est réversible, le détruire est un second geste
* **Inventaire** dans Odoo : liste, filtres, regroupements, et le compte des
  tokens sur la fiche du client et du projet

## Ce que le module ne fait pas

* Le **partage** de tokens entre personnes, qui demande un chiffrement par
  enveloppe (clé d'item emballée pour chaque destinataire)
* La lecture d'un **QR par la caméra** depuis le navigateur. L'application
  Android le fait ; ici, il faut coller le contenu du code

## Les clés d'accès, et leurs limites

Une clé d'accès scelle une **copie de la clé du coffre** ; la clé du coffre ne
change pas, donc aucune graine n'est ré-encryptée à l'enrôlement.

> ⚠️ Une clé d'accès est liée à une **origine** et à un **appareil**. Celle
> enrôlée sur un domaine n'ouvrira pas le coffre depuis un autre domaine ni
> depuis une extension de navigateur. **La phrase de passe reste le seul
> recours si aucun code de relève n'a été posé** : ne la tenez jamais pour
> facultative.

L'enrôlement redemande la phrase, exprès : ajouter un moyen d'ouvrir un coffre se
confirme par ce qu'on sait, pas par le fait qu'un écran soit resté ouvert.

## Le code de relève

Une clé d'accès est liée à une origine et à un appareil : elle ouvre vite, elle
ne remplace pas la phrase. Le code de relève, lui, tient dans une enveloppe et
survit au portable.

C'est **le même scellé qu'une clé d'accès**, avec un code tiré au sort (160 bits,
32 caractères) à la place du secret rendu par l'authentificateur. La clé du
coffre ne change pas, donc **aucune graine n'est ré-encryptée**. Sa création
redemande la phrase, pour la même raison qu'un enrôlement de clé d'accès.

> ⚠️ **Le code est montré une seule fois** et n'est jamais envoyé au serveur :
> ni le code, ni son condensat. Rien nulle part ne permet de le retrouver.
>
> ⚠️ **Il vaut la phrase de passe.** Sa place est une enveloppe scellée, un
> coffre-fort, ou un gestionnaire de mots de passe qui n'est **pas** celui que
> ces tokens protègent. Cinq codes au plus par coffre.

## L'export

Le fichier est chiffré par une phrase **choisie à l'export**, avec son propre
sel, ses propres itérations et son propre témoin : il ne dépend donc pas de la
clé du coffre Odoo, et il se relit sur une autre instance.

> ⚠️ **Il n'existe aucun export en clair, et c'est délibéré.** Un fichier de
> graines lisibles qui traîne dans un dossier de téléchargements est un coffre
> ouvert dont personne ne se souvient trois mois plus tard.

À la relecture, les rattachements reviennent **par le nom**, et seulement quand
le nom désigne un seul client ou projet de l'instance : deviner mal rattacherait
des tokens au mauvais client sans que personne ne le voie.

## Exigences

* Une **connexion sécurisée** (HTTPS, ou `localhost`). `crypto.subtle` n'existe
  pas autrement, et l'application le dit clairement plutôt que d'échouer champ
  par champ.
* Odoo 18, et l'application **Projet** (pour le champ Projet du token).

## Sécurité : ce que le serveur peut et ne peut pas

| | |
|---|---|
| Lire une graine | **Non**, jamais. Il n'a ni la clé ni de quoi la dériver. |
| Produire un code | **Non.** Aucune bibliothèque de chiffrement n'est importée côté serveur, et un test le refuse. |
| Voir la liste des comptes protégés | **Oui.** Émetteur et compte sont en clair, à dessein : c'est ce qui permet de chercher et de trier. |
| Voir les tokens d'autrui | **Non.** Une règle d'enregistrement limite chaque coffre à son propriétaire. |
| Mentir sur la liste des clés d'accès | **Oui**, et ça peut empêcher d'ouvrir. Ça ne donne aucune graine. Le remède est la phrase de passe, qui ne dépend d'aucune liste. |

## Licence

LGPL-3.
