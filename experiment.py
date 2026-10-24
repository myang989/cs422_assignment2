"""
This is the entry point for Assignement 2 expirements

Part 1 - iperf throughput application
We pick n random iperf3 servers and run one test per server using our custom client with the OS
default congestion control, logging goodput 

Part 2 - While our cleint is sending data, in addition to sampling goodput, we record TCP_INFO stats.

Part 3 - We run the same test as part 1 and part 2 collecting good put and TCP_INFO across the
following congestion control algorithms: CUBIC, Reno, and BRR.
"""
import argparse
import csv
import json
import os
import platform
import random
import socket
import sys
import time

import matplotlib
import numpy as np

import plots
from q1 import IperfError, load_servers, run_test, select_and_run
from tcpinfo import TCP_INFO_FIELDS

META_COLUMNS = [
    ("run_id", "", "experiment run identifier (start time)"),
    ("phase", "", "q1q2 = destination selection with OS default CC; q3 = algorithm comparison"),
    ("rep", "", "repetition number within the phase"),
    ("host", "", "iperf3 server address"),
    ("port", "", "iperf3 server port"),
    ("cc_requested", "", "algorithm set with TCP_CONGESTION (empty = OS default)"),
    ("cc_used", "", "algorithm read back with getsockopt(TCP_CONGESTION)"),
    ("interval_s", "s", "configured sampling interval"),
    ("t_s", "s", "sample time since TEST_RUNNING (time.monotonic)"),
]
DERIVED_COLUMNS = [
    ("app_limited", "flag", "tcpi_delivery_rate_app_limited bit"),
    ("tcp_info_len", "bytes", "length of struct tcp_info returned by this kernel"),
    ("goodput_bps", "bit/s", "(bytes_acked - previous bytes_acked) * 8 / (t_s - previous t_s)"),
    ("retrans_delta", "segments", "loss proxy: total_retrans - previous total_retrans"),
    ("bytes_retrans_delta", "bytes", "bytes_retrans - previous bytes_retrans"),
    ("rto_delta", "count", "RTO timeouts in the interval: total_rto - previous total_rto"),
]
SAMPLE_COLUMNS = ([c[0] for c in META_COLUMNS] + [f[0] for f in TCP_INFO_FIELDS]
                  + [c[0] for c in DERIVED_COLUMNS])
TEST_COLUMNS = ["run_id", "phase", "rep", "order", "start_time", "host", "port", "cc_requested",
                "cc_used", "outcome", "reason", "duration_requested", "elapsed_s", "block_size",
                "bytes_sent", "server_bytes", "total_retrans"]
SUMMARY_COLUMNS = ["phase", "rep", "host", "port", "cc_used", "n_samples",
                   "goodput_min_mbps", "goodput_median_mbps", "goodput_mean_mbps", "goodput_p95_mbps",
                   "srtt_median_ms", "srtt_p95_ms", "min_rtt_ms", "cwnd_median_segs",
                   "total_retrans_segs", "retrans_pct_bytes"]
AGG_COLUMNS = ["host", "port", "cc", "runs", "goodput_mean_mbps", "goodput_mean_std_mbps",
               "goodput_median_mbps", "goodput_p95_mbps", "srtt_median_ms", "srtt_p95_ms",
               "min_rtt_ms", "retrans_pct_bytes_mean"]


class CsvAppender:
    def __init__(self, path, columns):
        self.path, self.columns = path, columns
        with open(path, "w", newline="") as f:
            csv.DictWriter(f, fieldnames=columns).writeheader()

    def add(self, rows):
        with open(self.path, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=self.columns, extrasaction="ignore")
            for row in rows:
                w.writerow({k: ("" if v is None else v) for k, v in row.items()})


def write_csv(path, rows, columns):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def read_sysctl(name):
    try:
        with open("/proc/sys/" + name.replace(".", "/")) as f:
            return f.read().strip()
    except OSError:
        return None


def check_algorithm(alg):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_CONGESTION, alg.encode())
        return None
    except OSError as e:
        return str(e)
    finally:
        s.close()


