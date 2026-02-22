"""
Progress indicators and status utilities
"""
import sys
import time
import threading
from typing import Optional


class ProgressSpinner:
    """
    Thread-safe progress spinner that runs in a background thread
    Shows the user that the app is still working
    """

    def __init__(self, message: str = "Working"):
        self.message = message
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.frames = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']
        self.current_frame = 0
        self.lock = threading.RLock()  # Reentrant lock for thread safety
        self._last_line_length = 0

    def start(self):
        """Start the spinner"""
        self.running = True
        self.thread = threading.Thread(target=self._spin, daemon=True)
        self.thread.start()

    def stop(self):
        """Stop the spinner and clear the line"""
        self.running = False
        if self.thread:
            self.thread.join()
        # Clear the line completely
        with self.lock:
            sys.stdout.write('\r' + ' ' * max(120, self._last_line_length) + '\r')
            sys.stdout.flush()

    def update_message(self, message: str):
        """Update the spinner message (thread-safe)"""
        with self.lock:
            self.message = message

    def _spin(self):
        """Spinner animation loop"""
        while self.running:
            with self.lock:
                frame = self.frames[self.current_frame % len(self.frames)]
                line = f'{frame} {self.message}...'

                # Clear previous line completely
                sys.stdout.write('\r' + ' ' * max(120, self._last_line_length) + '\r')

                # Write new spinner frame
                sys.stdout.write(line)
                sys.stdout.flush()

                self._last_line_length = len(line)
                self.current_frame += 1

            time.sleep(0.1)


class ProgressBar:
    """
    Thread-safe progress bar for operations with known total
    Safe to use with parallel operations (ThreadPoolExecutor, etc.)
    """

    def __init__(self, total: int, prefix: str = "Progress", width: int = 40):
        self.total = total
        self.current = 0
        self.prefix = prefix
        self.width = width
        self.lock = threading.RLock()  # Reentrant lock for nested calls
        self._last_line_length = 0  # Track how much to clear

    def update(self, current: int, suffix: str = ""):
        """Update progress bar (thread-safe)"""
        with self.lock:  # Ensure atomic update across threads
            self.current = current
            percent = (current / self.total) * 100 if self.total > 0 else 0
            filled = int(self.width * current / self.total) if self.total > 0 else 0
            bar = '█' * filled + '-' * (self.width - filled)

            # Build the complete progress line
            line = f'{self.prefix}: |{bar}| {percent:.1f}% ({current}/{self.total}) {suffix}'

            # Clear the entire previous line (use max of 120 chars to be safe)
            sys.stdout.write('\r' + ' ' * max(120, self._last_line_length) + '\r')

            # Write the new progress line
            sys.stdout.write(line)
            sys.stdout.flush()

            # Remember length for next clear
            self._last_line_length = len(line)

    def increment(self, suffix: str = ""):
        """Increment progress by 1 (thread-safe)"""
        with self.lock:
            self.update(self.current + 1, suffix)

    def finish(self, message: str = "Complete"):
        """Finish progress bar and move to new line"""
        with self.lock:
            self.update(self.total, message)
            print()  # New line after completion
            sys.stdout.flush()  # Ensure newline is written immediately


class StatusPrinter:
    """
    Prints status messages with consistent formatting
    """

    @staticmethod
    def section(title: str):
        """Print a section header"""
        print(f"\n{'=' * 70}")
        print(f"  {title}")
        print('=' * 70)

    @staticmethod
    def subsection(title: str):
        """Print a subsection header"""
        print(f"\n{'-' * 70}")
        print(f"  {title}")
        print('-' * 70)

    @staticmethod
    def step(step_num: int, title: str):
        """Print a step"""
        print(f"\n[Step {step_num}] {title}")

    @staticmethod
    def info(message: str):
        """Print info message"""
        print(f"  ℹ {message}")

    @staticmethod
    def success(message: str):
        """Print success message"""
        print(f"  ✓ {message}")

    @staticmethod
    def warning(message: str):
        """Print warning message"""
        print(f"  ⚠ {message}")

    @staticmethod
    def error(message: str):
        """Print error message"""
        print(f"  ✗ {message}")

    @staticmethod
    def result(label: str, value):
        """Print a result"""
        print(f"  → {label}: {value}")


if __name__ == "__main__":
    # Demo the utilities
    status = StatusPrinter()

    status.section("Demo Progress Indicators")

    # Demo spinner
    spinner = ProgressSpinner("Loading data")
    spinner.start()
    time.sleep(2)
    spinner.update_message("Processing")
    time.sleep(2)
    spinner.stop()
    status.success("Spinner complete")

    # Demo progress bar
    print()
    bar = ProgressBar(total=10, prefix="Processing stocks")
    for i in range(10):
        time.sleep(0.3)
        bar.increment(suffix=f"Stock {i+1}")
    bar.finish("Done!")

    status.success("All demos complete")
