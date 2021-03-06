#!/usr/bin/python3
import argparse
import logging
import logging.handlers
import os
import sys

import notify2
from PyQt4.QtGui import QApplication

from pyrdp import player
from pyrdp.core import getLoggerPassFilters
from pyrdp.logging import log
from pyrdp.logging import LOGGER_NAMES


class NotifyHandler(logging.StreamHandler):
    """
    Logging handler that sends desktop (OS) notifications.
    """

    def __init__(self):
        notify2.init("pyrdp-player")
        super(NotifyHandler, self).__init__()

    def emit(self, record):
        """
        Sends a notification to the OS to display.
        :param record: the LogRecord object
        """
        notification = notify2.Notification(record.getMessage())
        notification.show()


def prepare_loggers(logLevel):
    """
    Sets up the "liveplayer" and "liveplayer.ui" loggers to print messages and send notifications on connect.
    """
    log.prepare_pyrdp_logger(logLevel)
    log.prepare_ssl_session_logger()

    if not os.path.exists("log"):
        os.makedirs("log")

    liveplayer_logger = getLoggerPassFilters(LOGGER_NAMES.LIVEPLAYER)
    liveplayer_logger.setLevel(logLevel)

    liveplayer_ui_logger = getLoggerPassFilters(f"{LOGGER_NAMES.LIVEPLAYER}.ui")
    liveplayer_ui_logger.setLevel(logLevel)

    formatter = log.get_formatter()

    stream_handler = logging.StreamHandler()
    file_handler = logging.FileHandler("log/liveplayer.log")
    stream_handler.setFormatter(formatter)
    file_handler.setFormatter(formatter)
    liveplayer_logger.addHandler(stream_handler)
    liveplayer_logger.addHandler(file_handler)

    notify_handler = NotifyHandler()
    notify_handler.setFormatter(logging.Formatter("[%(asctime)s] - %(message)s"))
    liveplayer_ui_logger.addHandler(notify_handler)


def main():
    """
    Parse the provided command line arguments and launch the GUI.
    :return: The app exit code (0 for normal exit, non-zero for errors)
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("replay", help="Replay files to open on launch (optional)", nargs="*")
    parser.add_argument("-b", "--bind", help="Bind address (default: 127.0.0.1)", default="127.0.0.1")
    parser.add_argument("-p", "--port", help="Bind port (default: 3000)", default=3000)
    parser.add_argument("-L", "--log-level", help="Log level", default="INFO", choices=["INFO", "DEBUG", "WARNING", "ERROR", "CRITICAL"], nargs="?")

    arguments = parser.parse_args()

    logLevel = getattr(logging, arguments.log_level)
    prepare_loggers(logLevel)

    app = QApplication(sys.argv)

    mainWindow = player.MainWindow(arguments.bind, int(arguments.port), arguments.replay)
    mainWindow.show()

    return app.exec_()


if __name__ == '__main__':
    mlog = getLoggerPassFilters(LOGGER_NAMES.LIVEPLAYER)
    ulog = getLoggerPassFilters(f"{LOGGER_NAMES.LIVEPLAYER}.ui")
    sys.exit(main())
