"""
kai-scheduler — placeholder for Phase 0.
Runs as a long-lived process. Actual cron jobs added in Phase 4.
"""
import time
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [scheduler] %(message)s")
log = logging.getLogger(__name__)


def main():
    log.info("kai-scheduler started — waiting for Phase 4 jobs")
    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
