- [x] Inspect existing input automation functions in actions/computer_control.py (pyautogui.scroll/hotkey/press).
- [x] Implement Windows active-window focus pre-flight check helpers in computer_control.py.
- [x] Wrap pyautogui.scroll() and pyautogui.hotkey() macros with focus checks and micro-sleeps between down/up sequences.
- [x] Add micro-sleep (time.sleep(0.02)) between individual event down/up sequences in affected helpers.
- [x] Ensure changes are minimal and do not break macOS/Linux behavior.
- [x] Run a quick import/lint check by executing a syntax compile for computer_control.py.

