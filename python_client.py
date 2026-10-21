# first we need to write a function that recieves data from a connected socket
# recv(bufszie) where bufsize is the max number of bytes to read in a single call. 
# so if less data is availabe we return what is there immedialty
# if we want to rexieve an exact ammmount of data
# we need to continue calling recv with the difference of what we needed and what we got.

import socket
import secrets

PARAM_EXCHANGE = 9
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

# make the cookie
cookie = make_cookie()
assert len(cookie) == 37
# create a socket for ipv4 with a two way stream like tcp
client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
client_socket.settimeout(5.0)

# set tcp no delay
# disable nagles algo to send data immediatly w/o buffering delays (we deliver immediatly even if the paylod is small)
# with nagle on if we send a small message, we don't send a next unless we get an ACK but that ACK might be delayed by the server! 
client_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

# connect to #iperf3 -c 185.93.1.65 -p 5201
client_socket.connect(("195.181.162.195", 5201))

# send cookie
client_socket.sendall(cookie)

# read one state byte
state = read_state(client_socket)

#check for paramexchange (9)
if state == 9:
    # ok
    print("ok")
elif state == -1:
    # server busy
    print("busy")
elif state == -2:
    # server error
    print("server error")
else:
    # protocol mismatch
    print("general error")

# send param json 

# receive exactly 1024 bytes of data
#data = recv_exact(client_socket, 1024)

# decode bytes for output
#print("Received from server:", data.decode("utf-8"))

client_socket.close()