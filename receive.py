#!/usr/bin/env python3
"""Print every lab packet that arrives at this host (run it in the background on a host)."""
from scapy.all import IP, UDP, Ether, Packet, Raw, ShortField, bind_layers, get_if_list, sniff


class MyTunnel(Packet):
    name = "MyTunnel"
    fields_desc = [ShortField("proto_id", 0x0800), ShortField("dst_id", 0)]


bind_layers(Ether, MyTunnel, type=0x1212)
bind_layers(MyTunnel, IP, proto_id=0x0800)


def show(pkt):
    if UDP in pkt and pkt[UDP].dport == 4321:
        how = f"tunnelled, dst_id={pkt[MyTunnel].dst_id}" if MyTunnel in pkt else "plain IPv4"
        msg = pkt[Raw].load.decode(errors="replace") if Raw in pkt else ""
        print(f"Received ({how}) from {pkt[IP].src} to {pkt[IP].dst}, TTL {pkt[IP].ttl}: {msg!r}", flush=True)


iface = next(i for i in get_if_list() if i.endswith("-eth0"))
print(f"Listening on {iface}...", flush=True)
sniff(iface=iface, prn=show, store=False)
