# first we need to write a function that recieves data from a connected socket
# recv(bufszie) where bufsize is the max number of bytes to read in a single call. 
# so if less data is availabe we return what is there immedialty
# if we want to rexieve an exact ammmount of data
# we need to continue calling recv with the difference of what we needed and what we got.

import socket
import secrets
import json
import struct
from enum import IntEnum
import time
import select

#iperf states
class TestState(IntEnum):
    # Success / Flow states
    TEST_START       = 1    # server -> client
    TEST_RUNNING     = 2    # server -> client
    TEST_END         = 4    # client -> server: "my sending is finished"
    PARAM_EXCHANGE   = 9    # server -> client: "send your parameters"
    CREATE_STREAMS   = 10   # server -> client: "open your data connection"
    SERVER_TERMINATE = 11   # server -> client: test aborted by the server
    EXCHANGE_RESULTS = 13   # server -> client: "send your results JSON"
    DISPLAY_RESULTS  = 14   # server -> client: results exchange finished
    IPERF_DONE       = 16   # client -> server: "I'm done, closing"
    
    # Error states
    ACCESS_DENIED    = -1   # server -> client: busy with another test
    SERVER_ERROR     = -2   # server -> client: followed by two 4-byte ints (error code, errno)

#consts 
BLOCK_SIZE = 131072

# this function recieves exactly buffersize bytes into a buffer byte array and returns it.
def recv_exact(socket, buffersize):
    need_to_fill = buffersize
    my_buffer = bytearray()

    # keep calling recv untill need_to_fill is 0.
    while need_to_fill > 0:
        data = socket.recv(need_to_fill)

        # case where server disconncts
        if not data:
            raise ConnectionAbortedError("The Socket closed before buffer could be filled!")

        my_buffer.extend(data)
        need_to_fill -= len(data)

    return bytes(my_buffer)


# this function generates a secure random cookie string using random chars like iperf_util.cs make_cookie()
def make_cookie() -> str:
    COOKIE_SIZE = 37 # size of iper3 cookiesize defined in iperf.h
    
    rndchars = b"abcdefghijklmnopqrstuvwxyz234567"
    alphabet_len = len(rndchars)

    # Generates COOKIE_SIZE cryptographically secure random bytes
    random_bytes = secrets.token_bytes(COOKIE_SIZE)
    
    # We process up to (COOKIE_SIZE - 1) just like the C code loop boundary
    cookie_chars = []
    for i in range(COOKIE_SIZE - 1):
        byte_val = random_bytes[i]
        mapped_char = chr(rndchars[byte_val % alphabet_len])
        cookie_chars.append(mapped_char)
    cookie_chars.append("\x00")
    msg = "".join(cookie_chars)
    return msg.encode('utf-8')

def read_state(socket):
    # read one state byte
    data = recv_exact(socket, 1)
    # read single byte as a signed int
    return int.from_bytes(data, "big", signed=True)

# tests the returned state with an expected goal state for param exchange.
def expect_state(socket, goal):
    state = read_state(socket)
    if state == goal:
        return 0
    else:
        if state == -1:
            # server busy
            print("busy")
        elif state == -2:
            # server error
            print("server error")
        else:
            # protocol mismatch
            print("general error")
        raise ConnectionError(f"The expected state {TestState(goal).name} was not returned but rather {TestState(state).name}")


def send_json(socket, obj):
    # encode our json data dict into a string literal json and then into bytes
    data = json.dumps(obj).encode('utf-8')

    byte_count = struct.pack("!I", len(data))
    socket.sendall(byte_count)
    socket.sendall(data)

class IperfError(Exception):
    """A test against one server failed. The message says why."""


def send_state(sock, state):
    # One signed byte, like iperf_set_send_state() (iperf_api.c:2064)
    sock.sendall(state.to_bytes(1, "big", signed=True))


def recv_json(sock, max_len=1_000_000):
    # 4-byte big-endian length, then that many bytes of JSON (JSON_read(), iperf_api.c:3197)
    (length,) = struct.unpack("!I", recv_exact(sock, 4))
    if length == 0 or length > max_len:      # JSON_read() also rejects 0 and oversized lengths
        raise IperfError(f"bad JSON length {length}")
    return json.loads(recv_exact(sock, length).decode())


