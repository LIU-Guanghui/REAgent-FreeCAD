"""The thread-safe pause/supplement boundary; the worker owns all other actions."""

from threading import Lock


class Control:
    def __init__(self):
        self.lock = Lock()
        self.generation = 0
        self.paused = False
        self.supplements = []
        self.active = False
        self.last_manual_pause = -1
        self.last_supplement = -1

    def pause(self):
        with self.lock:
            self.paused = True
            self.generation += 1
            self.last_manual_pause = self.generation

    def start_task(self):
        with self.lock:
            self.generation = 0
            self.paused = False
            self.supplements = []
            self.last_manual_pause = self.last_supplement = -1

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
            self.last_supplement = self.generation
            self.paused = True

    def capture(self, *, resume=False, expected_generation=None):
        with self.lock:
            if resume and (expected_generation is None or self.generation == expected_generation):
                self.paused = False
            return self.generation, self.paused

    def cancelled(self, generation):
        with self.lock:
            return self.paused or self.generation != generation

    def snapshot(self):
        with self.lock:
            return {"paused": self.paused, "generation": self.generation,
                    "manual": self.paused and self.last_manual_pause > self.last_supplement,
                    "queued": len(self.supplements)}