def sample_rows(result, run_id, interval):
    base = {"run_id": run_id, "phase": result["phase"], "rep": result["rep"], "host": result["host"],
            "port": result["port"], "cc_requested": result["cc_requested"] or "",
            "cc_used": result["cc_used"], "interval_s": interval}
    return [{**base, **s, "t_s": round(s["t_s"], 6)} for s in result["samples"]]


def pct(values, q):
    return float(np.percentile(values, q)) if len(values) else None


def test_summary(r):
    s = [plots.derive(x) for x in r["samples"]]
    g = [x["goodput_mbps"] for x in s if x["goodput_mbps"] is not None]
    rtt = [x["srtt_ms"] for x in s if x["srtt_ms"] is not None]
    cwnd = [x["snd_cwnd"] for x in s if x.get("snd_cwnd") is not None]
    last = s[-1] if s else {}
    sent = last.get("bytes_sent") or 0
    rnd = lambda v: None if v is None else round(v, 3)
    return {"phase": r["phase"], "rep": r["rep"], "host": r["host"], "port": r["port"],
            "cc_used": r["cc_used"], "n_samples": len(g),
            "goodput_min_mbps": rnd(min(g) if g else None), "goodput_median_mbps": rnd(pct(g, 50)),
            "goodput_mean_mbps": rnd(float(np.mean(g)) if g else None), "goodput_p95_mbps": rnd(pct(g, 95)),
            "srtt_median_ms": rnd(pct(rtt, 50)), "srtt_p95_ms": rnd(pct(rtt, 95)),
            "min_rtt_ms": rnd(last.get("min_rtt_ms")), "cwnd_median_segs": rnd(pct(cwnd, 50)),
            "total_retrans_segs": r.get("total_retrans"),
            "retrans_pct_bytes": rnd(100 * (last.get("bytes_retrans") or 0) / sent if sent else None)}


def aggregate(summaries, algorithms):
    rows = []
    keys = []
    for s in summaries:
        k = (s["host"], s["port"])
        if k not in keys:
            keys.append(k)
    groups = [(k, alg) for k in keys for alg in algorithms] + [(("ALL", ""), alg) for alg in algorithms]
    for (host, port), alg in groups:
        sel = [s for s in summaries if s["cc_used"] == alg and (host == "ALL" or (s["host"], s["port"]) == (host, port))]
        sel = [s for s in sel if s["goodput_mean_mbps"] is not None]
        if not sel:
            continue
        means = [s["goodput_mean_mbps"] for s in sel]
        rnd = lambda v: None if v is None else round(float(v), 3)
        rows.append({"host": host, "port": port, "cc": alg, "runs": len(sel),
                     "goodput_mean_mbps": rnd(np.mean(means)),
                     "goodput_mean_std_mbps": rnd(np.std(means, ddof=1) if len(means) > 1 else 0.0),
                     "goodput_median_mbps": rnd(np.median([s["goodput_median_mbps"] for s in sel])),
                     "goodput_p95_mbps": rnd(np.median([s["goodput_p95_mbps"] for s in sel])),
                     "srtt_median_ms": rnd(np.median([s["srtt_median_ms"] for s in sel if s["srtt_median_ms"] is not None])),
                     "srtt_p95_ms": rnd(np.median([s["srtt_p95_ms"] for s in sel if s["srtt_p95_ms"] is not None])),
                     "min_rtt_ms": rnd(np.min([s["min_rtt_ms"] for s in sel if s["min_rtt_ms"] is not None])),
                     "retrans_pct_bytes_mean": rnd(np.mean([s["retrans_pct_bytes"] or 0 for s in sel]))})
    return rows


def format_table(rows, columns):
    if not rows:
        return "(no rows)\n"
    widths = [max(len(c), *(len(str(r.get(c, ""))) for r in rows)) for c in columns]
    line = lambda vals: "  ".join(str(v).rjust(w) for v, w in zip(vals, widths))
    out = [line(columns)] + [line(["" if r.get(c) is None else r.get(c) for c in columns]) for r in rows]
    return "\n".join(out) + "\n"


