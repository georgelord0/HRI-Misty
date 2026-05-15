#!/usr/bin/env python3

import argparse
import os
import queue
import random
import secrets
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

try:
    from mistyPy.Events import Events
except ModuleNotFoundError:
    Events = None

try:
    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtWidgets import (
        QApplication,
        QFrame,
        QGridLayout,
        QHeaderView,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMessageBox,
        QPlainTextEdit,
        QPushButton,
        QSizePolicy,
        QTableWidget,
        QTableWidgetItem,
        QTextEdit,
        QVBoxLayout,
        QWidget,
    )
except ModuleNotFoundError as exc:
    if exc.name != "PySide6":
        raise
    print("The GUI requires PySide6.")
    print("From the repo root, run:")
    print("  source .venv/bin/activate")
    print("  pip install -r requirements.txt")
    print("  python Python-SDK/run.py --debug")
    raise SystemExit(1)

SDK_DIR = os.path.abspath(os.path.dirname(__file__))
if SDK_DIR not in sys.path:
    sys.path.insert(0, SDK_DIR)


TOTAL_ROUNDS = 8
OPTIONS_PER_ROUND = 6
FIRST_GUARANTEED_CORRECT_ROUNDS = 3
WRONG_GUESSES_AFTER_EASY_ROUNDS = 3

# Values are the option numbers Misty says when the protocol calls for a
# correct suggestion. Update these if the study materials' answer key changes.
CORRECT_OPTIONS = [3, 5, 4, 5, 1, 6, 1, 2]

FACE_JOY = "e_Joy.jpg"
FACE_THINKING = "e_Amazement.jpg"
FACE_DEFAULT = "e_DefaultContent.jpg"

# Misty's default speech/audio volume, 0-100. Lower this for quieter study rooms.
MISTY_VOLUME = 50

# Wait this long after the final handoff line before blanking Misty's face and LED.
FINAL_SHUTDOWN_DELAY_SECONDS = 8.0


@dataclass(frozen=True)
class RoundPlan:
    round_number: int
    correct_option: int
    wrong_option: int
    protocol_is_correct: bool

    @property
    def misty_choice(self):
        return self.correct_option if self.protocol_is_correct else self.wrong_option

    @property
    def outcome_label(self):
        return "correct" if self.protocol_is_correct else "incorrect"


@dataclass(frozen=True)
class ProtocolStep:
    phase: str
    title: str
    misty_line: str
    before_speech: Optional[Callable[[], None]] = None
    after_speech: Optional[Callable[[], None]] = None
    operator_note: str = ""


