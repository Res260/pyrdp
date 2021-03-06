from binascii import hexlify
from io import BytesIO

from pyrdp.core import Uint16BE, Uint16LE, Uint8
from pyrdp.enum import DrawingOrderControlFlags, EncryptionMethod, FastPathInputType, \
    FastPathOutputCompressionType, FastPathOutputType, FastPathSecurityFlags, FIPSVersion, ParserMode
from pyrdp.logging import log
from pyrdp.parser.parser import Parser
from pyrdp.parser.rdp.bitmap import BitmapParser
from pyrdp.parser.rdp.security import BasicSecurityParser
from pyrdp.pdu import FastPathBitmapEvent, FastPathEventRaw, FastPathMouseEvent, FastPathOrdersEvent, FastPathPDU, \
    FastPathScanCodeEvent, SecondaryDrawingOrder
from pyrdp.pdu.rdp.fastpath import FastPathOutputUpdateEvent
from pyrdp.security import RC4Crypter


class BasicFastPathParser(BasicSecurityParser):
    def __init__(self, mode):
        super().__init__()
        self.mode = mode
        input, output = FastPathInputParser(), FastPathOutputParser()

        if mode == ParserMode.CLIENT:
            self.readParser = output
            self.writeParser = input
        else:
            self.readParser = input
            self.writeParser = output

    def getPDULength(self, data):
        stream = BytesIO(data)
        stream.read(1)
        return self.parseLength(stream)

    def getPDULengthWithSocket(self, socket):
        """
        Same as getPDULength, but with a network socket.
        :type socket: socket.socket
        """
        data = socket.recv(1)
        data2, length = self.parseLengthWithSocket(socket)
        data += data2
        return data, length

    def isCompletePDU(self, data):
        if len(data) == 1:
            return False

        if Uint8.unpack(data[1]) & 0x80 != 0 and len(data) == 2:
            return False

        return len(data) >= self.getPDULength(data)

    def parse(self, data):
        stream = BytesIO(data)
        header = Uint8.unpack(stream)
        eventCount = self.parseEventCount(header)
        pduLength = self.parseLength(stream)

        if eventCount == 0:
            eventCount = Uint8.unpack(stream)

        data = stream.read(pduLength - stream.tell())
        events = self.parseEvents(data)
        return FastPathPDU(header, events)

    def parseEventCount(self, header):
        if self.mode == ParserMode.SERVER:
            return (header >> 2) & 0xf
        else:
            return 1

    def parseLength(self, stream):
        length = Uint8.unpack(stream)

        if length & 0x80 != 0:
            length = ((length & 0x7f) << 8) | Uint8.unpack(stream)

        return length

    def parseLengthWithSocket(self, socket):
        """
        Same as parseLength, but with a network socket.
        :type socket: socket.socket
        """
        data = socket.recv(1)
        length = Uint8.unpack(data)

        if length & 0x80 != 0:
            data2 = socket.recv(1)
            data += data2
            length = ((length & 0x7f) << 8) | Uint8.unpack(data2)

        return data, length

    def parseEvents(self, data):
        events = []

        while len(data) > 0:
            eventLength = self.readParser.getEventLength(data)
            eventData = data[: eventLength]
            data = data[eventLength :]

            try:
                event = self.readParser.parse(eventData)
            except KeyboardInterrupt:
                raise
            except Exception:
                log.error("Exception occurred when receiving: %(data)s", {"data": hexlify(eventData)})
                raise

            events.append(event)

        return events

    def writeHeader(self, stream: BytesIO, pdu: FastPathPDU):
        header = (pdu.header & 0xc0) | self.getHeaderFlags()
        eventCount = len(pdu.events)

        if eventCount <= 15 and self.mode == ParserMode.CLIENT:
            header |= eventCount << 2

        Uint8.pack(header, stream)
        self.writeLength(stream, pdu)

    def writeBody(self, stream: BytesIO, pdu: FastPathPDU):
        eventCount = len(pdu.events)

        if self.mode == ParserMode.CLIENT and eventCount > 15:
            Uint8.pack(eventCount, stream)

    def writePayload(self, stream: BytesIO, pdu: FastPathPDU):
        self.writeEvents(stream, pdu)

    def writeLength(self, stream, pdu):
        length = self.calculatePDULength(pdu)
        Uint16BE.pack(length | 0x8000, stream)

    def writeEvents(self, stream, pdu):
        for event in pdu.events:
            eventData = self.writeParser.write(event)
            stream.write(eventData)

    def calculatePDULength(self, pdu):
        # Header + length bytes
        length = 3
        length += sum(self.writeParser.getEventLength(event) for event in pdu.events)

        if self.mode == ParserMode.CLIENT and len(pdu.events) > 15:
            length += 1

        return length

    def getHeaderFlags(self):
        return 0


