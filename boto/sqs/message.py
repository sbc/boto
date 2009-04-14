# Copyright (c) 2006,2007 Mitch Garnaat http://garnaat.org/
#
# Permission is hereby granted, free of charge, to any person obtaining a
# copy of this software and associated documentation files (the
# "Software"), to deal in the Software without restriction, including
# without limitation the rights to use, copy, modify, merge, publish, dis-
# tribute, sublicense, and/or sell copies of the Software, and to permit
# persons to whom the Software is furnished to do so, subject to the fol-
# lowing conditions:
#
# The above copyright notice and this permission notice shall be included
# in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS
# OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABIL-
# ITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT
# SHALL THE AUTHOR BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, 
# WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS
# IN THE SOFTWARE.

"""
SQS Message

A Message represents the data stored in an SQS queue.  The rules for what is allowed within an SQS
Message are here:

    http://docs.amazonwebservices.com/AWSSimpleQueueService/2008-01-01/SQSDeveloperGuide/Query_QuerySendMessage.html

So, at it's simplest level a Message just needs to allow a developer to store bytes in it and get the bytes
back out.  However, to allow messages to have richer semantics, the Message class must support the 
following interfaces:

The constructor for the Message class must accept a keyword parameter "queue" which is an instance of a
boto Queue object and represents the queue that the message will be stored in.  The default value for
this parameter is None.

The constructor for the Message class must accept a keyword parameter "body" which represents the
content or body of the message.  The format of this parameter will depend on the behavior of the
particular Message subclass.  For example, if the Message subclass provides dictionary-like behavior to the
user the body passed to the constructor should be a dict-like object that can be used to populate
the initial state of the message.

The Message class must provide an encode method that accepts a value of the same type as the body
parameter of the constructor and returns a string of characters that are able to be stored in an
SQS message body (see rules above).

The Message class must provide a decode method that accepts a string of characters that can be
stored (and probably were stored!) in an SQS message and return an object of a type that is consistent
with the "body" parameter accepted on the class constructor.

The Message class must provide a __len__ method that will return the size of the encoded message
that would be stored in SQS based on the current state of the Message object.

The Message class must provide a get_body method that will return the body of the message in the
same format accepted in the constructor of the class.

The Message class must provide a set_body method that accepts a message body in the same format
accepted by the constructor of the class.  This method should alter to the internal state of the
Message object to reflect the state represented in the message body parameter.

The Message class must provide a get_body_encoded method that returns the current body of the message
in the format in which it would be stored in SQS.
"""

import base64
import StringIO
from boto.sqs.attributes import Attributes

class RawMessage:
    """
    Base class for SQS messages.  RawMessage does not encode the message
    in any way.  Whatever you store in the body of the message is what
    will be written to SQS and whatever is returned from SQS is stored
    directly into the body of the message.
    """
    
    def __init__(self, queue=None, body=''):
        self.queue = queue
        self.set_body(body)
        self.id = None
        self.receipt_handle = None
        self.md5 = None
        self.attributes = Attributes(self)

    def __len__(self):
        return len(self.encode(self._body))

    def startElement(self, name, attrs, connection):
        if name == 'Attribute':
            return self.attributes
        return None

    def endElement(self, name, value, connection):
        if name == 'Body':
            self.set_body(self.decode(value))
        elif name == 'MessageId':
            self.id = value
        elif name == 'ReceiptHandle':
            self.receipt_handle = value
        elif name == 'MD5OfBody':
            self.md5 = value
        else:
            setattr(self, name, value)

    def encode(self, value):
        """Transform body object into serialized byte array format."""
        return value

    def decode(self, value):
        """Transform seralized byte array into any object."""
        return value
 
    def set_body(self, body):
        """Override the current body for this object, using decoded format."""
        self._body = body

    def get_body(self):
        return self._body
    
    def get_body_encoded(self):
        """
        This method is really a semi-private method used by the Queue.write
        method when writing the contents of the message to SQS.
        You probably shouldn't need to call this method in the normal course of events.
        """
        return self.encode(self.get_body())

    def delete(self):
        if self.queue:
            return self.queue.delete_message(self)

    def change_visibility(self, visibility_timeout):
        if self.queue:
            self.queue.connection.change_message_visibility(self.queue,
                                                            self.receipt_handle,
                                                            visibility_timeout)
    
class Message(RawMessage):
    """
    The default Message class used for SQS queues.  This class automatically
    encodes/decodes the message body using Base64 encoding to avoid any
    illegal characters in the message body.  See:

    http://developer.amazonwebservices.com/connect/thread.jspa?messageID=49680%EC%88%90

    for details on why this is a good idea.  The encode/decode is meant to
    be transparent to the end-user.
    """
    
    def encode(self, value):
        return base64.b64encode(value)

    def decode(self, value):
        return base64.b64decode(value)

class MHMessage(Message):
    """
    The MHMessage class provides a message that provides RFC821-like
    headers like this:

    HeaderName: HeaderValue

    The encoding/decoding of this is handled automatically and after
    the message body has been read, the message instance can be treated
    like a mapping object, i.e. m['HeaderName'] would return 'HeaderValue'.
    """

    def __init__(self, queue=None, body=None, xml_attrs=None):
        if body == None or body == '':
            body = {}
        Message.__init__(self, queue, body)

    def decode(self, value):
        msg = {}
        fp = StringIO.StringIO(value)
        line = fp.readline()
        while line:
            delim = line.find(':')
            key = line[0:delim]
            value = line[delim+1:].strip()
            msg[key.strip()] = value.strip()
            line = fp.readline()
        return msg

    def encode(self, value):
        s = ''
        for item in value.items():
            s = s + '%s: %s\n' % (item[0], item[1])
        return s

    def __getitem__(self, key):
        if self._body.has_key(key):
            return self._body[key]
        else:
            raise KeyError(key)

    def __setitem__(self, key, value):
        self._body[key] = value
        self.set_body(self._body)

    def keys(self):
        return self._body.keys()

    def values(self):
        return self._body.values()

    def items(self):
        return self._body.items()

    def has_key(self, key):
        return self._body.has_key(key)

    def update(self, d):
        self._body.update(d)
        self.set_body(self._body)

    def get(self, key, default=None):
        return self._body.get(key, default)

class EncodedMHMessage(MHMessage):
    """
    The EncodedMHMessage class provides a message that provides RFC821-like
    headers like this:

    HeaderName: HeaderValue

    This variation encodes/decodes the body of the message in base64 automatically.
    The message instance can be treated like a mapping object,
    i.e. m['HeaderName'] would return 'HeaderValue'.
    """

    def decode(self, value):
        value = base64.b64decode(value)
        return MHMessage.decode(value)

    def encode(self, value):
        value = MHMessage.encode(value)
        return base64.b64encode(value)
    
