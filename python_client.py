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

