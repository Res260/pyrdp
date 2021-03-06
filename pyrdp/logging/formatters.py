"""
Contains custom logging handlers for the library.
"""
import binascii
import json
import logging
from datetime import datetime


class JSONFormatter(logging.Formatter):
    """
    Formatter that returns a single JSON line of the provided data.
    Example usage: logger.info("MITM Server listening on port %(port)d", {"port": listenPort})
    Will output: {"message": "MITM Server listening on port %(port)d", "loggerName": "mitm", "timestamp": "2018-12-03T10:51:S.f-0500", "level": "INFO", "port": 3388}
    """

    def __init__(self):
        super().__init__()

    def format(self, record: logging.LogRecord) -> str:
        data = {
            "message": record.msg,
            "loggerName": record.name,
            "timestamp": datetime.strftime(datetime.utcfromtimestamp(record.created), "%Y-%m-%dT%H:%M:%S.%f"),
            "level": record.levelname,
        }
        data.update(record.args)
        return json.dumps(data, ensure_ascii=False, default=lambda item: item.__repr__())


class SSLSecretFormatter(logging.Formatter):
    """
    Custom formatter used to log SSL client randoms and master secrets.
    """

    def __init__(self):
        super(SSLSecretFormatter, self).__init__("format")

    def format(self, record: logging.LogRecord):
        return "CLIENT_RANDOM {} {}".format(binascii.hexlify(record.msg).decode(),
                                            binascii.hexlify(record.args[0]).decode())
