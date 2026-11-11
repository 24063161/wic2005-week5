#!/usr/bin/env python3
r"""Week 5 lab: two P4 switches (BMv2) in Mininet, both running tunnel.p4.

    h1 ---- s1 ---- s2 ---- h2   10.NN.0.2   tunnel exit 2
                      \---- h3   10.NN.0.3   tunnel exit 3
    h1: 10.NN.0.1, tunnel exit 1

NN is the last 2 digits of your student ID, without a leading zero.

Compiles tunnel.p4, starts the network, fills the switches' tables from
s1-commands.txt and s2-commands.txt (with your NN filled in), starts receive.py
on h2 and h3 (output in logs/h2.txt and logs/h3.txt), then opens the Mininet CLI.

    python3 topo.py --id NNNN        (the last 4 digits of your student ID)

For the assignment's mixed-rollout experiment, s2 can run a different program:

    python3 topo.py --id NNNN --s2-program old.p4
"""
import argparse
import json
import os
import socket
import subprocess
import sys
import time

from mininet.cli import CLI
from mininet.log import info, setLogLevel
from mininet.net import Mininet
from mininet.node import Switch

PROGRAM = "tunnel.p4"
BUILD = "build/tunnel.json"
THRIFT_PORT = {"s1": 9090, "s2": 9091}
# The compiled program each switch runs. Normally both run tunnel.p4; --s2-program changes s2's.
SWITCH_BUILD = {"s1": BUILD, "s2": BUILD}


class BMv2Switch(Switch):
    """A Mininet switch that runs BMv2's simple_switch with a compiled P4 program."""

    def start(self, controllers):
        intfs = {port: intf.name for intf, port in self.ports.items() if intf.name != "lo"}
        for name in intfs.values():  # keep IPv6 chatter off the switch's own ports
            self.cmd(f"sysctl -qw net.ipv6.conf.{name}.disable_ipv6=1")
        ports = " ".join(f"-i {port}@{name}" for port, name in intfs.items())
        thrift = THRIFT_PORT[self.name]
        self.cmd(
            f"simple_switch --device-id {thrift - 9090} --thrift-port {thrift} "
            f"--log-file logs/{self.name} --log-flush {ports} {SWITCH_BUILD[self.name]} > logs/{self.name}.out 2>&1 &"
        )
        for _ in range(100):  # wait until the switch accepts table commands
            with socket.socket() as s:
                if s.connect_ex(("127.0.0.1", thrift)) == 0:
                    return
            time.sleep(0.1)
        sys.exit(f"{self.name} did not start: see logs/{self.name}.out")

    def stop(self, deleteIntfs=True):
        self.cmd(f"pkill -f 'thrift-port {THRIFT_PORT[self.name]}'")
        super().stop(deleteIntfs)


def compile_program(program=PROGRAM, build=BUILD):
    os.makedirs("build", exist_ok=True)
    os.makedirs("logs", exist_ok=True)
    info(f"*** Compiling {program}\n")
    result = subprocess.run(["p4c-bm2-ss", "-o", build, program], capture_output=True, text=True)
    if result.stderr.strip():
        print(result.stderr.strip())
    if result.returncode != 0:
        sys.exit(f"Compilation failed — fix {program} and run topo.py again.")


def load_tables(switch, nn):
    """Send <switch>-commands.txt to the switch, skipping tables the program doesn't use.

    Addresses in the files are written 10.NN.…; NN is replaced with yours.
    """
    with open(SWITCH_BUILD[switch]) as f:
        tables = {t["name"] for p in json.load(f)["pipelines"] for t in p["tables"]}
    lines, skipped = [], set()
    with open(f"{switch}-commands.txt") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            line = line.replace("10.NN.", f"10.{nn}.")
            table = line.split()[1]
            if table in tables:
                lines.append(line)
            else:
                skipped.add(table)
    for table in sorted(skipped):
        info(f"*** {switch}: {table} isn't used by the program (yet), so its entries were skipped\n")
    out = subprocess.run(
        ["simple_switch_CLI", "--thrift-port", str(THRIFT_PORT[switch])],
        input="\n".join(lines) + "\n", capture_output=True, text=True,
    ).stdout
    errors = [l for l in out.splitlines() if "Error" in l or "Invalid" in l]
    info(f"*** {switch}: {len(lines)} table entries added\n")
    for e in errors:
        print(f"    {e}")


def parse_args():
    ap = argparse.ArgumentParser(description="Week 5: two P4 switches on your own addresses.")
    ap.add_argument("--id", required=True, metavar="NNNN", help="the last 4 digits of your student ID")
    ap.add_argument("--s2-program", metavar="FILE", help="run this P4 program on s2 instead of tunnel.p4")
    args = ap.parse_args()
    if len(args.id) != 4 or not args.id.isdigit():
        ap.error("--id must be the last 4 digits of your student ID, e.g. --id 4207")
    return int(args.id[2:]), args.s2_program


def main():
    nn, s2_program = parse_args()
    setLogLevel("info")
    compile_program()
    if s2_program:
        SWITCH_BUILD["s2"] = "build/s2.json"
        compile_program(s2_program, SWITCH_BUILD["s2"])
    net = Mininet(controller=None, autoSetMacs=True)
    h1 = net.addHost("h1", ip=f"10.{nn}.0.1/24")
    h2 = net.addHost("h2", ip=f"10.{nn}.0.2/24")
    h3 = net.addHost("h3", ip=f"10.{nn}.0.3/24")
    s1 = net.addSwitch("s1", cls=BMv2Switch)
    s2 = net.addSwitch("s2", cls=BMv2Switch)
    net.addLink(h1, s1)  # s1 port 1
    net.addLink(s1, s2)  # s1 port 2, s2 port 1
    net.addLink(s2, h2)  # s2 port 2
    net.addLink(s2, h3)  # s2 port 3
    net.build()
    # Before the switches start, so IPv6 chatter doesn't clutter their logs.
    for h in (h1, h2, h3):
        h.cmd("sysctl -qw net.ipv6.conf.all.disable_ipv6=1 net.ipv6.conf.default.disable_ipv6=1")
    net.start()
    net.staticArp()  # the P4 program doesn't handle ARP, so hosts know each other's MACs
    for s in ("s1", "s2"):
        load_tables(s, nn)
    for h in (h2, h3):  # print what arrives at h2 and h3 into logs/h2.txt and logs/h3.txt
        h.cmd(f"python3 receive.py > logs/{h.name}.txt 2>&1 &")
    info(f"*** Your hosts: h1 10.{nn}.0.1, h2 10.{nn}.0.2, h3 10.{nn}.0.3\n")
    if s2_program:
        info(f"*** Mixed rollout: s1 runs {PROGRAM}, s2 runs {s2_program}\n")
    info("*** Ready. Switch logs: logs/s1.txt, logs/s2.txt. What h2 and h3 receive: logs/h2.txt, logs/h3.txt\n")
    CLI(net)
    net.stop()


if __name__ == "__main__":
    main()
