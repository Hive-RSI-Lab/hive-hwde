# sense_tap_bad

A constraints.json `power[].sense_pads` entry names a pad that is not on the board, or a
pad to read that is not on the entry's net, so `check_kelvin` can't check the sense line.

- **Fix:** name both pads as REF.PAD (`"R113.1": "C114.1"`), the second on the entry's
  `net`. Check the references after a re-annotate.