class SignedFastPathParser(BasicFastPathParser):
    def __init__(self, crypter, mode):
        BasicFastPathParser.__init__(self, mode)
        self.crypter = crypter
        self.eventData = b""

    def parse(self, data):
        stream = BytesIO(data)
        header = Uint8.unpack(stream)
        eventCount = self.parseEventCount(header)
        pduLength = self.parseLength(stream)
        signature = stream.read(8)

        if eventCount == 0:
            eventCount = Uint8.unpack(stream)

        data = stream.read(pduLength - stream.tell())

        if header & FastPathSecurityFlags.FASTPATH_OUTPUT_ENCRYPTED != 0:
            data = self.crypter.decrypt(data)
            self.crypter.addDecryption()

        events = self.parseEvents(data)
        return FastPathPDU(header, events)

    def writeBody(self, stream, pdu):
        eventStream = BytesIO()
        self.writeEvents(eventStream, pdu)
        self.eventData = eventStream.getvalue()
        signature = self.crypter.sign(self.eventData, True)

        stream.write(signature)
        BasicFastPathParser.writeBody(self, stream, pdu)

    def writePayload(self, stream, pdu):
        eventData = self.crypter.encrypt(self.eventData)
        self.crypter.addEncryption()
        self.eventData = b""

        stream.write(eventData)

    def calculatePDULength(self, pdu):
        return BasicFastPathParser.calculatePDULength(self, pdu) + 8

    def getHeaderFlags(self):
        return FastPathSecurityFlags.FASTPATH_OUTPUT_ENCRYPTED | FastPathSecurityFlags.FASTPATH_OUTPUT_SECURE_CHECKSUM


class FIPSFastPathParser(SignedFastPathParser):
    def __init__(self, crypter, mode):
        SignedFastPathParser.__init__(self, crypter, mode)

    def parse(self, data):
        stream = BytesIO(data)
        header = Uint8.unpack(stream)
        eventCount = self.parseEventCount(header)
        pduLength = self.parseLength(stream)
        fipsLength = Uint16LE.unpack(stream)
        version = Uint8.unpack(stream)
        padLength = Uint8.unpack(stream)
        signature = stream.read(8)

        if eventCount == 0:
            eventCount = Uint8.unpack(stream)

        data = stream.read(pduLength - stream.tell())

        if header & FastPathSecurityFlags.FASTPATH_OUTPUT_ENCRYPTED != 0:
            data = self.crypter.decrypt(data)
            self.crypter.addDecryption()

        events = self.parseEvents(data)
        return FastPathPDU(header, events)

    def writeBody(self, stream, pdu):
        bodyStream = BytesIO()
        SignedFastPathParser.writeBody(self, bodyStream, pdu)
        body = bodyStream.getvalue()

        Uint16LE.pack(0x10, stream)
        Uint8.pack(FIPSVersion.TSFIPS_VERSION1, stream)
        Uint8.pack(self.crypter.getPadLength(self.eventData), stream)
        stream.write(body)

    def calculatePDULength(self, pdu):
        return SignedFastPathParser.calculatePDULength(self, pdu) + 4


