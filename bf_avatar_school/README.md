# Symbifox Avatars at School

A minor student's avatar, without a beard, a moustache or a bald head.

## Why

A generated avatar is drawn from the name, so a school that installs `bf_avatar` gives a
character to every student without a photo. Nothing stopped that draw from handing a seven
year old a full beard, grey hair or a bald head. On the Symbifox school demo, 11 of the 49
students would have been given an adult's face; on the main demo, 3 of 18 already had one.

## What it takes away, and from whom

From students under 18, and from them alone. Staff, guardians and students of age are
untouched.

| Style | Taken away |
|---|---|
| Open Peeps | all facial hair; the `grayBun`, `grayMedium`, `grayShort`, `noHair1`, `noHair2`, `noHair3` hairstyles |
| Notionists | all facial hair |

Open Peeps' `shaved1` to `shaved3` **stay**: a buzz cut with a full hairline is not
baldness, it is an ordinary teenage haircut. Notionists is line art with no colour palette
at all, and none of its 64 hairstyles is bald or receding. Both calls come from looking at
all 48 and 64 variants one by one.

## The two traps this module avoids

**The age read is majority, not the age of consent.** `privacy_consent`'s `is_minor_child`
means "under the age of consent", which is 14 in Québec. Reading it here would leave every
14 to 17 year old with a beard. This module reads `student_birthdate`.

**The date of birth is read with sudo.** It is restricted to `base.group_user`, while a
contact's avatar is computed for whoever is looking, portal visitors included. Without
sudo, the read would raise on the very pages this module protects.

## How it is wired

`bf_avatar` exposes `bf.avatar._seed_exclusions(record, style)`, which returns `{}`. This
module overrides it. The exclusions travel down to `render.seed_config` and
`render.clean_config`: a barred part is never drawn, never kept if it had been composed
before, and never offered in the builder.

This is a **hard ban**, unlike `render.SEED_EXCLUDE`, which only keeps a part out of the
seeded draw while leaving it in the builder — that is how the gamification bridge sells
hats and sunglasses.

**The draw stays aligned.** A barred slot still spends its roll, and the draw is still made
on everybody's pool; only the person who lands on a barred part gets a stand-in, drawn from
a separate hash. Two cheaper rules were tried and measured first: drawing from the
shortened pool renumbers it and changes the hairstyle of nearly every barred person, and
walking to the next part in the pool herds them all onto the same stand-in. A test covers
this (`test_interdire_une_piece_ne_decale_pas_la_suite_du_tirage`).

## On install

`post_init_hook` redraws the **stored** avatar of minor students that have one. Contacts
need nothing: their avatar is computed when read. Adults are not touched, because they
would produce the same bytes.
