FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-numpy python3-matplotlib \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY custom_iperf3_client.py tcpinfo.py plots.py experiment.py listed_iperf3_servers.csv ./

ENTRYPOINT ["python3", "experiment.py"]
CMD ["-n", "5", "-t", "60", "-i", "0.2", "-r", "3"]
