"""Code that decodes Linux struct tcp_info (include/uapi/linux/tcp.h) from getsockopt(TCP_INFO).

Offsets were taken with offsetof() against the kernel UAPI header on Linux 6.x
(x86_64 and arm64 lay this struct out identically). Values are in the host's
native byte order, hence the "=" struct prefix. Older kernels return a shorter
struct; fields past the returned length are left out instead of guessed.
"""
import socket
import struct

TCP_INFO_REQUEST_LEN = 256

# (name, byte offset, struct format, unit, description)
TCP_INFO_FIELDS = [
    ("snd_mss",           16,  "I", "bytes",    "sender maximum segment size"),
    ("unacked",           24,  "I", "segments", "segments sent but not yet acknowledged (in flight)"),
    ("srtt_us",           68,  "I", "us",       "smoothed RTT (SRTT)"),
    ("rttvar_us",         72,  "I", "us",       "RTT variation (RTTVAR)"),
    ("snd_ssthresh",      76,  "I", "segments", "slow-start threshold"),
    ("snd_cwnd",          80,  "I", "segments", "congestion window"),
    ("total_retrans",     100, "I", "segments", "cumulative retransmitted segments"),
    ("pacing_rate",       104, "Q", "bytes/s",  "current pacing rate"),
    ("bytes_acked",       120, "Q", "bytes",    "cumulative bytes acknowledged by the receiver"),
    ("min_rtt_us",        148, "I", "us",       "minimum RTT observed on the connection"),
    ("delivery_rate",     160, "Q", "bytes/s",  "kernel delivery-rate estimate"),
    ("bytes_sent",        200, "Q", "bytes",    "cumulative bytes sent, including retransmissions"),
    ("bytes_retrans",     208, "Q", "bytes",    "cumulative bytes retransmitted"),
    ("total_rto",         240, "H", "count",    "cumulative RTO timeouts (Linux >= 6.7)"),
]


def read_tcp_info(sock):
    raw = sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_INFO, TCP_INFO_REQUEST_LEN)
    info = {"tcp_info_len": len(raw)}
    for name, offset, fmt, _unit, _desc in TCP_INFO_FIELDS:
        size = struct.calcsize("=" + fmt)
        if offset + size <= len(raw):
            (info[name],) = struct.unpack_from("=" + fmt, raw, offset)
    return info