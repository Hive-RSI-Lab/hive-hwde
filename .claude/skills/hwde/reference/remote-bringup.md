# Remote bring-up: the Hive brings the board up on its own

The person, 10-28 22:05: "plug it in to power, connect some diagnostic USB
or something to a laptop, or just have it connect to the internet, run some
script on my laptop that configures wifi etc, and then i just leave it
plugged in at home and the hive talks to it, brings it up, revs the FW,
tests things, etc, all on its own". 22:06: "This should be the flow for all
board designs".

This page is that flow for any board with an MCU and a network, generalised
from hifi-receiver-2's design (`docs/remote.md` in that project, task
261029-014757). The board side is firmware (the hive-fw module builds,
tests in QEMU and flashes); hwde's part is that the board is designed for
it (self-observation.md) and that its bring-up close records what was
measured (`state.py bringup`). Decide per board what applies: a board with
no MCU is brought up on a bench with its test points, and its plan says so.

## The person's part, as small as it can be

1. The boards arrive. Plug one into power, and for setup a USB cable to a
   laptop.
2. Run one command from the project (`python3 bench/provision.py` on
   hifi-receiver-2). It finds the board's serial port, flashes the current
   firmware if the board is blank or older (JLC ships MCUs empty), asks for
   the Wi-Fi name and password, sends them over the console, and waits until
   the board says it is online. It ends by printing the board's id and
   "leave it plugged in".
3. Move it where it lives and leave it. The Hive pings the person when the
   board is ready for use, or with what it found.

Anything that needs the person's hands (a dummy load, a thermocouple, a
listen) goes in one batch at the end, as a short list, never trickled.

## The channel: the board dials out

The board connects outward to a relay, and so does the Hive; neither opens a
port. hifi-receiver-2 chose MQTT over TLS (ESP-IDF's esp-mqtt client; a free
hosted broker; retained status and last-will give online/offline without
polling; the Hive's client tunnels through the sandbox's proxy). A direct
LAN path (`--url http://<ip>`) is the fallback when the Hive's machine shares
the board's network and has an egress grant to it.

- **Commands are signed** (Ed25519; the public key is compiled into the
  firmware, up to two for rotation) with the device id, an increasing id
  and a timestamp, so a leaked broker password can't drive the board or
  flash it. Replies and status ride the broker's login.
- **The broker account, the secrets folder and any egress grant are not
  the agent's**: the account is the person's to make, the rest a warden
  request (`hive agent warden`), asked once per project.

## Firmware updates with rollback

- Two app slots (plus a factory/recovery app where the MCU has room), so a
  new image is written beside the running one. Decide the partition table
  before the first board is flashed: once boards are in the field it can
  only change over USB.
- The update goes over the same signed channel in chunks (begin with size
  and sha256, chunks at offsets, finish checks the hash and reboots).
- The new image boots on trial and must, within a stated time, start, join
  the network, reach the relay and pass the self-test items that don't need
  a load; then it marks itself good. A failed check, a timeout, a crash or
  a watchdog rolls back to the previous image, which reports why.

## The unattended run

The Hive's side (a project's `bench/`, run from a worker) does, in order,
stopping at the first failure that could damage something:

1. Read the board's status and info; run its self-test.
2. Update its firmware to the current build (rollback armed).
3. Read every rail and status (self-observation.md) in each power state the
   board can reach on its own.
4. Exercise the outputs at a quiet level and read them back through the
   loopback taps (gain, level, response, distortion, noise, offset).
5. Write the results in hwde's bring-up schema (`scripts/lib/bringuplib.py`:
   each item with its measured value, its limit and a verdict; a project's
   own session file converts when it carries `selftests[].items`, a
   `verdicts` map and a by-hand `checklist`).
6. Close the bring-up in the board's workspace:
   `state.py bringup --workspace <ws> --results bringup/results.json`. It
   refuses a file with a required item unmeasured, and logs
   `bringup_passed`, `bringup_derated` or `bringup_failed` as the items say.
   `attest.py disposition` then reads `bring-up-passed`, `derated` or
   `rework-required`, and `knowledge.py --prove` cites the results file.
7. Tell the person: ready for use (with the one-page results), or what
   failed, what it means, and the contingency it calls for.

Items only a bench can take (power into a real load, a thermocouple at 1/8
power) are `skip` with a reason until they are taken; mark one
`optional: true` only when the board can be called working without it.

## What is not this

`scripts/bench.py` tunes hwde's own pipeline stages on frozen fixtures; it
has nothing to do with a board in hand. The hardware bench lives in the
board's project (hifi-receiver-2's `bench/` is the first).