class FastPathInputParser(Parser):
    INPUT_EVENT_LENGTHS = {
        FastPathInputType.FASTPATH_INPUT_EVENT_SCANCODE: 2,
        FastPathInputType.FASTPATH_INPUT_EVENT_MOUSE: 7,
        FastPathInputType.FASTPATH_INPUT_EVENT_MOUSEX: 7,
        FastPathInputType.FASTPATH_INPUT_EVENT_SYNC: 1,
        FastPathInputType.FASTPATH_INPUT_EVENT_UNICODE: 3,
        FastPathInputType.FASTPATH_INPUT_EVENT_QOE_TIMESTAMP: 5,
    }

    def getEventLength(self, data):
        if isinstance(data, FastPathEventRaw):
            return len(data.data)
        elif isinstance(data, bytes):
            header = Uint8.unpack(data[0])
            type = (header & 0b11100000) >> 5
            return FastPathInputParser.INPUT_EVENT_LENGTHS[type]
        elif isinstance(data, FastPathScanCodeEvent):
            return FastPathInputParser.INPUT_EVENT_LENGTHS[FastPathInputType.FASTPATH_INPUT_EVENT_SCANCODE]
        elif isinstance(data, FastPathMouseEvent):
            return FastPathInputParser.INPUT_EVENT_LENGTHS[FastPathInputType.FASTPATH_INPUT_EVENT_MOUSE]
        raise ValueError("Unsupported event type?")

    def parse(self, data):
        stream = BytesIO(data)
        eventHeader = Uint8.unpack(stream.read(1))
        eventCode = (eventHeader & 0b11100000) >> 5
        eventFlags= eventHeader & 0b00011111
        if eventCode == FastPathInputType.FASTPATH_INPUT_EVENT_SCANCODE:
            return self.parseScanCode(eventFlags, eventHeader, stream)
        elif eventCode == FastPathInputType.FASTPATH_INPUT_EVENT_MOUSE:
            return self.parseMouseEvent(data, eventHeader)
        return FastPathEventRaw(data)

    def parseMouseEvent(self, data, eventHeader):
        pointerFlags = Uint16LE.unpack(data[1:3])
        mouseX = Uint16LE.unpack(data[3:5])
        mouseY = Uint16LE.unpack(data[5:7])
        return FastPathMouseEvent(eventHeader, pointerFlags, mouseX, mouseY)

    def parseScanCode(self, eventFlags, eventHeader, stream):
        scancode = Uint8.unpack(stream.read(1))
        return FastPathScanCodeEvent(eventHeader, scancode, eventFlags)

    def write(self, event):
        if isinstance(event, FastPathEventRaw):
            return event.data
        elif isinstance(event, FastPathScanCodeEvent):
            return self.writeScanCodeEvent(event)
        elif isinstance(event, FastPathMouseEvent):
            return self.writeMouseEvent(event)
        raise ValueError("Invalid FastPath event: {}".format(event))

    def writeScanCodeEvent(self, event):
        raw_data = BytesIO()
        Uint8.pack(event.rawHeaderByte, raw_data)
        Uint8.pack(event.scancode, raw_data)
        return raw_data.getvalue()

    def writeMouseEvent(self, event):
        rawData = BytesIO()
        Uint8.pack(event.rawHeaderByte, rawData)
        Uint16LE.pack(event.pointerFlags, rawData)
        Uint16LE.pack(event.mouseX, rawData)
        Uint16LE.pack(event.mouseY, rawData)
        return rawData.getvalue()


