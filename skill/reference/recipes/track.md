# track - where is the order

One command: `order_track.py --workspace <ws>`. It reads the latched order
number from `fab/order.json` and refreshes `fab/tracking.json`. Unchanged status
is exit 0, not an error - polling is meant to be cheap and non-blocking.

## What it can and cannot see

API-created orders report their status. Orders placed through the JLC WEB flow -
which is every 4-layer board, since the Open API refuses those - may not be
visible to the API at all. If the workspace records a web order number and the
API returns nothing, say exactly that and point at the JLC order page. Do not
report "no order found" for a board that is on a panel somewhere.

The PCB tracking-number surface (shipping carrier + number) is unverified at
this pin; treat a missing shipping field as unknown, not as unshipped.

## Logging

Milestones belong in the audit trail:
`state.py log --workspace <ws> --event order_status --data <file>`. Event names
must match `[a-z][a-z0-9_-]{0,31}` - a live run once stored whole paragraphs as
event keys, which is why the CLI validates them now.

Poll at checkpoints and on resume; never block a session waiting for a fab.
