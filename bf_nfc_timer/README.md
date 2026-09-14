# NFC tags: timer (`bf_nfc_timer`)

A tag on a file, a desk, a laptop. Bring the phone close: that task's timer
starts. Bring it close again when leaving: the timer stops **and the timesheet
is logged**.

## Why stopping also logs

The timesheet timer separates two steps: stopping, and confirming the
timesheet in a dialog. A dialog assumes a browser and someone in front of it; a
tap has neither. This module chains both in the same request, with the rounding
configured in the settings, so a tap and a click produce the same duration.

## Gestures

| Gesture | What it does |
|---|---|
| Timer: start or stop | one tag for both halves: the first tap starts, the next stops and logs |
| Timer: start | refuses politely if the timer is already running |
| Timer: stop and log | refuses politely if no timer is running |

Timer gestures do not accept offline taps: a timer started hours after the tap
would log time nobody worked.

## Requirements

`bf_nfc` and `bf_timesheet_timer`.

## License

Business Source License 1.1, see [LICENSE](LICENSE).
