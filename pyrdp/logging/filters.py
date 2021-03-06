from logging import Filter, LogRecord

from pyrdp.core import Config


class SensorFilter(Filter):
    """
    Filter that adds the sensor id to the logrecord's arguments.
    """

    def __init__(self):
        super().__init__()

    def filter(self, record: LogRecord) -> bool:
        record.args.update({"sensor": Config.arguments.sensor_id})
        return True


class ConnectionMetadataFilter(Filter):
    """
    Filter that adds arguments to the record regarding the
    active session (such as source IP, port and sessionId)
    """

    def __init__(self, server, sessionId: str):
        super().__init__()
        self.server = server
        self.sessionId = sessionId

    def filter(self, record: LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = {}

        clientInfo = self.server.tcp.transport.client
        record.args.update({
            "src_ip": clientInfo[0],
            "src_port": clientInfo[1],
            "session": self.sessionId
        })

        return True