def build_round_plan(seed=None):
    rng = random.Random(seed)
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
        print(
            "[DEBUG Misty move_head] "
            f"pitch={pitch} roll={roll} yaw={yaw} velocity={velocity}"
        )

    def move_arms(
        self,
        leftArmPosition=None,
        rightArmPosition=None,
        leftArmVelocity=None,
        rightArmVelocity=None,
        duration=None,
        units=None,
    ):
        print(
            "[DEBUG Misty move_arms] "
            f"left={leftArmPosition} right={rightArmPosition} "
            f"left_velocity={leftArmVelocity} right_velocity={rightArmVelocity} "
            f"duration={duration} units={units}"
        )

    def drive_time(self, linearVelocity=None, angularVelocity=None, timeMs=None, degree=None):
        print(
            "[DEBUG Misty drive_time] "
            f"linear={linearVelocity} angular={angularVelocity} "
            f"timeMs={timeMs} degree={degree}"
        )

    def stop(self, hold=None):
        print(f"[DEBUG Misty stop] hold={hold}")

    def stop_speaking(self):
        print("[DEBUG Misty stop_speaking]")

    def display_text(self, text=None, layer=None):
        print(f"[DEBUG Misty display_text] text={text!r} layer={layer}")

    def set_image_display_settings(self, *args, **kwargs):
        print(f"[DEBUG Misty set_image_display_settings] {kwargs}")

    def set_text_display_settings(self, *args, **kwargs):
        print(f"[DEBUG Misty set_text_display_settings] {kwargs}")

    def register_event(self, event_name=None, event_type=None, callback_function=None, keep_alive=None):
        print(f"[DEBUG Misty register_event] {event_name}")


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

    def _log_response(self, command_name, response):
        if response is None or not hasattr(response, "status_code"):
            return
        if response.status_code >= 400:
            body = response.text[:250] if hasattr(response, "text") else ""
            raise RuntimeError(f"{command_name} failed with HTTP {response.status_code}: {body}")

    def set_volume(self, volume):
        command = getattr(self.robot, "set_default_volume", None)
        if command is None:
            self.log_queue.put("Misty SDK does not expose set_default_volume; volume was not changed.")
            return
        response = command(volume=volume)
        self._log_response("set_default_volume", response)

    def speak(self, text):
        response = self.robot.speak(
            text=text,
            pitch=None,
            speechRate=None,
            voice=None,
            flush=True,
            utteranceId=f"woz-{int(time.time() * 1000)}",
            language=None,
        )
        self._log_response("speak", response)

    def display_image(self, filename):
        response = self.robot.display_image(fileName=filename, alpha=1)
        self._log_response("display_image", response)

    def display_text(self, text, layer="woz-blank"):
        command = getattr(self.robot, "display_text", None)
        if command is None:
            self.log_queue.put("Misty SDK does not expose display_text; face was not blanked.")
            return
        response = command(text=text, layer=layer)
        self._log_response("display_text", response)

    def hide_image_layer(self, layer=None):
        command = getattr(self.robot, "set_image_display_settings", None)
        if command is None:
            return
        response = command(layer=layer, visible=False)
        self._log_response("set_image_display_settings", response)

    def configure_blank_text_layer(self, layer="woz-blank"):
        command = getattr(self.robot, "set_text_display_settings", None)
        if command is None:
            return
        response = command(
            layer=layer,
            size=1,
            red=0,
            green=0,
            blue=0,
            placeOnTop=True,
            width=480,
            height=272,
        )
        self._log_response("set_text_display_settings", response)

    def change_led(self, red, green, blue):
        response = self.robot.change_led(red=red, green=green, blue=blue)
        self._log_response("change_led", response)

    def move_head(self, pitch=0, roll=0, yaw=0, velocity=85):
        response = self.robot.move_head(
            pitch=pitch,
            roll=roll,
            yaw=yaw,
            velocity=velocity,
            duration=None,
            units="degrees",
        )
        self._log_response("move_head", response)

    def move_arms(self, left=80, right=80, velocity=55):
        response = self.robot.move_arms(
            leftArmPosition=left,
            rightArmPosition=right,
            leftArmVelocity=velocity,
            rightArmVelocity=velocity,
            duration=None,
            units="degrees",
        )
        self._log_response("move_arms", response)

    def small_body_motion(self):
        response = self.robot.drive_time(
            linearVelocity=0,
            angularVelocity=10,
            timeMs=350,
            degree=None,
        )
        self._log_response("drive_time", response)
        time.sleep(0.4)
        response = self.robot.drive_time(
            linearVelocity=0,
            angularVelocity=-10,
            timeMs=350,
            degree=None,
        )
        self._log_response("drive_time", response)

    def warmup_greeting_behavior(self):
        self.display_image(FACE_JOY)
        self.change_led(0, 180, 80)
        self.move_head(pitch=0, roll=0, yaw=0, velocity=100)
        self.move_arms(left=-40, right=40, velocity=65)
        time.sleep(0.5)
        self.move_arms(left=40, right=-40, velocity=65)
        time.sleep(0.5)
        self.move_arms(left=-40, right=40, velocity=65)
        time.sleep(0.5)
        self.move_arms(left=80, right=80, velocity=60)
        self.move_head(pitch=0, roll=0, yaw=0, velocity=100)
        time.sleep(0.5)
        self.change_led(70, 120, 255)
        self.move_head(pitch=-5, roll=0, yaw=25, velocity=100)
        time.sleep(0.5)
        self.move_head(pitch=-5, roll=0, yaw=-25, velocity=100)
        time.sleep(0.5)
        self.move_head(pitch=0, roll=0, yaw=0, velocity=100)

    def thinking_behavior(self):
        self.display_image(FACE_THINKING)
        self.change_led(40, 120, 255)

    def answer_behavior(self):
        self.display_image(FACE_THINKING)
        self.change_led(40, 120, 255)
        self.move_head(pitch=40, roll=0, yaw=0, velocity=100)
        time.sleep(2)
        self.move_head(pitch=0, roll=0, yaw=0, velocity=100)
        time.sleep(1)

    def wait_for_touch_behavior(self):
        self.change_led(255, 180, 0)

    def listening_behavior(self):
        self.display_image(FACE_JOY)
        self.change_led(0, 180, 80)
        self.move_head(pitch=0, roll=0, yaw=0, velocity=100)

    def _nod(self):
        self.move_head(pitch=26, roll=0, yaw=0, velocity=100)
        time.sleep(0.35)
        self.move_head(pitch=0, roll=0, yaw=0, velocity=100)
        time.sleep(0.35)
        self.move_head(pitch=26, roll=0, yaw=0, velocity=100)
        time.sleep(0.35)
        self.move_head(pitch=0, roll=0, yaw=0, velocity=100)

    def acknowledge_behavior(self):
        self.display_image(FACE_JOY)
        self.change_led(0, 180, 80)
        self._nod()

    def explain_board_behavior(self):
        self.display_image(FACE_JOY)
        self.change_led(0, 180, 80)
        self.move_head(pitch=40, roll=0, yaw=0, velocity=100)
        self.move_arms(left=0, right=0, velocity=100)
        time.sleep(1.6)
        self.move_head(pitch=0, roll=0, yaw=0, velocity=100)
        self.move_arms(left=80, right=80, velocity=100)
        time.sleep(0.9)

    def final_behavior(self):
        self.display_image(FACE_DEFAULT)
        self.change_led(90, 90, 255)
        self._nod()
        self.move_arms(left=80, right=80, velocity=100)

    def finish_shutdown_behavior(self):
        time.sleep(FINAL_SHUTDOWN_DELAY_SECONDS)
        self.move_head(pitch=0, roll=0, yaw=0, velocity=100)
        self.move_arms(left=80, right=80, velocity=100)
        self.change_led(0, 0, 0)
        self.hide_image_layer()
        self.configure_blank_text_layer()
        self.display_text(" ", layer="woz-blank")

    def register_head_front_touch(self, on_touch):
        if Events is None:
            self.log_queue.put("mistyPy.Events not available; head touch not registered.")
            return

        def callback(data):
            if data.get("message", {}).get("sensorPosition") == "HeadFront":
                on_touch()

        self.robot.register_event(
            event_name="participant_head_touch",
            event_type=Events.TouchSensor,
            callback_function=callback,
            keep_alive=True,
        )

    def stop_robot(self):
        for command_name in ("stop_speaking", "stop"):
            command = getattr(self.robot, command_name, None)
            if command is None:
                continue
            if command_name == "stop":
                response = command(hold=True)
            else:
                response = command()
            self._log_response(command_name, response)

    def run_step(self, step):
        if step.before_speech is not None:
            step.before_speech()
        if step.misty_line.strip():
            self.speak(step.misty_line)
        if step.after_speech is not None:
            step.after_speech()


