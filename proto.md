(Unnamed RPC Protocol)
======================

* Pulls a bunch of ideals from varlink
* Encoded as two msgpack streams, one in each direction
* The stream is broken up into channels, where there is a single call and
potentially many responses.
* Method names are forward-dotted paths (eg com.foobar.service.Method)
* Parameters and returns are both maps; at the application level, methods only take keyword arguments and return named return values
* Methods may return 0 or more returns or errors
* Method calls are fully parallel. No pipelining necessary.

In paricular:

> It should be easy to forward, proxy, redirect varlink interfaces over any connection-oriented transport. Varlink should be free of any side-effects of local APIs. All interactions need to be simple messages on a network, not carrying things like file descriptors or direct references to locally stored files.

This version of the protocol is not self-describing. Namespacing method names is encouraged, however.

The empty namespace (method names starting with `.`) is reserved.

Packet
------

Packets are either arrays or strings.

Array packets are used to encode RPC data:
0. int: The channel ID
1. int: Packet type
2+. Any additional arguments

Channels are created by the client sending a `Call` packet and destroyed by the `Shoosh` packet.

String packets are unstructured textual log messages not associated with a partical channel. Note that no structure is defined on this stream, so you need to include newlines and other markers.

### Packet types

#### 0 Shoosh (Any)
No parameters.

Deletes a channel. No further returns or errors should be produced by the server.

It is up to the application to decide if this cancels the operation.

When sent by the server, represents that the operation has finished.

#### 1 Call (C2S)
Parameters:
1. name: string
2. parameters: map, string->Any
3. log level: int, optional

A method call. This creates a channel.

If the log level is given, than any log message produced by the server less than this level should be surpressed and not delivered to the client. If the log level is not given, then no log messages should be sent.

### 2 Return (S2C)
Parameters:
1. value: map, string->Any

A return value.

### 3 Error (S2C)
Parameters:
1. Name: string, dotted path
2. Additional: map or nil

Represents an error. Often will be the last item produced by a method.

If there's additional data, it's suggested that the `msg` key is a human-readable message.

### 4 Log (S2C)
Parameters:
1. group: str, the dotted path of the log group
2. level: int, the severity of the log
3. msg: str, the message

A semi-structured log message from the server. A lower level is less severe.

The suggested log levels are:

* 0: Trace (Extremely verbose developer tracing)
* 10: Debug (Mostly significant to developers)
* 20: Verbose (Potentially interesting status updates to the user)
* 30: Informational (Higher-level status updates to the user)
* 40: Warning (Something funky happened, a human may want to look into it)
* 50: Error (Something bad happened, results were probably affected, a human might need to take action)
* 60: Critical (Stuff fell over and is on fire, results were certainly affected, and a human probably needs to take action)

Additional levels may be defined by the application.
