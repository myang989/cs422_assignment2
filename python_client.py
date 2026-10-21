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


# make the cookie
cookie = make_cookie()
assert len(cookie) == 37
# create a socket for ipv4 with a two way stream like tcp
control_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
control_socket.settimeout(5.0)

# set tcp no delay
# disable nagles algo to send data immediatly w/o buffering delays (we deliver immediatly even if the paylod is small)
# with nagle on if we send a small message, we don't send a next unless we get an ACK but that ACK might be delayed by the server! 
control_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

# connect to #iperf3 -c 185.93.1.65 -p 5201
control_socket.connect(("195.181.162.195", 5201))

# send cookie
control_socket.sendall(cookie)

# read one state byte and expect param_exchange
expect_state(control_socket, TestState.PARAM_EXCHANGE)

print("ok")
params = {
    "tcp": True,           # TCP test, not UDP (json.dumps turns True into true)
    "omit": 0,             # don't leave out any seconds at the start
    "time": 10,            # test duration in seconds
    "num": 0,              # 0 = stop by time, not after a byte count
    "blockcount": 0,       # 0 = stop by time, not after a block count
    "parallel": 1,         # one data stream
    "len": 131072,         # 128 KB per write on the data socket (iperf3's TCP default)
    "client_version": "cs422-client",  # informational only; the server never reads it
}


# send param json 
send_json(control_socket, params)

expect_state(control_socket, TestState.CREATE_STREAMS)
print("params sent")

# create the data socket
data_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
data_socket.settimeout(5.0)
data_socket.connect(("195.181.162.195", 5201))

# send cookie
data_socket.sendall(cookie)

expect_state(control_socket, TestState.TEST_START)
print("test start")
expect_state(control_socket, TestState.TEST_RUNNING)
print("test running")



data_socket.close()
control_socket.close()