def make_protocol_steps(controller, round_plans):
    steps = [
        ProtocolStep(
            phase="Phase 6: Misty Warm-Up",
            title="Greeting and warm-up behavior",
            misty_line="Hello! I'm Misty. It's nice to meet you today.",
            before_speech=controller.warmup_greeting_behavior,
            operator_note="Trigger after RA1 leaves the room.",
        ),
        ProtocolStep(
            phase="Phase 6: Misty Warm-Up",
            title="Ask participant name",
            misty_line="Before we start, I'd like to ask you a couple of questions. What's your name?",
            before_speech=controller.listening_behavior,
            operator_note="Wait for the participant to answer, then trigger the next step regardless of content.",
        ),
        ProtocolStep(
            phase="Phase 6: Misty Warm-Up",
            title="Ask prior robot interaction",
            misty_line="Nice to meet you. Have you often interacted with a robot before?",
            before_speech=controller.listening_behavior,
            operator_note="Wait for the participant to answer, then trigger the game explanation.",
        ),
        ProtocolStep(
            phase="Phase 6: Misty Warm-Up",
            title="Explain task: game setup",
            misty_line=(
                "Sounds good. Now, we're going to play a pattern-matching game together. "
                "I'll explain the rules, and then we'll play eight rounds."
            ),
            before_speech=controller.listening_behavior,
            operator_note="Trigger the board gesture next.",
        ),
        ProtocolStep(
            phase="Phase 6: Misty Warm-Up",
            title="Explain task: game materials",
            misty_line=(
                "For each round, you'll see a pattern with one piece missing. "
            ),
            before_speech=controller.listening_behavior,
            operator_note="Trigger the next step after the board explanation gesture is complete."
        ),
        ProtocolStep(
            phase="Phase 6: Misty Warm-Up",
            title="Explain task: rules and readiness - Part 2",
            misty_line=(
                "Your job is to pick the piece that best completes the pattern from the options shown. "
                "I'll give you my suggestion first, and then you'll make the final decision. "
                "When my light turns yellow, that means I'm waiting for you. "
                "Once you've decided, please use the pen on the table to circle your answer on the sheet, then gently touch my forehead to continue. "
                "Ready to start?"
            ),
            before_speech=controller.explain_board_behavior,
            operator_note="Wait for the participant to confirm readiness.",
        ),
    ]

    for plan in round_plans:
        prefix = "" if plan.round_number == 1 else "Thank you. "
        steps.extend(
            [
                ProtocolStep(
                    phase="Phase 7: Pattern-Matching Task",
                    title=f"Round {plan.round_number}: turn to question",
                    misty_line=(
                        f"{prefix}Let's start Round {plan.round_number}. "
                        f"Please turn to Question {plan.round_number}."
                    ),
                    before_speech=(
                        controller.listening_behavior
                        if plan.round_number == 1
                        else controller.acknowledge_behavior
                    ),
                    operator_note="Wait until the participant has found the question.",
                ),
                ProtocolStep(
                    phase="Phase 7: Pattern-Matching Task",
                    title=f"Round {plan.round_number}: Misty suggestion",
                    misty_line=(
                        f"For this question, question {plan.round_number}, "
                        f"my choice is {plan.misty_choice}. "
                        f"Take a moment to think, circle your final answer on the sheet, "
                        f"then touch my forehead to continue."
                    ),
                    before_speech=controller.answer_behavior,
                    after_speech=controller.wait_for_touch_behavior,
                    operator_note=(
                        f"Protocol: Misty is {plan.outcome_label}. "
                        f"Correct option is {plan.correct_option}; Misty says {plan.misty_choice}."
                    ),
                ),
            ]
        )

    steps.append(
        ProtocolStep(
            phase="Phase 7: Pattern-Matching Task",
            title="Final survey handoff",
            misty_line=(
                "That was our last round. Thank you for playing with me. "
                "There's one more survey for you to complete. Please wait for assistance."
            ),
            before_speech=controller.final_behavior,
            after_speech=controller.finish_shutdown_behavior,
            operator_note=(
                "Trigger after the participant selects their answer for Round 8. Misty will "
                "blank the face and turn off the LED after the final line."
            ),
        )
    )
    return steps


