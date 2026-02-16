# soubor: core/logging.py
import logging
import os
import sys
from datetime import datetime


def setup_logging(level_console="INFO", level_file="DEBUG"):
    """Nastaví logging: INFO+ do konzole, DEBUG+ do souboru."""
    os.makedirs("logs", exist_ok=True)
    log_file = f"logs/predator_{datetime.now().strftime('%Y%m%d_%H%M')}.log"

    # Formát pro konzoli (kompaktní)
    console_formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"
    )

    # Formát pro soubor (plný detail)
    file_formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Konzolový handler – jen INFO a vyšší
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(getattr(logging, level_console))
    console_handler.setFormatter(console_formatter)

    # Souborový handler – všechno
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(getattr(logging, level_file))
    file_handler.setFormatter(file_formatter)

    # Nastavení root loggeru
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers = [console_handler, file_handler]

    # Ztlumit spam od httpx, httpcore, websockets
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)


# Pro rychlý test (spusť tento soubor samostatně)
if __name__ == "__main__":
    setup_logging()
    logging.getLogger("Test").info(
        "Logging inicializováno – konzole INFO, soubor DEBUG"
    )
