"""
This is a custom iperf3 client that steams data to an iperf3 server to test TCP
"""

import socket
import secrets
import json
import struct
from enum import IntEnum
import time
import select
import csv
import random
import re

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from tcpinfo import read_tcp_info
"""
These states let both parties (client and server) know what is currently happening
"""
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

"""
Some constant values
"""
BLOCK_SIZE = 131072
TCPI_BYTES_ACKED_OFFSET = 120

"""
This function recieves exactly buffersize bytes into a buffer byte array and returns it. 
Nessecary because recv(size) will fill up to size but not nessecarilty the entirety of it.
"""
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

"""
This function generates a secure random cookie string using random chars like iperf_util.cs make_cookie()
Required to establish a connection and allows for multiple parallel streams to link up with the correct control connection.
"""
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

"""
This function reads exactly one state byte
"""
def read_state(socket):
    data = recv_exact(socket, 1)
    # read single byte as a signed int
    return int.from_bytes(data, "big", signed=True)

"""
This function sends the client state to the server (one signed byte)
"""
def send_state(sock, state):
    # One signed byte, like iperf_set_send_state() (iperf_api.c:2064)
    sock.sendall(state.to_bytes(1, "big", signed=True))

"""
Tests the returned state with an expected goal state (for param exchange and further communication).
If we get something we didn't expect we can raise an error.
"""
def expect_state(socket, goal):
    state = read_state(socket)
    if state == goal:
        return 0
    raise_for_state(socket, state, TestState(goal).name)

"""
This defines an iperf error (just wraps a python exception)
"""
class IperfError(Exception):
    """A test against one server failed. The message says why."""

"""
This function raises an error due to faulty state
"""
def raise_for_state(sock, state, expected=None):
    # mirrors the error cases in iperf_handle_message_client() (iperf_client_api.c:303)
    if state == TestState.SERVER_ERROR:
        # two 4-byte big-endian ints follow: iperf error code, then OS errno (cleanup_server())
        (code,) = struct.unpack("!i", recv_exact(sock, 4))
        (err,) = struct.unpack("!i", recv_exact(sock, 4))
        raise IperfError(f"SERVER_ERROR: iperf error code {code}, errno {err}")
    if state == TestState.ACCESS_DENIED:
        raise IperfError("ACCESS_DENIED: server is busy")
    if state == TestState.SERVER_TERMINATE:
        raise IperfError("SERVER_TERMINATE: server ended the test")
    try:
        name = TestState(state).name
    except ValueError:
        name = "unknown"
    raise IperfError(f"unexpected state {state} ({name}), expected {expected}")


"""
This function sends our param json to the server
"""
def send_json(socket, obj):
    # encode our json data dict into a string literal json and then into bytes
    data = json.dumps(obj).encode('utf-8')
    # get the length in bytes of the enocded json
    byte_count = struct.pack("!I", len(data))
    socket.sendall(byte_count)
    socket.sendall(data)

"""
This function recives a param json from the server
"""
def recv_json(sock, max_len=1_000_000):
    # first recv 4-byte big-endian to get the length
    # then recv length many bytes of JSON (JSON_read(), iperf_api.c:3197)
    (length,) = struct.unpack("!I", recv_exact(sock, 4))
    if length == 0 or length > max_len:      # JSON_read() also rejects 0 and oversized lengths
        raise IperfError(f"bad JSON length {length}")
    return json.loads(recv_exact(sock, length).decode())

"""
Reads TCP_INFO from the data socket, stores all fields, and computes per-interval
goodput ( (change in bytes_acked * 8) / change in time) plus retransmission and RTO deltas.
Uses read_tcp_info from tcpinfo.py
"""
def take_sample(sock, start, samples):
    now = time.monotonic()
    info = read_tcp_info(sock)
    if "bytes_acked" not in info:
        raise IperfError(f"TCP_INFO too short ({info['tcp_info_len']} B) for tcpi_bytes_acked")
    sample = {"t_s": now - start, **info, "goodput_bps": None,
              "retrans_delta": None, "bytes_retrans_delta": None, "rto_delta": None}
    if samples:
        prev = samples[-1]
        dt = sample["t_s"] - prev["t_s"]
        if dt > 0:
            sample["goodput_bps"] = (info["bytes_acked"] - prev["bytes_acked"]) * 8 / dt
        sample["retrans_delta"] = info.get("total_retrans", 0) - prev.get("total_retrans", 0)
        sample["bytes_retrans_delta"] = info.get("bytes_retrans", 0) - prev.get("bytes_retrans", 0)
        if "total_rto" in info:
            sample["rto_delta"] = info["total_rto"] - prev.get("total_rto", 0)
    samples.append(sample)