def raise_for_state(sock, state, expected=None):
    # Mirrors the error cases in iperf_handle_message_client() (iperf_client_api.c:303)
    if state == TestState.SERVER_ERROR:
        # Two 4-byte big-endian ints follow: iperf error code, then OS errno (cleanup_server())
        (code,) = struct.unpack("!i", recv_exact(sock, 4))
        (err,) = struct.unpack("!i", recv_exact(sock, 4))
        raise IperfError(f"SERVER_ERROR: iperf error code {code}, errno {err}")
    if state == TestState.ACCESS_DENIED:
        raise IperfError("ACCESS_DENIED: server is busy")
    if state == TestState.SERVER_TERMINATE:
        raise IperfError("SERVER_TERMINATE: server ended the test")
    name = TestState.STATE_NAMES.get(state, "unknown")
    raise IperfError(f"unexpected state {state} ({name}), expected {expected}")


def run_test(host, port, duration, block_size, interval, cc=None):
    # make the cookie
    cookie = make_cookie()
    assert len(cookie) == 37
    
    # create a socket for ipv4 with a two way stream like tcp
    control_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        control_socket.settimeout(5.0)

        # set tcp no delay
        # disable nagles algo to send data immediatly w/o buffering delays (we deliver immediatly even if the paylod is small)
        # with nagle on if we send a small message, we don't send a next unless we get an ACK but that ACK might be delayed by the server! 
        control_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

        control_socket.connect((host, port))

        # send cookie
        control_socket.sendall(cookie)

        # read one state byte and expect param_exchange
        expect_state(control_socket, TestState.PARAM_EXCHANGE)

        print("ok")
        params = {
            "tcp": True,           # TCP test, not UDP (json.dumps turns True into true)
            "omit": 0,             # don't leave out any seconds at the start
            "time": duration,            # test duration in seconds
            "num": 0,              # 0 = stop by time, not after a byte count
            "blockcount": 0,       # 0 = stop by time, not after a block count
            "parallel": 1,         # one data stream
            "len": block_size,         # 128 KB per write on the data socket (iperf3's TCP default)
            "client_version": "cs422-client",  # informational only; the server never reads it
        }


        # send param json 
        send_json(control_socket, params)

        expect_state(control_socket, TestState.CREATE_STREAMS)
        print("params sent")

        # create the data socket
        data_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        data_socket.settimeout(5.0)
        data_socket.connect((host, port))

        # send cookie
        data_socket.sendall(cookie)

        expect_state(control_socket, TestState.TEST_START)
        print("test start")
        expect_state(control_socket, TestState.TEST_RUNNING)
        print("test running")

        # loop and send blocks

        start = time.monotonic()
        deadline = start + duration
        bytes_sent = 0
        block = bytes(block_size)
        while time.monotonic() < deadline:
            data_socket.sendall(block)
            bytes_sent += block_size
            ready_to_read, _, _ = select.select([control_socket], [], [], 0)
            if ready_to_read:
                state = read_state(control_socket)
                raise_for_state(control_socket, state)
        elapsed = time.monotonic() - start

        #ending TEST_END case like in iperf_server_api.c
        send_state(control_socket, TestState.TEST_END)
        data_socket.close()

        expect_state(control_socket, TestState.EXCHANGE_RESULTS)

        # Client sends its results first
        # get_results() (iperf_api.c:2977) rejects the message if any of these fields is missing.
        client_results = {
            "cpu_util_total": 0, "cpu_util_user": 0, "cpu_util_system": 0,   # not measured
            "sender_has_retransmits": 0,       # becomes 1 once we read TCP_INFO retransmits
            "streams": [{
                "id": 1,                       # server's first stream id (iperf_add_stream())
                "bytes": bytes_sent,
                "retransmits": 0,
                "jitter": 0, "errors": 0, "packets": 0,   # UDP-only fields
                "start_time": 0, "end_time": elapsed,
            }],
        }
        send_json(control_socket, client_results)
        server_results = recv_json(control_socket)

        expect_state(control_socket, TestState.DISPLAY_RESULTS)
        send_state(control_socket, TestState.IPERF_DONE)

        server_bytes = server_results["streams"][0]["bytes"]
        return {
            "host": host,
            "port": port,
            "duration_requested": duration,
            "elapsed": elapsed,            # actual seconds from TEST_RUNNING to end of send loop
            "block_size": block_size,
            "bytes_sent": bytes_sent,      # bytes handed to sendall()
            "server_bytes": server_bytes,  # bytes the server reports receiving
            "cc_requested": cc,            # None until Q2/Q3
            "samples": [],                 # filled with TCP_INFO samples in Lesson 3
        }
    finally:
        data_socket.close()  
        control_socket.close()




r = run_test("195.181.162.195", 5201, duration=10, block_size=BLOCK_SIZE, interval=0.2)
print(f"sent {r['bytes_sent']} B in {r['elapsed']:.2f} s; server received {r['server_bytes']} B "
      f"(difference {r['bytes_sent'] - r['server_bytes']} B)")