def run_fixed(host, port, cc, rep, args, run_id, order, tests_csv):
    for attempt in range(args.retries + 1):
        start_time = time.strftime("%Y-%m-%dT%H:%M:%S")
        print(f"[q3 rep {rep} #{order}] {cc:>6} {host}:{port} (try {attempt + 1}) ...", flush=True)
        try:
            r = run_test(host, port, args.duration, args.block_size, args.interval, cc)
        except (IperfError, OSError, ValueError, KeyError) as e:
            reason = f"{type(e).__name__}: {e}"
            print(f"    FAIL {reason}", flush=True)
            tests_csv.add([{"run_id": run_id, "phase": "q3", "rep": rep, "order": order,
                            "start_time": start_time, "host": host, "port": port, "cc_requested": cc,
                            "outcome": "fail", "reason": reason}])
            if attempt < args.retries:
                time.sleep(args.retry_wait)
            continue
        r.update(phase="q3", rep=rep)
        tests_csv.add([{"run_id": run_id, "phase": "q3", "rep": rep, "order": order,
                        "start_time": start_time, "host": host, "port": port, "cc_requested": cc,
                        "cc_used": r["cc_used"], "outcome": "ok", "duration_requested": r["duration_requested"],
                        "elapsed_s": round(r["elapsed"], 3), "block_size": r["block_size"],
                        "bytes_sent": r["bytes_sent"], "server_bytes": r["server_bytes"],
                        "total_retrans": r["total_retrans"]}])
        return r
    return None