"""
Runs a single iperf3 test on a given (host, port) tuple
"""
def run_test(host, port, duration, block_size, interval, cc=None):
    
    # part 1 - connect and send cookie
    
    # make the cookie
    cookie = make_cookie()
    assert len(cookie) == 37

    # create a socket for ipv4 with a two way stream like tcp
    control_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    data_socket = None
    try:
        control_socket.settimeout(5.0)

        # set tcp no delay
        # disable nagles algo to send data immediatly w/o buffering delays (we deliver immediatly even if the paylod is small)
        # with nagle on if we send a small message, we don't send a next unless we get an ACK but that ACK might be delayed by the server!
        control_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

        control_socket.connect((host, port))

        # send cookie
        control_socket.sendall(cookie)

        # part 1 - parameter exchange

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
        if cc is not None:
            data_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_CONGESTION, cc.encode())
        raw_cc = data_socket.getsockopt(socket.IPPROTO_TCP, socket.TCP_CONGESTION, 16)
        cc_used = raw_cc.split(b"\x00", 1)[0].decode()
        data_socket.connect((host, port))

        # send cookie
        data_socket.sendall(cookie)

        # part 3 - test and data tranmission
        expect_state(control_socket, TestState.TEST_START)
        print("test start")
        expect_state(control_socket, TestState.TEST_RUNNING)
        print("test running")

        # loop and send blocks
        start = time.monotonic()
        deadline = start + duration
        bytes_sent = 0
        block = bytes(block_size)
        samples = []
        take_sample(data_socket, start, samples)
        next_sample = start + interval
        while time.monotonic() < deadline:
            data_socket.sendall(block)
            bytes_sent += block_size
            if time.monotonic() >= next_sample:
                take_sample(data_socket, start, samples)
                next_sample += interval
            ready_to_read, _, _ = select.select([control_socket], [], [], 0)
            if ready_to_read:
                state = read_state(control_socket)
                raise_for_state(control_socket, state)
        elapsed = time.monotonic() - start
        
        final_info = read_tcp_info(data_socket)

        # part 4  - tear down
        
        #ending TEST_END case like in iperf_server_api.c
        send_state(control_socket, TestState.TEST_END)
        data_socket.close()

        expect_state(control_socket, TestState.EXCHANGE_RESULTS)

        # Client sends its results first
        # get_results() (iperf_api.c:2977) rejects the message if any of these fields is missing.
        client_results = {
            "cpu_util_total": 0, "cpu_util_user": 0, "cpu_util_system": 0,   # not measured
            "sender_has_retransmits": 1,       # we report tcpi_total_retrans, like iperf3 on Linux
            "streams": [{
                "id": 1,                       # server's first stream id (iperf_add_stream())
                "bytes": bytes_sent,
                "retransmits": final_info.get("total_retrans", 0),
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
            "cc_used": cc_used,
            "total_retrans": final_info.get("total_retrans"),
            "samples": samples,
        }
    finally:
        if data_socket is not None:
            data_socket.close()
        control_socket.close()

"""
Helper function to format ports
"""
def parse_ports(field, default_port):
    field = (field or "").strip()
    m = re.match(r'^(\d+)\s*-\s*(\d+)$', field)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        return list(range(lo, hi + 1))
    if field.isdigit():
        return [int(field)]
    return [default_port]

"""
Helper function to read servers and ports from csv
"""
def load_servers(path, host_column, port_column, default_port):
    servers = []
    with open(path, newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            ip = (row.get(host_column) or "").strip()
            if not re.match(r'^(\d{1,3}\.){3}\d{1,3}$', ip):
                continue
            servers.append((ip, parse_ports(row.get(port_column), default_port)))
    return servers

"""
Run an iperf test on n servers
"""
def select_and_run(servers, n, max_attempts, duration, block_size, interval, cc, attempts_log):
    successes = []
    for attempt, (host, ports) in enumerate(servers[:max_attempts], start=1):
        if len(successes) >= n:
            break
        port = random.choice(ports)
        t0 = time.monotonic()
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
        print(f"[{attempt}] testing {host}:{port} ...", flush=True)
        try:
            result = run_test(host, port, duration, block_size, interval, cc)
        except (IperfError, OSError, ValueError, KeyError) as e:
            reason = f"{type(e).__name__}: {e}"
            attempts_log.append({"attempt": attempt, "timestamp": stamp, "host": host, "port": port,
                                 "outcome": "fail", "reason": reason,
                                 "attempt_s": round(time.monotonic() - t0, 3)})
            print(f"[{attempt}] FAIL {host}:{port} - {reason}")
            continue
        attempts_log.append({"attempt": attempt, "timestamp": stamp, "host": host, "port": port,
                             "outcome": "ok", "reason": "",
                             "attempt_s": round(time.monotonic() - t0, 3)})
        successes.append(result)
        print(f"[{attempt}] OK   {host}:{port} - {len(successes)}/{n}")
    return successes
