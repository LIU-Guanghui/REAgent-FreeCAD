"""The thread-safe pause/supplement boundary; the worker owns all other actions."""

from threading import Lock


class Control:
    def __init__(self):
        self.lock = Lock()
        self.generation = 0
        self.paused = False
        self.supplements = []
        self.active = False

    def pause(self):
        with self.lock:
            self.paused = True
            self.generation += 1

    def start_task(self):
        with self.lock:
            self.generation = 0
            self.paused = False
            self.supplements = []

    def advance_generation(self):
        with self.lock:
            self.generation += 1
            return self.generation

    def supplement(self, text):
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Supplement must be non-empty text")
        with self.lock:
            if not self.active:
                raise RuntimeError("There is no current task")
            self.generation += 1
            self.supplements.append((self.generation, text))
            self.paused = True

    def capture(self, *, resume=False):
        with self.lock:
            if resume:
                self.paused = False
            return self.generation, self.paused

    def cancelled(self, generation):
        with self.lock:
            return self.paused or self.generation != generation
