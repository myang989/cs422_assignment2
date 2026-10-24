"""plots (Q1 goodput, Q2/Q3 time series and scatter plots)"""
from collections import OrderedDict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

TIME_SERIES = [
    ("snd_cwnd", "cwnd (segments)"),
    ("srtt_ms", "SRTT (ms)"),
    ("retrans_delta", "retransmitted segs / interval"),
    ("goodput_mbps", "goodput (Mbit/s)"),
]

SCATTERS = [
    ("snd_cwnd", "cwnd (segments)"),
    ("srtt_ms", "SRTT (ms)"),
    ("retrans_delta", "retransmitted segs / interval"),
]


def derive(sample):
    """Add unit-converted copies of the fields the plots use."""
    s = dict(sample)
    s["srtt_ms"] = s["srtt_us"] / 1000 if s.get("srtt_us") is not None else None
    s["rttvar_ms"] = s["rttvar_us"] / 1000 if s.get("rttvar_us") is not None else None
    s["min_rtt_ms"] = s["min_rtt_us"] / 1000 if s.get("min_rtt_us") is not None else None
    s["goodput_mbps"] = s["goodput_bps"] / 1e6 if s.get("goodput_bps") is not None else None
    s["pacing_mbps"] = s["pacing_rate"] * 8 / 1e6 if s.get("pacing_rate") is not None else None
    s["delivery_mbps"] = s["delivery_rate"] * 8 / 1e6 if s.get("delivery_rate") is not None else None
    return s


def _series(result, key):
    pts = [(s["t_s"], s[key]) for s in map(derive, result["samples"]) if s.get(key) is not None]
    return ([p[0] for p in pts], [p[1] for p in pts]) if pts else ([], [])


def _pairs(result, xkey):
    pts = [(s[xkey], s["goodput_mbps"]) for s in map(derive, result["samples"])
           if s.get(xkey) is not None and s.get("goodput_mbps") is not None]
    return ([p[0] for p in pts], [p[1] for p in pts]) if pts else ([], [])


def _rto_times(result):
    return [s["t_s"] for s in result["samples"] if s.get("rto_delta")]


def _data_max(ax, axis):
    vals = []
    for line in ax.get_lines():
        vals.extend(line.get_ydata() if axis == "y" else line.get_xdata())
    for coll in ax.collections:
        offs = coll.get_offsets()
        if len(offs):
            vals.extend(offs[:, 1] if axis == "y" else offs[:, 0])
    vals = [float(v) for v in vals]
    return max(vals) if vals else 0.0


def _zero_limits(axes, axis):
    top = max(_data_max(a, axis) for a in axes) or 1.0
    for a in axes:
        (a.set_ylim if axis == "y" else a.set_xlim)(0, top * 1.05)


def _label(result):
    return f'run {result.get("rep", 1)}'


def _draw_timeseries(axes_col, results, show_legend):
    for r in results:
        for ax, (key, _ylabel) in zip(axes_col, TIME_SERIES):
            t, v = _series(r, key)
            if key == "retrans_delta":
                line, = ax.step(t, v, where="post", linewidth=1, label=_label(r))
                rto = _rto_times(r)
                if rto:
                    ax.plot(rto, [0] * len(rto), "x", color=line.get_color(), markersize=7)
            else:
                ax.plot(t, v, linewidth=1, label=_label(r))
    for ax, (_key, ylabel) in zip(axes_col, TIME_SERIES):
        ax.set_ylabel(ylabel, fontsize=8)
        ax.grid(alpha=0.3)
    axes_col[-1].set_xlabel("time since TEST_RUNNING (s)")
    if show_legend and len(results) > 1:
        axes_col[0].legend(fontsize=7)


def _draw_scatter(axes_col, results, show_legend):
    for r in results:
        for ax, (key, _xlabel) in zip(axes_col, SCATTERS):
            x, y = _pairs(r, key)
            ax.scatter(x, y, s=6, alpha=0.6, label=_label(r))
    for ax, (_key, xlabel) in zip(axes_col, SCATTERS):
        ax.set_xlabel(xlabel, fontsize=8)
        ax.set_ylabel("goodput (Mbit/s)", fontsize=8)
        ax.grid(alpha=0.3)
    if show_legend and len(results) > 1:
        axes_col[0].legend(fontsize=7)