def main():
    p = argparse.ArgumentParser(description="CS 422 Assignment 2: iperf3 goodput, TCP stats, CC comparison")
    p.add_argument("--servers", default="listed_iperf3_servers.csv", help="server-list CSV")
    p.add_argument("--host-column", default="IP/HOST")
    p.add_argument("--port-column", default="PORT")
    p.add_argument("--default-port", type=int, default=5201)
    p.add_argument("--dest-file", default=None,
                   help="CSV with host,port columns (e.g. a previous chosen_servers.csv); skips random selection")
    p.add_argument("-n", "--num-destinations", type=int, default=10)
    p.add_argument("--max-attempts", type=int, default=None, help="selection attempts (default 3 * n)")
    p.add_argument("-t", "--duration", type=int, default=60, help="seconds per test")
    p.add_argument("-i", "--interval", type=float, default=0.2, help="sampling interval in seconds")
    p.add_argument("-l", "--block-size", type=int, default=131072, help="bytes per write")
    p.add_argument("-r", "--reps", type=int, default=3, help="Q3 repetitions per algorithm and server")
    p.add_argument("--algorithms", default="cubic,reno,bbr", help="comma-separated Q3 algorithms")
    p.add_argument("--skip-q3", action="store_true", help="only run the Q1/Q2 phase")
    p.add_argument("--rep-dest", default=None, help="representative destination host:port for Q2/Q3 plots")
    p.add_argument("--retries", type=int, default=1, help="Q3 retries per test on failure")
    p.add_argument("--retry-wait", type=float, default=15.0, help="seconds before a Q3 retry")
    p.add_argument("--pause", type=float, default=1.0, help="seconds between tests")
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("-o", "--out", default="results")
    args = p.parse_args()

    algorithms = [a.strip() for a in args.algorithms.split(",") if a.strip()]
    seed = args.seed if args.seed is not None else int(time.time())
    random.seed(seed)
    run_id = time.strftime("%Y%m%d-%H%M%S")
    out = os.path.join(args.out, run_id)
    os.makedirs(out, exist_ok=True)

    meta = {"run_id": run_id, "seed": seed, "args": vars(args), "argv": sys.argv,
            "start_time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "kernel_release": platform.release(), "kernel_version": platform.version(),
            "machine": platform.machine(), "python": platform.python_version(),
            "numpy": np.__version__, "matplotlib": matplotlib.__version__,
            "tcp_congestion_control_default": read_sysctl("net.ipv4.tcp_congestion_control"),
            "tcp_available_congestion_control": read_sysctl("net.ipv4.tcp_available_congestion_control"),
            "tcp_allowed_congestion_control": read_sysctl("net.ipv4.tcp_allowed_congestion_control")}
    print(f"run {run_id}  seed {seed}  kernel {meta['kernel_release']}  "
          f"default cc {meta['tcp_congestion_control_default']}", flush=True)

    if not args.skip_q3:
        problems = {alg: err for alg in algorithms if (err := check_algorithm(alg))}
        if problems:
            for alg, err in problems.items():
                print(f"cannot select {alg}: {err}")
            print("On the host run:  sudo modprobe tcp_bbr && "
                  f"sudo sysctl -w net.ipv4.tcp_allowed_congestion_control=\"{' '.join(algorithms)}\"")
            sys.exit(2)

    with open(os.path.join(out, "samples_schema.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["column", "unit", "tcp_info_offset", "description"])
        for name, unit, desc in META_COLUMNS:
            w.writerow([name, unit, "", desc])
        for name, offset, fmt, unit, desc in TCP_INFO_FIELDS:
            w.writerow([name, unit, f"{offset} ({fmt})", desc])
        for name, unit, desc in DERIVED_COLUMNS:
            w.writerow([name, unit, "", desc])

    samples_csv = CsvAppender(os.path.join(out, "samples.csv"), SAMPLE_COLUMNS)
    tests_csv = CsvAppender(os.path.join(out, "tests.csv"), TEST_COLUMNS)

    if args.dest_file:
        servers = load_servers(args.dest_file, "host", "port", args.default_port)
        n, max_attempts = len(servers), len(servers)
    else:
        servers = load_servers(args.servers, args.host_column, args.port_column, args.default_port)
        random.shuffle(servers)
        n, max_attempts = args.num_destinations, args.max_attempts or 3 * args.num_destinations
    per_test = args.duration + 3
    print(f"{len(servers)} servers loaded; target {n} destinations; "
          f"Q1/Q2 phase >= {n * per_test / 60:.0f} min", flush=True)
    if not args.skip_q3:
        print(f"Q3 phase: {len(algorithms)} algorithms x {n} servers x {args.reps} reps "
              f">= {len(algorithms) * n * args.reps * (per_test + args.pause) / 60:.0f} min", flush=True)

    q1_results, q3_results, order = [], [], []
    attempts_log = []
    try:
        q1_results = select_and_run(servers, n, max_attempts, args.duration, args.block_size,
                                    args.interval, None, attempts_log)
        for r in q1_results:
            r.update(phase="q1q2", rep=1)
            samples_csv.add(sample_rows(r, run_id, args.interval))
            tests_csv.add([{"run_id": run_id, "phase": "q1q2", "rep": 1, "host": r["host"], "port": r["port"],
                            "cc_requested": "", "cc_used": r["cc_used"], "outcome": "ok",
                            "duration_requested": r["duration_requested"], "elapsed_s": round(r["elapsed"], 3),
                            "block_size": r["block_size"], "bytes_sent": r["bytes_sent"],
                            "server_bytes": r["server_bytes"], "total_retrans": r["total_retrans"]}])
        write_csv(os.path.join(out, "attempts_q1.csv"), attempts_log,
                  ["attempt", "timestamp", "host", "port", "outcome", "reason", "attempt_s"])
        dests = [(r["host"], r["port"]) for r in q1_results]
        write_csv(os.path.join(out, "chosen_servers.csv"),
                  [{"host": h, "port": pt} for h, pt in dests], ["host", "port"])
        print(f"\nQ1: requested {n}, succeeded {len(q1_results)}, attempts {len(attempts_log)}, "
              f"shortfall {n - len(q1_results)}\n", flush=True)

        if not args.skip_q3:
            k = 0
            for rep in range(1, args.reps + 1):
                shift = (rep - 1) % len(algorithms)
                rep_algs = algorithms[shift:] + algorithms[:shift]
                for host, port in dests:
                    for alg in rep_algs:
                        k += 1
                        order.append({"order": k, "rep": rep, "host": host, "port": port, "cc": alg})
                        r = run_fixed(host, port, alg, rep, args, run_id, k, tests_csv)
                        if r is not None:
                            q3_results.append(r)
                            samples_csv.add(sample_rows(r, run_id, args.interval))
                        time.sleep(args.pause)
    except KeyboardInterrupt:
        print("\ninterrupted - writing summaries and plots for the tests completed so far", flush=True)
        meta["interrupted"] = True

    summaries = [test_summary(r) for r in q1_results + q3_results]
    write_csv(os.path.join(out, "per_test_summary.csv"), summaries, SUMMARY_COLUMNS)
    agg = aggregate([s for s in summaries if s["phase"] == "q3"], algorithms)
    write_csv(os.path.join(out, "q3_aggregate.csv"), agg, AGG_COLUMNS)
    write_csv(os.path.join(out, "q3_order.csv"), order, ["order", "rep", "host", "port", "cc"])

    rep_dest = None
    if args.rep_dest:
        h, _, pt = args.rep_dest.rpartition(":")
        rep_dest = (h, int(pt))
    elif q1_results:
        counts = {(r["host"], r["port"]): 0 for r in q1_results}
        for r in q3_results:
            if (r["host"], r["port"]) in counts:
                counts[(r["host"], r["port"])] += 1
        best = max(counts.values())
        rep_dest = next(d for d in counts if counts[d] == best)
    meta["representative_destination"] = f"{rep_dest[0]}:{rep_dest[1]}" if rep_dest else None

    plots.plot_goodput_grid(q1_results, os.path.join(out, "q1_goodput.pdf"),
                            "Q1: goodput over time per destination (OS default CC)")
    rep_q1 = [r for r in q1_results if rep_dest and (r["host"], r["port"]) == rep_dest]
    if rep_q1:
        plots.plot_q2(rep_q1[0], os.path.join(out, "q2_timeseries.pdf"), os.path.join(out, "q2_scatter.pdf"))
    if q3_results and rep_dest:
        by_alg = {alg: [r for r in q3_results if r["cc_requested"] == alg and (r["host"], r["port"]) == rep_dest]
                  for alg in algorithms}
        by_alg = {a: rs for a, rs in by_alg.items() if rs}
        if by_alg:
            plots.plot_q3(by_alg, os.path.join(out, "q3_timeseries.pdf"), os.path.join(out, "q3_scatter.pdf"),
                          f"{rep_dest[0]}:{rep_dest[1]}")
        for alg in algorithms:
            rs = [r for r in q3_results if r["cc_requested"] == alg]
            if rs:
                plots.plot_goodput_grid(rs, os.path.join(out, f"q3_goodput_{alg}.pdf"),
                                        f"Q3: goodput over time per destination ({alg}, all repetitions)")

    meta["end_time"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    meta["q1_succeeded"] = len(q1_results)
    meta["q3_tests_ok"] = len(q3_results)
    meta["q3_tests_planned"] = len(order)
    with open(os.path.join(out, "meta.json"), "w") as f:
        json.dump(meta, f, indent=2)

    text = (f"run {run_id}  kernel {meta['kernel_release']}  seed {seed}\n"
            f"representative destination: {meta['representative_destination']}\n\n"
            "Per-test summary (goodput in Mbit/s; percentiles use numpy linear interpolation)\n"
            + format_table(summaries, SUMMARY_COLUMNS)
            + "\nQ3 aggregate across repetitions (ALL = every destination)\n"
            + format_table(agg, AGG_COLUMNS))
    with open(os.path.join(out, "summary.txt"), "w") as f:
        f.write(text)
    print("\n" + text)
    print(f"results in {out}")


if __name__ == "__main__":
    main()
