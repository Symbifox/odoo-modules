{
    "name": "Work Routes: Privacy Bridge (Quebec Law 25)",
    "summary": "Declares the position read at a stop, its retention rule and the two uses "
               "made of it, and writes every erasure by bf_route's watcher to the register",
    "version": "18.0.1.1.0",
    "category": "Productivity",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    # This bridge DESTROYS nothing: erasing the positions belongs to bf_route's watcher, and
    # how long they are kept belongs to the company's own setting (bf_route_position_days).
    # It declares, it records, it makes the models classifiable. Like the bridges that only
    # record (bf_cx_privacy, bf_meeting_privacy, bf_securetransfer_privacy), it installs by
    # itself as soon as both dependencies are there: a database that can read positions must
    # not be able to do it with a silent register.
    "auto_install": True,
    "depends": [
        "bf_route",
        "privacy_consent",
    ],
    "description": """
Work Routes: Privacy Bridge (Quebec Law 25)
===========================================

`bf_route` reads the position of the person on the road once, at the moment they mark a
stop, and only when the company turned the setting on and the person has read the notice.
All of that is already written, versioned and acknowledged.

What was missing was not the guard rail, it was the trace in the register. Measured on a
real base: dozens of declared purposes, dozens of rules in the retention calendar, and
**not one** of them spoke of the position of a person on the road. This module aside,
**16** other applications
carry a privacy bridge (`bf_email_privacy`, `bf_sms_privacy`, `bf_work_hours_privacy`,
`bf_meeting_privacy`, `bf_sign_privacy`, `bf_securetransfer_privacy`, `bf_cx_privacy`,
`bf_federation_privacy`, `bf_outreach_privacy`, `bf_persona_privacy`, `bf_record_privacy`,
`bf_security_awareness_privacy`, `bf_editorial_audience_privacy` and the three
`bf_employee_experience_*`); this one carried none.

What this bridge does
---------------------

1. **It declares three purposes** in the processing register: the position read at a stop,
   the use of that position to place a customer whose record has no coordinates, and the
   kilometres of the day measured from one reading to the next.
2. **It poses two rules** in the retention calendar, each with its legal basis and its term:
   ROU-001 for the reading, at most 90 days, and ROU-002 for the kilometres derived from it,
   six years.
3. **It keeps the applied term under the declared one, and refuses to start without a
   declaration.** A company that reads positions cannot be set to keep them LONGER than the
   register declares, and cannot begin reading at all while no term is declared. A shorter
   setting is accepted as it stands: section 23 of Law 25 asks for destruction once the
   purpose is accomplished, which is a ceiling, and each entry names the term it applied.
4. **It writes every erasure** to the destruction register, in PARTIAL scope, naming the
   fields that left and also naming what SURVIVES them. The stop itself lives on: its time
   and its mark are the proof of the visit.
5. **It refuses to attest to an emptiness it cannot see.** Before writing the entry it
   reads the row back and raises if the position is still there. Lesson of an earlier
   bridge, where
   the register certified anonymisations that had rewritten no field at all.
6. **It makes both models classifiable**, through the `_privacy_classifiable_models()`
   extension point.

No consent, and that is not an oversight
----------------------------------------

Section 8.1 of Quebec's Law 25 requires **informing** people before activating a technology
that allows them to be located, and saying what means are offered to activate it. It does
not require consent, and that is just as well: an employee's consent to being located by
their employer is not free. Presenting it as consent would suggest a refusal carries no
consequence, when what is really offered is simpler and more honest, namely that the
browser's permission stays theirs and that a refusal lets the mark go through without a
position.

So the purposes carry `requires_consent = False`, like the message archive
(`sms_call_archive`) and video surveillance (`surveillance`).

What the register declares, and what applies it
-----------------------------------------------

**ROU-001, at most 90 days.** Why not one day, a setting seen in the field: the
notice the person reads says the position serves to prove the visit and to settle a
disagreement about a delivery, and a disagreement is raised days later. A position erased
the next morning does not serve the purpose written in the notice, which is the opposite of
the usual complaint about a retention period.

Why not three years, the term of COR-001 (correspondence) and RDV-001 (a dispute about what
had been agreed): a person's position is not correspondence. Three years of readings, even
one per stop, end up drawing their days. Ninety days cover the billing cycle and the dispute
that follows it, without accumulating that picture.

**ROU-002, six years, for the kilometres measured.** The figure is derived from the
readings but it is not a reading: it says how far the day drove, not where it went, so it
outlives them and it is printed in the kilometre register. Six years is the term the Quebec
and federal tax laws set for books and records.

🔴 Nothing applies ROU-002 yet, and its notes say so: no cron destroys a route day, here or
in `bf_route`, because automatically destroying a tax record deserves its own decision and
its own release. The rule declares the regime and makes the gap visible; it does not pretend
to have closed it. An earlier writing of this module declared no rule at all and said the
figure "follows the company's kilometre register": no rule of that
calendar covered vehicles or route days, so the sentence pointed at nothing, which is the
very defect this module refuses elsewhere with another hat on.

🔴 What this bridge does NOT cover
-------------------------------------

* **The phone.** A mark waiting for the network keeps its position in the device's browser
  until it is sent. The server's purge does not reach it.
* **The map tiles.** Without `bf_route.tile_url`, the person's browser fetches the map
  background from the OpenStreetMap Foundation, in the United Kingdom, which learns the
  area being looked at. Since `bf_route` 18.0.1.4.0 the notice names that service and
  says it is outside Canada, in two variants; assessing the transfer itself belongs in
  the privacy impact assessment.
* **Backups.** They keep the earlier state for as long as their own rotation does.
""",
    "data": [
        "data/privacy_purpose_data.xml",
        "data/privacy_retention_calendar_data.xml",
    ],
}