class WizardOfOzApp(QMainWindow):
    def __init__(self, controller, fixed_seed=None):
        super().__init__()
        self.controller = controller
        self.fixed_seed = fixed_seed
        self.log_queue = controller.log_queue
        self.step_index = 0
        self.subject_number = 0
        self.log_timer = QTimer(self)
        self.log_timer.timeout.connect(self._poll_log_queue)

        self.setWindowTitle("Misty Wizard of Oz Controller")
        self.resize(1120, 900)
        self.setMinimumSize(920, 800)

        self.head_touch_pending = False
        self._new_subject_plan()
        self._build_ui()
        self._update_ui()
        self.log_timer.start(100)
        self.controller.register_head_front_touch(self._on_head_front_touch)

    def _new_subject_plan(self):
        self.subject_number += 1
        if self.fixed_seed is None:
            self.session_seed = secrets.randbelow(2**32)
        else:
            self.session_seed = self.fixed_seed + self.subject_number - 1
        self.round_plans = build_round_plan(self.session_seed)
        self.steps = make_protocol_steps(self.controller, self.round_plans)
        self.step_index = 0

    def _build_ui(self):
        self.setStyleSheet(
            """
            QMainWindow, QWidget {
                background: #f4f6f8;
                color: #102033;
                font-family: -apple-system, BlinkMacSystemFont, "Helvetica Neue", Arial, sans-serif;
            }
            #Header, #Footer {
                background: #ffffff;
                border: 0;
            }
            #Panel {
                background: #ffffff;
                border: 1px solid #d1d9e6;
                border-radius: 8px;
            }
            QLabel#Phase {
                color: #102033;
                font-size: 18px;
                font-weight: 700;
            }
            QLabel#Progress {
                color: #46576b;
                font-size: 15px;
                font-weight: 700;
            }
            QLabel#Title {
                color: #102033;
                font-size: 30px;
                font-weight: 800;
            }
            QLabel#PanelTitle {
                color: #102033;
                font-size: 18px;
                font-weight: 700;
            }
            QLabel#Note, QLabel#Seed {
                color: #45566b;
                font-size: 14px;
            }
            QLabel#PanelHeader {
                color: #6e7c8e;
                font-size: 13px;
                font-weight: 500;
                padding: 2px 4px;
            }
            QLabel#CurrentHeader {
                color: #2e7d32;
                font-size: 14px;
                font-weight: 700;
                padding: 2px 4px;
            }
            QTextEdit#PanelBody {
                background: #f9fbfd;
                color: #46576b;
                border: 1px solid #d7e0ec;
                border-radius: 7px;
                font-size: 14px;
                padding: 8px;
            }
            QTextEdit#CurrentBody {
                background: #f0fbf3;
                color: #1b5e20;
                border: 2px solid #2e7d32;
                border-radius: 7px;
                font-size: 15px;
                padding: 8px;
                font-weight: 600;
            }
            QTextEdit, QPlainTextEdit, QLineEdit {
                background: #ffffff;
                border: 1px solid #b7c4d4;
                border-radius: 7px;
                color: #102033;
                selection-background-color: #d8e9ff;
            }
            QTextEdit {
                font-size: 21px;
                padding: 10px;
            }
            QPlainTextEdit {
                background: #f9fbfd;
                font-size: 14px;
                padding: 8px;
            }
            QLineEdit {
                font-size: 15px;
                padding: 8px 10px;
            }
            QPushButton {
                background: #52616f;
                color: #ffffff;
                border: none;
                border-radius: 7px;
                min-height: 42px;
                padding: 8px 14px;
                font-size: 14px;
                font-weight: 700;
            }
            QPushButton:hover {
                background: #3f4b56;
            }
            QPushButton:disabled {
                background: #b5bec8;
                color: #eef2f6;
            }
            QPushButton#Primary {
                background: #0b66d8;
                min-height: 52px;
                font-size: 17px;
            }
            QPushButton#Primary:hover {
                background: #0954b2;
            }
            QPushButton#Stop {
                background: #b42318;
            }
            QPushButton#Stop:hover {
                background: #8f1d15;
            }
            QTableWidget {
                background: #ffffff;
                alternate-background-color: #f7f9fc;
                color: #102033;
                border: 0;
                gridline-color: #e1e7ef;
                font-size: 13px;
            }
            QHeaderView::section {
                background: #eef3f8;
                color: #45566b;
                border: 0;
                border-bottom: 1px solid #d1d9e6;
                padding: 7px;
                font-weight: 700;
            }
            """
        )

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QFrame()
        header.setObjectName("Header")
        header_layout = QGridLayout(header)
        header_layout.setContentsMargins(20, 16, 20, 14)
        header_layout.setHorizontalSpacing(16)

        self.phase_label = QLabel()
        self.phase_label.setObjectName("Phase")
        header_layout.addWidget(self.phase_label, 0, 0, alignment=Qt.AlignLeft)

        self.progress_label = QLabel()
        self.progress_label.setObjectName("Progress")
        self.progress_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        header_layout.addWidget(self.progress_label, 0, 1)

        self.title_label = QLabel()
        self.title_label.setObjectName("Title")
        self.title_label.setWordWrap(True)
        header_layout.addWidget(self.title_label, 1, 0, 1, 2)
        header_layout.setColumnStretch(1, 1)
        root.addWidget(header)

        main = QWidget()
        main_layout = QVBoxLayout(main)
        main_layout.setContentsMargins(20, 18, 20, 16)
        main_layout.setSpacing(12)

        three_col = QWidget()
        three_col_layout = QHBoxLayout(three_col)
        three_col_layout.setContentsMargins(0, 0, 0, 0)
        three_col_layout.setSpacing(10)

        prev_panel = QWidget()
        prev_layout = QVBoxLayout(prev_panel)
        prev_layout.setContentsMargins(0, 0, 0, 0)
        prev_layout.setSpacing(4)
        self.prev_header = QLabel()
        self.prev_header.setObjectName("PanelHeader")
        self.prev_header.setWordWrap(True)
        self.prev_text = QTextEdit()
        self.prev_text.setReadOnly(True)
        self.prev_text.setObjectName("PanelBody")
        self.prev_text.setFixedHeight(140)
        prev_layout.addWidget(self.prev_header)
        prev_layout.addWidget(self.prev_text)
        three_col_layout.addWidget(prev_panel, 1)

        curr_panel = QWidget()
        curr_layout = QVBoxLayout(curr_panel)
        curr_layout.setContentsMargins(0, 0, 0, 0)
        curr_layout.setSpacing(4)
        self.curr_header = QLabel()
        self.curr_header.setObjectName("CurrentHeader")
        self.curr_header.setWordWrap(True)
        self.curr_text = QTextEdit()
        self.curr_text.setReadOnly(True)
        self.curr_text.setObjectName("CurrentBody")
        self.curr_text.setFixedHeight(140)
        curr_layout.addWidget(self.curr_header)
        curr_layout.addWidget(self.curr_text)
        three_col_layout.addWidget(curr_panel, 1)

        next_panel = QWidget()
        next_layout = QVBoxLayout(next_panel)
        next_layout.setContentsMargins(0, 0, 0, 0)
        next_layout.setSpacing(4)
        self.next_header = QLabel()
        self.next_header.setObjectName("PanelHeader")
        self.next_header.setWordWrap(True)
        self.next_text = QTextEdit()
        self.next_text.setReadOnly(True)
        self.next_text.setObjectName("PanelBody")
        self.next_text.setFixedHeight(140)
        next_layout.addWidget(self.next_header)
        next_layout.addWidget(self.next_text)
        three_col_layout.addWidget(next_panel, 1)

        main_layout.addWidget(three_col)

        self.note_label = QLabel()
        self.note_label.setObjectName("Note")
        self.note_label.setWordWrap(True)
        main_layout.addWidget(self.note_label)

        controls = QHBoxLayout()
        controls.setSpacing(12)
        self.next_button = QPushButton("Trigger Next Protocol Step")
        self.next_button.setObjectName("Primary")
        self.next_button.clicked.connect(self.trigger_next_step)
        controls.addWidget(self.next_button, stretch=4)

        self.replay_button = QPushButton("Replay Last")
        self.replay_button.clicked.connect(self.replay_last_step)
        controls.addWidget(self.replay_button, stretch=1)

        self.back_button = QPushButton("Back One")
        self.back_button.clicked.connect(self.back_one_step)
        controls.addWidget(self.back_button, stretch=1)

        self.stop_button = QPushButton("Stop Misty")
        self.stop_button.setObjectName("Stop")
        self.stop_button.clicked.connect(self.stop_misty)
        controls.addWidget(self.stop_button, stretch=1)
        main_layout.addLayout(controls)
        root.addWidget(main)

        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(20, 0, 20, 16)
        body_layout.setSpacing(14)

        schedule_frame = self._panel()
        schedule_layout = QVBoxLayout(schedule_frame)
        schedule_layout.setContentsMargins(14, 14, 14, 14)
        schedule_layout.setSpacing(8)
        schedule_title = QLabel("Round Schedule")
        schedule_title.setObjectName("PanelTitle")
        schedule_layout.addWidget(schedule_title)
        self.seed_label = QLabel()
        self.seed_label.setObjectName("Seed")
        self.seed_label.setWordWrap(True)
        schedule_layout.addWidget(self.seed_label)

        self.schedule_table = QTableWidget(8, 5)
        self.schedule_table.setHorizontalHeaderLabels(["Round", "Misty", "Says", "Correct", "Wrong"])
        self.schedule_table.setAlternatingRowColors(True)
        self.schedule_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.schedule_table.setSelectionMode(QTableWidget.NoSelection)
        self.schedule_table.verticalHeader().setVisible(False)
        self.schedule_table.horizontalHeader().setStretchLastSection(True)
        for column in range(5):
            self.schedule_table.horizontalHeader().setSectionResizeMode(column, QHeaderView.Stretch)
        schedule_layout.addWidget(self.schedule_table, stretch=1)
        body_layout.addWidget(schedule_frame, stretch=3)
        schedule_frame.setMinimumWidth(420)

        log_frame = self._panel()
        log_layout = QVBoxLayout(log_frame)
        log_layout.setContentsMargins(14, 14, 14, 14)
        log_layout.setSpacing(8)
        log_title = QLabel("Event Log")
        log_title.setObjectName("PanelTitle")
        log_layout.addWidget(log_title)
        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        log_layout.addWidget(self.log_text, stretch=1)
        body_layout.addWidget(log_frame, stretch=2)
        root.addWidget(body, stretch=1)

        footer = QFrame()
        footer.setObjectName("Footer")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(20, 14, 20, 14)
        footer_layout.setSpacing(12)
        self.say_entry = QLineEdit()
        self.say_entry.setPlaceholderText("Type optional ad hoc speech for Misty")
        self.say_entry.returnPressed.connect(self.say_now)
        footer_layout.addWidget(self.say_entry, stretch=1)
        say_button = QPushButton("Say Now")
        say_button.setObjectName("Primary")
        say_button.clicked.connect(self.say_now)
        footer_layout.addWidget(say_button)
        reset_button = QPushButton("Reset for New Subject")
        reset_button.clicked.connect(self.reset_subject)
        footer_layout.addWidget(reset_button)
        root.addWidget(footer)

    def _panel(self):
        panel = QFrame()
        panel.setObjectName("Panel")
        panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        return panel

    def _current_step(self):
        if self.step_index >= len(self.steps):
            return None
        return self.steps[self.step_index]

    def _update_schedule(self):
        for row, plan in enumerate(self.round_plans):
            values = (
                str(plan.round_number),
                plan.outcome_label,
                str(plan.misty_choice),
                str(plan.correct_option),
                str(plan.wrong_option),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setTextAlignment(Qt.AlignCenter)
                if column == 1:
                    if plan.protocol_is_correct:
                        item.setBackground(Qt.GlobalColor.transparent)
                        item.setForeground(Qt.darkGreen)
                    else:
                        item.setForeground(Qt.darkYellow)
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                self.schedule_table.setItem(row, column, item)
        self.seed_label.setText(f"Subject {self.subject_number} randomization seed: {self.session_seed}")

    def _update_ui(self):
        step = self._current_step()
        total_steps = len(self.steps)
        self.progress_label.setText(f"Step {min(self.step_index + 1, total_steps)} of {total_steps}")

        if step is None:
            self.phase_label.setText("Session Complete")
            self.title_label.setText("All scripted steps are complete")
            self.note_label.setText("RA1 may re-enter and assist with the final survey.")
            self.next_button.setEnabled(False)
        else:
            self.phase_label.setText(step.phase)
            self.title_label.setText(step.title)
            self.note_label.setText(step.operator_note)
            self.next_button.setEnabled(True)

        prev_step = self.steps[self.step_index - 2] if self.step_index >= 2 else None
        curr_step = self.steps[self.step_index - 1] if self.step_index >= 1 else None
        next_step = self.steps[self.step_index] if self.step_index < total_steps else None

        def _format_speech(s):
            if s is None:
                return ""
            return s.misty_line if s.misty_line.strip() else "[Gesture only: no speech]"

        self.prev_header.setText(f"Previous · {prev_step.title}" if prev_step else "Previous · (none)")
        self.prev_text.setPlainText(_format_speech(prev_step))
        self.curr_header.setText(f"Now Reading · {curr_step.title}" if curr_step else "Now Reading · (not started)")
        self.curr_text.setPlainText(_format_speech(curr_step))
        self.next_header.setText(f"Next · {next_step.title}" if next_step else "Next · (session complete)")
        self.next_text.setPlainText(_format_speech(next_step))

        self.replay_button.setEnabled(self.step_index > 0)
        self.back_button.setEnabled(self.step_index > 0)
        self._update_schedule()

    def _append_log(self, message):
        self.log_text.appendPlainText(message)
        scrollbar = self.log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _poll_log_queue(self):
        while True:
            try:
                self._append_log(self.log_queue.get_nowait())
            except queue.Empty:
                break
        if self.head_touch_pending:
            self.head_touch_pending = False
            if self._is_waiting_for_answer():
                self._append_log("Participant touched Misty's HeadFront → advancing step.")
                self.trigger_next_step()
            else:
                self._append_log("HeadFront touched but ignored (not at answering step).")

    def _is_waiting_for_answer(self):
        if not 0 < self.step_index <= len(self.steps):
            return False
        return self.steps[self.step_index - 1].title.endswith("Misty suggestion")

    def _on_head_front_touch(self):
        if not self._is_waiting_for_answer():
            self.head_touch_pending = True
            return
        threading.Thread(target=self._flash_then_advance, daemon=True).start()

    def _flash_then_advance(self):
        self.controller.change_led(255, 255, 255)
        time.sleep(1.5)
        self.head_touch_pending = True

    def closeEvent(self, event):
        self.log_timer.stop()
        super().closeEvent(event)

    def _run_protocol_step(self, step, replay=False):
        prefix = "replay" if replay else "step"
        label = f"{prefix}: {step.phase} - {step.title}"
        self.controller.run_async(label, lambda: self.controller.run_step(step))
        self._append_log(f"Triggered {label}")

    def trigger_next_step(self):
        step = self._current_step()
        if step is None:
            return
        self._run_protocol_step(step)
        self.step_index += 1
        if self.step_index == len(self.steps):
            QMessageBox.information(self, "Protocol complete", "All scripted Misty steps have been triggered.")
        self._update_ui()

    def replay_last_step(self):
        if self.step_index <= 0:
            return
        step = self.steps[self.step_index - 1]
        self._run_protocol_step(step, replay=True)

    def back_one_step(self):
        if self.step_index <= 0:
            return
        self.step_index -= 1
        self._append_log("Moved back one protocol step. The backed-up step has not been re-triggered yet.")
        self._update_ui()

    def stop_misty(self):
        self.controller.run_async("stop Misty", self.controller.stop_robot)
        self._append_log("Stop command sent.")

    def reset_subject(self):
        self._new_subject_plan()
        self.say_entry.clear()
        self._append_log("Reset for new subject and generated a new round schedule.")
        self._update_ui()

    def say_now(self):
        text = self.say_entry.text().strip()
        if not text:
            return
        self.say_entry.clear()
        self.controller.run_async("ad hoc speech", lambda: self.controller.speak(text))
        self._append_log(f"Ad hoc speech: {text}")


def parse_args():
    parser = argparse.ArgumentParser(description="Wizard of Oz controller for Misty HRI study.")
    parser.add_argument("ip_address", nargs="?", help="Misty robot IP address. Omit when using --debug.")
    parser.add_argument("--debug", action="store_true", help="Run the GUI without connecting to a Misty robot.")
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help=(
            "Optional base randomization seed. If omitted, each new subject gets a random seed. "
            "When supplied, resets use seed, seed+1, seed+2, and so on."
        ),
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if not args.debug and not args.ip_address:
        print("Usage: python3 Python-SDK/run.py <Misty IP address>")
        print("   or: python3 Python-SDK/run.py --debug")
        return 1

    if args.debug:
        robot = DebugRobot()
    else:
        try:
            from mistyPy.Robot import Robot
        except ModuleNotFoundError as exc:
            print(f"Missing Python package: {exc.name}")
            print("From the repo root, run:")
            print("  python3 -m venv .venv")
            print("  source .venv/bin/activate")
            print("  pip install -r requirements.txt")
            return 1
        robot = Robot(args.ip_address)

    qt_app = QApplication(sys.argv)
    log_queue = queue.Queue()
    controller = MistyController(robot, log_queue)
    controller.run_async(f"set volume to {MISTY_VOLUME}", lambda: controller.set_volume(MISTY_VOLUME))
    window = WizardOfOzApp(controller, fixed_seed=args.seed)
    window.show()
    return qt_app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
