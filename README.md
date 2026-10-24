# CS 422 Assignment 2: iPerf, TCP Statistics, and Congestion Control

A from-scratch Python client for the iperf3 TCP protocol that measures goodput and
TCP_INFO statistics against public iperf3 servers, and compares CUBIC, Reno and BBR.

## Files

| File | Purpose |
|---|---|
| `experiment.py` | **Single entry point.** Runs every phase, writes raw data, tables and plots. |
| `custom_iperf3_client.py` | iperf3 protocol client (`run_test`), server-list loading and destination selection with retries. Also runs standalone for Q1 only. |
| `tcpinfo.py` | Decoder for Linux `struct tcp_info` (`read_tcp_info`) with the field table (offsets, formats, units). |
| `plots.py` | PDF plots: Q1 goodput panels, Q2 and Q3 time series and scatter plots. |
| `listed_iperf3_servers.csv` | Server list from https://iperf3serverlist.net/ (columns `IP/HOST`, `PORT`). |
| `host_setup.sh` | One-time host setup: loads `tcp_bbr` and allows unprivileged selection of reno, cubic and bbr. |
| `Dockerfile` | `FROM ubuntu:24.04` image that runs the full pipeline. |

## Requirements

- Linux host kernel (tested on a Ubuntu 26.04.1 LTS VM).
- The host's network path must be end to end. Inside a VM, use bridged or IP-NAT ("Shared Network")
  networking, not a user-space proxy (UTM "Emulated VLAN", Docker Desktop on macOS).
- Python 3.10+, numpy, matplotlib.

## Reproduce

```sh
./host_setup.sh                                  # once per boot, on the host
docker build -t cs422-a2 .
docker run --rm --network host -v "$PWD/results:/app/results" cs422-a2 \
    -n 5 -t 60 -i 0.2 -r 3 --seed 42
```

Without Docker: `python3 experiment.py -n 5 -t 60 -i 0.2 -r 3 --seed 42`.

`--network host` makes the container use the host's network namespace, so the measured TCP
connection is the host kernel's connection to the server, with no extra NAT layer.

To rerun on the same destinations as an earlier run:
`python3 experiment.py --dest-file results/<run_id>/chosen_servers.csv -t 60 -i 0.2 -r 3`.

## Command-line options

| Option | Default | Meaning |
|---|---|---|
| `--servers` | `listed_iperf3_servers.csv` | Server-list CSV (`--host-column`, `--port-column` set column names; ports may be ranges like `5201-5210`) |
| `-n` | 10 | Number of destinations to select |
| `--max-attempts` | 3 × n | Bound on selection attempts |
| `--dest-file` | none | Use a fixed `host,port` list instead of random selection |
| `-t` | 60 | Test duration per destination (s) |
| `-i` | 0.2 | Sampling interval (s) |
| `-l` | 131072 | Bytes per `sendall()` (also sent to the server as `len`) |
| `-r` | 3 | Q3 repetitions per algorithm and destination |
| `--algorithms` | `cubic,reno,bbr` | Q3 algorithms |
| `--skip-q3` | off | Run only the Q1/Q2 phase |
| `--rep-dest` | auto | Representative destination `host:port` for Q2/Q3 plots |
| `--retries`, `--retry-wait` | 1, 15 s | Q3 retries for a failed test |
| `--pause` | 1 s | Pause between tests |
| `--seed` | time | RNG seed for server shuffling and port choice |
| `-o` | `results` | Output directory; each run writes `results/<run_id>/` |


## AI use

AI was used to generate some of the code used to run expirements. This includes part of the iperf3 client such as helper methods like read_csv and summarize. AI also helped in plots.py and tcpinfo.py, Additaionly AI was used to verify that the code I wrote was bug free and provided assitance to guide me to a more correct answer. Finally I used AI to help me understand the material that the HW assignments cover.