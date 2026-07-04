from __future__ import annotations

import time


def run_worker() -> None:
    while True:
        print("OpenSQL AutoRAG worker heartbeat", flush=True)
        time.sleep(10)


if __name__ == "__main__":
    run_worker()
