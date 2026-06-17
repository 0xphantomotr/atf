from app.core.logging import configure_logging, get_logger


def main() -> None:
    configure_logging()
    logger = get_logger(__name__)
    logger.info("worker.runner.loaded")


if __name__ == "__main__":
    main()