class FastPathOutputParser(Parser):
    def __init__(self):
        super().__init__()
        self.bitmapParser = BitmapParser()

    def getEventLength(self, event: FastPathOutputUpdateEvent):

        if isinstance(event, bytes):
            header = Uint8.unpack(event[0])
            if self.isCompressed(header):
                return Uint16LE.unpack(event[2: 4]) + 4
            else:
                return Uint16LE.unpack(event[1: 3]) + 3

        size = 3

        if self.isCompressed(event.header):
            size += 1

        if isinstance(event, FastPathOrdersEvent):
            size += 2 + len(event.orderData)
        elif isinstance(event, FastPathBitmapEvent):
            size += len(event.payload)
        elif isinstance(event, FastPathOutputUpdateEvent):
            length = len(event.payload) + 3
            if event.compressionFlags is not None:
                length += 1
            return length

        return size

    def isCompressed(self, header):
        return (header >> 6) & FastPathOutputCompressionType.FASTPATH_OUTPUT_COMPRESSION_USED

    def parse(self, data) -> FastPathOutputUpdateEvent:
        stream = BytesIO(data)
        header = Uint8.unpack(stream)

        compressionFlags = None

        if self.isCompressed(header):
            compressionFlags = Uint8.unpack(stream)

        size = Uint16LE.unpack(stream)

        eventType = header & 0xf
        fragmentation = header & 0b00110000 != 0
        if fragmentation:
            log.debug("Fragmentation is present in output fastpath event packets."
                      " Not parsing it and saving to FastPathOutputUpdateEvent.")
            return FastPathOutputUpdateEvent(header, compressionFlags, payload=stream.read(size))

        if eventType == FastPathOutputType.FASTPATH_UPDATETYPE_BITMAP:
            return self.parseBitmapEventRaw(stream, header, compressionFlags, size)
        elif eventType == FastPathOutputType.FASTPATH_UPDATETYPE_ORDERS:
            return self.parseOrdersEvent(stream, header, compressionFlags, size)

        read = stream.read(size)
        return FastPathOutputUpdateEvent(header, compressionFlags, read)

    def parseBitmapEventRaw(self, stream, header, compressionFlags, size):
        return FastPathBitmapEvent(header, compressionFlags, [], stream.read(size))

    def parseBitmapEvent(self, fastPathBitmapEvent: FastPathOutputUpdateEvent) -> FastPathBitmapEvent:
        """
        :return: a FastPathBitmapEvent with bitmapUpdateData
        """
        rawBitmapUpdateData = fastPathBitmapEvent.payload
        stream = BytesIO(rawBitmapUpdateData)
        updateType = Uint16LE.unpack(stream.read(2))
        bitmapData = self.bitmapParser.parseBitmapUpdateData(stream.read())

        return FastPathBitmapEvent(fastPathBitmapEvent.header, fastPathBitmapEvent.compressionFlags,
                                   bitmapData, rawBitmapUpdateData)

    def writeBitmapEvent(self, stream, event: FastPathBitmapEvent):
        stream.write(event.payload)

    def parseOrdersEvent(self, stream, header, compressionFlags, size):
        orderCount = Uint16LE.unpack(stream)
        orderData = stream.read(size - 2)
        assert len(orderData) == size - 2
        ordersEvent = FastPathOrdersEvent(header, compressionFlags, orderCount, orderData)
        controlFlags = Uint8.unpack(orderData[0])
        if controlFlags & (DrawingOrderControlFlags.TS_SECONDARY | DrawingOrderControlFlags.TS_STANDARD)\
                == (DrawingOrderControlFlags.TS_SECONDARY | DrawingOrderControlFlags.TS_STANDARD):
            ordersEvent.secondaryDrawingOrders = self.parseSecondaryDrawingOrder(orderData)
        elif controlFlags & DrawingOrderControlFlags.TS_SECONDARY:
            pass
        return ordersEvent

    def parseSecondaryDrawingOrder(self, orderData):
        stream = BytesIO(orderData)
        controlFlags = Uint8.unpack(stream.read(1))
        orderLength = Uint16LE.unpack(stream.read(2))
        extraFlags = Uint16LE.unpack(stream.read(2))
        orderType = Uint8.unpack(stream.read(1))
        return SecondaryDrawingOrder(controlFlags, orderLength, extraFlags, orderType)

    def writeOrdersEvent(self, stream, event):
        Uint16LE.pack(event.orderCount, stream)
        stream.write(event.orderData)

    def write(self, event: FastPathOutputUpdateEvent):

        stream = BytesIO()
        Uint8.pack(event.header, stream)

        if event.compressionFlags is not None:
            Uint8.pack(event.compressionFlags if event.compressionFlags else 0, stream)

        updateStream = BytesIO()

        if isinstance(event, FastPathBitmapEvent):
            self.writeBitmapEvent(updateStream, event)
        elif isinstance(event, FastPathOrdersEvent):
            self.writeOrdersEvent(updateStream, event)
        else:
            # Means it's simply a FastPathOutputUpdateEvent, this needs to be the last elif.
            updateStream.write(event.payload)

        updateData = updateStream.getvalue()
        Uint16LE.pack(len(updateData), stream)
        stream.write(updateData)
        return stream.getvalue()


def createFastPathParser(tls, encryptionMethod, crypter, mode):
    """
    Create a fast-path parser based on which encryption method is used.
    :param tls: whether TLS is used or not.
    :type tls: bool
    :param encryptionMethod: the encryption method.
    :type encryptionMethod: EncryptionMethod
    :param crypter: the crypter for this connection.
    :type crypter: RC4Crypter | RC4CrypterProxy
    :param mode: the fast-path parser mode.
    :type mode: ParserMode
    """
    if tls:
        return BasicFastPathParser(mode)
    elif encryptionMethod in [EncryptionMethod.ENCRYPTION_40BIT, EncryptionMethod.ENCRYPTION_56BIT, EncryptionMethod.ENCRYPTION_128BIT]:
        return SignedFastPathParser(crypter, mode)
    elif encryptionMethod == EncryptionMethod.ENCRYPTION_FIPS:
        return FIPSFastPathParser(crypter, mode)
    else:
        raise ValueError("Invalid fast-path layer mode")