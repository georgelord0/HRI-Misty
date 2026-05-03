import argparse
import os
import queue
import random
import sys
import threading
import tkinter as tk
from dataclasses import dataclass
from tkinter import messagebox, scrolledtext, ttk

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "Python-SDK"))

from mistyPy.Robot import Robot


TOTAL_ROUNDS = 8
OPTIONS_PER_ROUND = 4
FIRST_GUARANTEED_CORRECT_ROUNDS = 3
WRONG_GUESSES_AFTER_EASY_ROUNDS = 4
RANDOM_SEED = 20260430

# Update this list if your physical boards use a different answer key.
# Values are the option numbers Misty says for a correct guess on each round.
CORRECT_OPTIONS = [1, 3, 2, 4, 1, 2, 4, 3]

INTRO_SCRIPT = """
Hi, I am Misty. It is nice to meet you.

We are going to play a pattern matching game together. On each round, you will
see a three by three board with one space missing, and four possible pieces
that could complete the pattern.

I will look at the board first and say which option I think fits best. You get
to make the final decision, so you do not have to choose the same piece I choose.

The boards will get harder as we go. Please take your time and answer in the
way that seems best to you.
""".strip()

FILLER_PHRASES = [
    "I think the answer is option {option}.",
    "My guess is option {option}.",
    "I would choose option {option}.",
    "This one looks like option {option} to me.",
]


@dataclass(frozen=True)
class RoundPlan:
    round_number: int
    correct_option: int
    wrong_option: int
    protocol_is_correct: bool


def build_round_plan() -> list[RoundPlan]:
    rng = random.Random(RANDOM_SEED)
    later_rounds = list(range(FIRST_GUARANTEED_CORRECT_ROUNDS + 1, TOTAL_ROUNDS + 1))
    wrong_rounds = set(rng.sample(later_rounds, WRONG_GUESSES_AFTER_EASY_ROUNDS))

    plans = []
    for round_number, correct_option in enumerate(CORRECT_OPTIONS, start=1):
        wrong_choices = [
            option
            for option in range(1, OPTIONS_PER_ROUND + 1)
            if option != correct_option
        ]
        plans.append(
            RoundPlan(
                round_number=round_number,
                correct_option=correct_option,
                wrong_option=rng.choice(wrong_choices),
                protocol_is_correct=round_number <= FIRST_GUARANTEED_CORRECT_ROUNDS
                or round_number not in wrong_rounds,
            )
        )
    return plans


class DebugRobot:
    def speak(self, text=None, *args, **kwargs):
        print(f"[DEBUG Misty speak] {text}")

    def display_image(self, fileName=None, *args, **kwargs):
        print(f"[DEBUG Misty display_image] {fileName}")

    def change_led(self, red=None, green=None, blue=None):
        print(f"[DEBUG Misty change_led] ({red}, {green}, {blue})")

    def move_head(self, pitch=None, roll=None, yaw=None, velocity=None, *args, **kwargs):
        print(f"[DEBUG Misty move_head] pitch={pitch} roll={roll} yaw={yaw} velocity={velocity}")

    def move_arms(self, *args, **kwargs):
        print(f"[DEBUG Misty move_arms] args={args} kwargs={kwargs}")


class MistyController:
    def __init__(self, robot, log_queue):
        self.robot = robot
        self.log_queue = log_queue

    def run_async(self, label, action):
        thread = threading.Thread(target=self._run_action, args=(label, action), daemon=True)
        thread.start()

    def _run_action(self, label, action):
        try:
            self.log_queue.put(f"Starting: {label}")
            action()
            self.log_queue.put(f"Finished: {label}")
        except Exception as exc:
            self.log_queue.put(f"ERROR during {label}: {exc}")

    def speak(self, text):
        self.robot.speak(text, None, None, None, True, "woz-controller")

    def intro(self):
        self.robot.change_led(0, 180, 80)
        self.robot.display_image("e_joy.jpg")
        self.robot.move_head(0, 0, 0, 85)
        self.speak(INTRO_SCRIPT)

    def guess(self, round_plan, is_correct):
        option = round_plan.correct_option if is_correct else round_plan.wrong_option
        phrase = FILLER_PHRASES[(round_plan.round_number - 1) % len(FILLER_PHRASES)]
        self.robot.change_led(0, 80, 255 if is_correct else 180)
        self.robot.display_image("e_thinking.jpg")
        self.speak(f"For round {round_plan.round_number}, {phrase.format(option=option)}")


