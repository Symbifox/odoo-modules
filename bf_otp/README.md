# Symbifox — Jetons OTP

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
  témoin — un texte connu chiffré, qui permet de dire « mauvaise phrase » sans
  rien savoir de la bonne.
* **Aucun condensat de la phrase n'est stocké** : il donnerait à qui lit la base
  un meilleur point de départ hors ligne que le chiffré lui-même.
* Les codes se calculent dans la page, conformément aux RFC 4226 et 6238.

> ⚠️ **Phrase de passe perdue, jetons perdus.** Personne ne peut les rendre, et
> c'est le prix de ne pas les détenir. Range ta phrase ailleurs avant de créer
> ton coffre.

## Ce que le module fait

* Coffre personnel, ouvert par phrase de passe ou par **clé d'accès**
  (WebAuthn, extension PRF : Touch ID, Windows Hello, clé matérielle), refermé
  seul après cinq minutes sans activité
* Jetons TOTP et HOTP, SHA-1 / SHA-256 / SHA-512, 6 à 8 chiffres
* Rattachement à un **client** et à un **projet** ; sans étiquette de
  regroupement, le client sert de regroupement
* Favoris épinglés en tête, tri par dernière utilisation, recherche au clavier
* Ajout par adresse `otpauth://` ou à la main
* Import d'un export du gestionnaire OTP de Nextcloud, chiffré ou en clair, le
  déchiffrement se faisant dans la page

## Ce que le module ne fait pas

* Le **partage** de jetons entre personnes, qui demande un chiffrement par
  enveloppe (clé d'item emballée pour chaque destinataire)
* La lecture d'un **QR par la caméra**

## Les clés d'accès, et leurs limites

Une clé d'accès scelle une **copie de la clé du coffre** ; la clé du coffre ne
change pas, donc aucune graine n'est ré-encryptée à l'enrôlement.

> ⚠️ Une clé d'accès est liée à une **origine** et à un **appareil**. Celle
> enrôlée sur un domaine n'ouvrira pas le coffre depuis un autre domaine ni
> depuis une extension de navigateur. **La phrase de passe reste le seul
> recours** : ne la considère jamais comme facultative.

L'enrôlement redemande la phrase, exprès : ajouter un moyen d'ouvrir un coffre se
confirme par ce qu'on sait, pas par le fait qu'un écran soit resté ouvert.

## Exigences

* Une **connexion sécurisée** (HTTPS, ou `localhost`). `crypto.subtle` n'existe
  pas autrement, et l'application le dit clairement plutôt que d'échouer champ
  par champ.
* Odoo 18, et l'application **Projet** (pour le champ Projet du jeton).

## Sécurité — ce que le serveur peut et ne peut pas

| | |
|---|---|
| Lire une graine | **Non**, jamais. Il n'a ni la clé ni de quoi la dériver. |
| Produire un code | **Non.** Aucune bibliothèque de chiffrement n'est importée côté serveur, et un test le refuse. |
| Voir la liste des comptes protégés | **Oui.** Émetteur et compte sont en clair, à dessein : c'est ce qui permet de chercher et de trier. |
| Voir les jetons d'autrui | **Non.** Une règle d'enregistrement limite chaque coffre à son propriétaire. |
| Mentir sur la liste des clés d'accès | **Oui**, et ça peut empêcher d'ouvrir. Ça ne donne aucune graine. Le remède est la phrase de passe, qui ne dépend d'aucune liste. |

## Licence

LGPL-3.
