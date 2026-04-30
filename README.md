# HRI Final Project Misty Code

By George Lord

## Setup

1. (Optional) Activate a virtual environment for Python>=3.10
2. `pip install -r requirements.txt`

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

`Python-SDK/run.py` opens a Wizard of Oz controller GUI for the pattern-matching experiment. At the start of each subject session, only the `Intro` button is enabled. The intro makes Misty introduce herself and explain the Raven-style pattern game.

After the intro, the `Correct Guess` and `Wrong Guess` buttons are enabled. The GUI shows the current round, and each guess automatically advances to the next round. The `<` and `>` buttons can manually move backward or forward if the researcher needs to correct the current round.

The controller uses a fixed random seed so the same rounds and wrong answers are used for every subject. The first three rounds are correct. The remaining rounds follow the seeded protocol shown in the GUI. The correct answer key and seed are defined near the top of `Python-SDK/run.py` as `CORRECT_OPTIONS` and `RANDOM_SEED`.

Use `Reset for New Subject` between participants. The text box at the bottom lets the researcher type an immediate custom phrase for Misty to say.