class WizardOfOzApp(tk.Tk):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.round_plans = build_round_plan()
        self.current_round_index = 0
        self.intro_complete = False
        self.session_complete = False
        self.log_queue = controller.log_queue

        self.title("Misty Wizard of Oz Controller")
        self.geometry("780x620")
        self.minsize(680, 560)

        self._build_ui()
        self._update_round_ui()
        self._poll_log_queue()

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(4, weight=1)

        header = ttk.Frame(self, padding=16)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)

        ttk.Label(header, text="Current round", font=("Segoe UI", 12)).grid(row=0, column=0, sticky="w")

        round_controls = ttk.Frame(header)
        round_controls.grid(row=0, column=1, sticky="e")
        self.back_button = ttk.Button(round_controls, text="<", width=4, command=self.previous_round)
        self.back_button.grid(row=0, column=0, padx=(0, 8))
        self.round_label = ttk.Label(round_controls, text="", font=("Segoe UI", 20, "bold"), width=12, anchor="center")
        self.round_label.grid(row=0, column=1)
        self.forward_button = ttk.Button(round_controls, text=">", width=4, command=self.next_round)
        self.forward_button.grid(row=0, column=2, padx=(8, 0))

        self.protocol_label = ttk.Label(self, text="", padding=(16, 0), font=("Segoe UI", 10))
        self.protocol_label.grid(row=1, column=0, sticky="ew")

        action_frame = ttk.Frame(self, padding=16)
        action_frame.grid(row=2, column=0, sticky="ew")
        action_frame.columnconfigure((0, 1, 2), weight=1, uniform="actions")

        self.intro_button = ttk.Button(action_frame, text="Intro", command=self.play_intro)
        self.intro_button.grid(row=0, column=0, sticky="ew", padx=(0, 8), ipady=14)

        self.correct_button = ttk.Button(action_frame, text="Correct Guess", command=lambda: self.give_guess(True))
        self.correct_button.grid(row=0, column=1, sticky="ew", padx=8, ipady=14)

        self.wrong_button = ttk.Button(action_frame, text="Wrong Guess", command=lambda: self.give_guess(False))
        self.wrong_button.grid(row=0, column=2, sticky="ew", padx=(8, 0), ipady=14)

        utility_frame = ttk.Frame(self, padding=(16, 0, 16, 8))
        utility_frame.grid(row=3, column=0, sticky="ew")
        utility_frame.columnconfigure(0, weight=1)
        ttk.Button(utility_frame, text="Reset for New Subject", command=self.reset_subject).grid(row=0, column=1, sticky="e")

        self.log_text = scrolledtext.ScrolledText(self, height=12, state="disabled", wrap="word")
        self.log_text.grid(row=4, column=0, sticky="nsew", padx=16, pady=(0, 12))

        say_frame = ttk.Frame(self, padding=(16, 0, 16, 16))
        say_frame.grid(row=5, column=0, sticky="ew")
        say_frame.columnconfigure(0, weight=1)
        self.say_entry = ttk.Entry(say_frame)
        self.say_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.say_entry.bind("<Return>", lambda _event: self.say_now())
        ttk.Button(say_frame, text="Say Now", command=self.say_now).grid(row=0, column=1)

    def _current_plan(self):
        return self.round_plans[self.current_round_index]

    def _update_round_ui(self):
        plan = self._current_plan()
        outcome = "correct" if plan.protocol_is_correct else "wrong"
        protocol_option = plan.correct_option if plan.protocol_is_correct else plan.wrong_option
        self.round_label.config(text=f"{plan.round_number} / {TOTAL_ROUNDS}")
        self.protocol_label.config(
            text=(
                f"Seeded protocol for this round: Misty {outcome}, option {protocol_option}. "
                f"Correct option: {plan.correct_option}. Wrong option: {plan.wrong_option}."
            )
        )
        guesses_enabled = self.intro_complete and not self.session_complete
        guess_state = "normal" if guesses_enabled else "disabled"
        self.correct_button.config(state=guess_state)
        self.wrong_button.config(state=guess_state)
        self.intro_button.config(state="disabled" if self.intro_complete else "normal")
        self.back_button.config(state="normal" if self.current_round_index > 0 else "disabled")
        self.forward_button.config(state="normal" if self.current_round_index < TOTAL_ROUNDS - 1 else "disabled")

    def _append_log(self, message):
        self.log_text.config(state="normal")
        self.log_text.insert("end", f"{message}\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def _poll_log_queue(self):
        while True:
            try:
                self._append_log(self.log_queue.get_nowait())
            except queue.Empty:
                break
        self.after(100, self._poll_log_queue)

    def play_intro(self):
        self.intro_complete = True
        self._update_round_ui()
        self.controller.run_async("intro", self.controller.intro)

    def give_guess(self, is_correct):
        plan = self._current_plan()
        label = "correct guess" if is_correct else "wrong guess"
        self.controller.run_async(f"round {plan.round_number} {label}", lambda: self.controller.guess(plan, is_correct))
        self._append_log(
            f"Round {plan.round_number}: Misty gave a {label}; "
            f"option {plan.correct_option if is_correct else plan.wrong_option}."
        )
        if self.current_round_index < TOTAL_ROUNDS - 1:
            self.current_round_index += 1
        else:
            self.session_complete = True
            messagebox.showinfo("Final round complete", "All rounds have been completed.")
        self._update_round_ui()

    def previous_round(self):
        self.current_round_index = max(0, self.current_round_index - 1)
        self._update_round_ui()

    def next_round(self):
        self.current_round_index = min(TOTAL_ROUNDS - 1, self.current_round_index + 1)
        self._update_round_ui()

    def reset_subject(self):
        self.current_round_index = 0
        self.intro_complete = False
        self.session_complete = False
        self.say_entry.delete(0, "end")
        self._append_log("Reset for new subject.")
        self._update_round_ui()

    def say_now(self):
        text = self.say_entry.get().strip()
        if not text:
            return
        self.say_entry.delete(0, "end")
        self.controller.run_async("ad hoc speech", lambda: self.controller.speak(text))
        self._append_log(f"Ad hoc speech: {text}")


def parse_args():
    parser = argparse.ArgumentParser(description="Wizard of Oz controller for Misty Raven-style HRI study.")
    parser.add_argument("ip_address", nargs="?", help="Misty robot IP address. Omit when using --debug.")
    parser.add_argument("--debug", action="store_true", help="Run the GUI without connecting to a Misty robot.")
    return parser.parse_args()


def main():
    args = parse_args()
    if not args.debug and not args.ip_address:
        print("Usage: python Python-SDK/run.py <Misty IP address>")
        print("   or: python Python-SDK/run.py --debug")
        return 1

    robot = DebugRobot() if args.debug else Robot(args.ip_address)
    log_queue = queue.Queue()
    controller = MistyController(robot, log_queue)
    app = WizardOfOzApp(controller)
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
