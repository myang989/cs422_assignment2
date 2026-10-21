# first we need to write a function that recieves data from a connected socket
# recv(bufszie) where bufsize is the max number of bytes to read in a single call. 
# so if less data is availabe we return what is there immedialty
# if we want to rexieve an exact ammmount of data
# we need to continue calling recv with the difference of what we needed and what we got.

import socket

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

import secrets


# make the cookie
# create control socket with timeout and connect
# set tcp_nodelay
# send cookie
# read one state byte
# send param json 


# create a socket for ipv4 with a two way stream like tcp
client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

# connect to #iperf3 -c 185.93.1.65 -p 5201
client_socket.connect(("185.93.1.65", 5201))

# receive exactly 1024 bytes of data
data = recv_exact(client_socket, 1024)

# decode bytes for output
print("Received from server:", data.decode("utf-8"))

client_socket.close()