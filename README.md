# HRI Final Project Misty Code

By George Lord

## Setup (Mac) - From repo root

`python3 -m venv .venv`
`source .venv/bin/activate`
`pip install -r requirements.txt`
`git clone https://github.com/MistyCommunity/Python-SDK.git`
Move `run.py` in to `/Python-SDK/`

```
Usage: python Python-SDK/run.py <Misty IP address>
   or: python Python-SDK/run.py --debug
```

Our misty uses `128.135.202.116`

## Usage

Run with a Misty robot:

```powershell
python Python-SDK/run.py <Misty IP address>
```

Run without a robot for testing the GUI and experiment flow:

```powershell
python Python-SDK/run.py --debug
```

## How It Works

`Python-SDK/run.py` opens a Qt Wizard of Oz controller GUI for the pattern-matching experiment. The main button triggers the next scripted Misty step in the procedure, starting with the warm-up sequence and then moving through the participant questions, eight pattern-matching rounds, and final survey handoff.

The GUI shows the current phase, the exact line Misty will say next, operator notes, the randomized round schedule, and an event log. `Replay Last` repeats the most recently triggered line, `Back One` moves the controller back one script step, and `Stop Misty` sends stop commands to speech and drive.

Rounds 1-3 are always correct. Exactly three of rounds 4-8 are randomized as incorrect for each subject. Use `--seed <number>` for reproducible randomization; otherwise each new subject gets a fresh random seed.

Use `Reset for New Subject` between participants. The text box at the bottom lets the researcher type an immediate custom phrase for Misty to say.