def plot_q2(result, ts_path, sc_path):
    title = f'{result["host"]}:{result["port"]}  ({result["cc_used"]})'
    fig, axes = plt.subplots(len(TIME_SERIES), 1, figsize=(8, 9), sharex=True)
    _draw_timeseries(list(axes), [result], False)
    for ax in axes:
        _zero_limits([ax], "y")
    fig.suptitle(f"TCP stats over time - {title}\n(x on the retransmission panel = RTO timeout)", fontsize=10)
    fig.tight_layout()
    fig.savefig(ts_path)
    plt.close(fig)

    fig, axes = plt.subplots(1, len(SCATTERS), figsize=(13, 4), sharey=True)
    _draw_scatter(list(axes), [result], False)
    _zero_limits(list(axes), "y")
    for ax in axes:
        _zero_limits([ax], "x")
    fig.suptitle(f"Goodput vs TCP stats per sample - {title}", fontsize=10)
    fig.tight_layout()
    fig.savefig(sc_path)
    plt.close(fig)


def plot_q3(results_by_alg, ts_path, sc_path, dest_label):
    algs = list(results_by_alg)
    fig, axes = plt.subplots(len(TIME_SERIES), len(algs), figsize=(4.5 * len(algs), 9),
                             sharex=True, sharey="row", squeeze=False)
    for j, alg in enumerate(algs):
        _draw_timeseries(list(axes[:, j]), results_by_alg[alg], j == 0)
        axes[0, j].set_title(alg)
    for i in range(axes.shape[0]):
        _zero_limits(list(axes[i, :]), "y")
    fig.suptitle(f"TCP stats over time by algorithm - {dest_label}\n"
                 "(same y-scale per row; x on the retransmission row = RTO timeout)", fontsize=10)
    fig.tight_layout()
    fig.savefig(ts_path)
    plt.close(fig)

    fig, axes = plt.subplots(len(SCATTERS), len(algs), figsize=(4.5 * len(algs), 9),
                             sharex="row", sharey=True, squeeze=False)
    for j, alg in enumerate(algs):
        _draw_scatter(list(axes[:, j]), results_by_alg[alg], j == 0)
        axes[0, j].set_title(alg)
    _zero_limits(list(axes.flat), "y")
    for i in range(axes.shape[0]):
        _zero_limits(list(axes[i, :]), "x")
    fig.suptitle(f"Goodput vs TCP stats by algorithm - {dest_label} (same scales per row)", fontsize=10)
    fig.tight_layout()
    fig.savefig(sc_path)
    plt.close(fig)


def plot_goodput_grid(results, path, title):
    by_dest = OrderedDict()
    for r in results:
        by_dest.setdefault((r["host"], r["port"]), []).append(r)
    k = len(by_dest)
    if k == 0:
        return
    ncols = 2 if k > 1 else 1
    nrows = (k + ncols - 1) // ncols
    fig, axes = plt.subplots(nrows, ncols, figsize=(6 * ncols, 2.8 * nrows), squeeze=False, sharex=True)
    for ax in axes.flat[k:]:
        ax.set_visible(False)
    for ax, ((host, port), rs) in zip(axes.flat, by_dest.items()):
        for r in rs:
            t, g = _series(r, "goodput_mbps")
            ax.plot(t, g, linewidth=1, label=_label(r))
        ax.set_title(f"{host}:{port} ({rs[0]['cc_used']})", fontsize=9)
        ax.set_ylabel("goodput (Mbit/s)", fontsize=8)
        _zero_limits([ax], "y")
        ax.grid(alpha=0.3)
        if len(rs) > 1:
            ax.legend(fontsize=6)
    for ax in axes[-1]:
        ax.set_xlabel("time since TEST_RUNNING (s)")
    fig.suptitle(title, fontsize=10)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
