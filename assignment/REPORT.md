# Week 5 assignment — Put a limit on the tunnel

Student ID (last 4 digits only): **3161**

## 1. Analysis: the loop

The tunnel loop was created on switch s2 by modifying its exact-match tunnel forwarding table `MyIngress.myTunnel_exact`. In the baseline configuration, tunnel exit 3 forwards traffic to port 3 (host h3). Using `./p4cli s2 table_dump MyIngress.myTunnel_exact`, handle 2 was identified as the entry for key 3 (`dst_id = 3`). We modified this entry using:
```bash
sh ./p4cli s2 "table_modify MyIngress.myTunnel_exact MyIngress.myTunnel_forward 2 1"
```
This redirected tunnel exit 3 to port 1, pointing back towards switch s1. Because s1's table already routes exit 3 to port 2 (towards s2), the two switches formed an immediate forwarding loop.

When a test packet was injected from h1 (`h1 python3 send.py 10.61.0.3 "loop" --tunnel 3`), the switch logs captured rapid packet processing. As recorded in `assignment/evidence/loop-before.txt`:
- Initial check: `logs/s1.txt: 40`, `logs/s2.txt: 37`
- 5 seconds later: `logs/s1.txt: 205`, `logs/s2.txt: 202`

The packet count grew continuously at roughly 33 packets per second per switch. Because the custom tunnel header had no hop-count or lifetime field, the packet looped indefinitely between the two software switches without terminating.

In `tunnel.p4`, the IPv4 forwarding action `ipv4_forward` decrements the IP TTL:
```p4
action ipv4_forward(egressSpec_t port) {
    standard_metadata.egress_spec = port;
    hdr.ipv4.ttl = hdr.ipv4.ttl - 1;
}
```
However, this decrement does **not** stop an IPv4 loop in this program. In `tunnel.p4`, the ingress control only checks `hdr.ipv4.isValid()`; there is no conditional check verifying whether `hdr.ipv4.ttl == 0` or `hdr.ipv4.ttl <= 1`. When `hdr.ipv4.ttl` (defined as `bit<8>`) reaches 0 and is decremented by 1, unsigned binary arithmetic wraps around to 255 (`0 - 1 = 255`). Consequently, an IPv4 loop in this program would also loop indefinitely across 255 -> 0 -> 255 cycles without being dropped.

## 2. Design: the hop limit

To resolve the infinite loop vulnerability in the tunnel pipeline, we designed and implemented a hop limit mechanism:

1. **Field Size and Placement**:
   We added `bit<8> hop_limit;` to `myTunnel_t`, positioned at the end of the header after `dst_id`:
   ```p4
   header myTunnel_t {
       bit<16> proto_id;
       bit<16> dst_id;
       bit<8>  hop_limit;
   }
   ```
   An 8-bit size provides a count up to 255 hops, perfectly mirroring standard IP TTL and IPv6 Hop Limit. Placing it after `dst_id` maintains the byte offsets of the core routing keys (`proto_id` at bytes 0-1 and `dst_id` at bytes 2-3), ensuring structural consistency and partial backward compatibility with legacy parsers.

2. **Starting Value**:
   In `send.py`, the default starting value is set to `64`, consistent with standard Linux operating system network stack defaults. A CLI argument `--hops` was implemented to allow testing arbitrary starting hop limits (e.g. `--hops 2`).

3. **Decrement and Expiration Logic**:
   - Decrement location: In `action myTunnel_forward`, executed whenever a tunnel entry matches:
     ```p4
     hdr.myTunnel.hop_limit = hdr.myTunnel.hop_limit - 1;
     ```
   - Expiration check: In `MyIngress` `apply` block before table matching:
     ```p4
     if (hdr.myTunnel.isValid()) {
         if (hdr.myTunnel.hop_limit == 0) {
             drop();
         } else {
             myTunnel_exact.apply();
         }
     }
     ```
     If a packet arrives with `hop_limit == 0`, it is immediately dropped via `mark_to_drop(standard_metadata)` without undergoing table lookup or transmission.

4. **Coordinated Path Modifications**:
   - `tunnel.p4`: Header updated; parser extracts the 5-byte header; deparser emits the valid header; ingress checks zero and decrements.
   - `send.py`: Packet class updated with `ByteField("hop_limit", 64)`; `--hops` argument added to inject the field.
   - `receive.py`: Packet class updated with `ByteField("hop_limit", 64)`; display logic updated to report the received hop limit.

## 3. Implementation

The implementation required coordinated changes across three files:

1. **`tunnel.p4`**:
   - Extended `myTunnel_t` with `bit<8> hop_limit;`.
   - Updated `myTunnel_forward` to execute `hdr.myTunnel.hop_limit = hdr.myTunnel.hop_limit - 1;`.
   - Updated `MyIngress` `apply` block to check `if (hdr.myTunnel.hop_limit == 0) { drop(); } else { myTunnel_exact.apply(); }`.
   - Deparser and Parser required no state additions since `packet.extract(hdr.myTunnel)` and `packet.emit(hdr.myTunnel)` automatically adjust to the revised header struct size.

2. **`send.py`**:
   - Imported `ByteField` from `scapy.all`.
   - Updated `MyTunnel` definition: `fields_desc = [ShortField("proto_id", 0x0800), ShortField("dst_id", 0), ByteField("hop_limit", 64)]`.
   - Added `--hops` CLI argument (`type=int, default=64`).
   - Attached `MyTunnel(dst_id=args.tunnel, hop_limit=args.hops)` when encapsulating.

3. **`receive.py`**:
   - Imported `ByteField` and mirrored the 3-field `MyTunnel` Scapy definition.
   - Updated output formatting in `show()` to print `hop_limit={pkt[MyTunnel].hop_limit}`.

## 4. Evidence: it works

The implementation was validated through the experiments recorded in `assignment/evidence/`:

1. **Tunnelled Delivery (`assignment/evidence/delivery.txt`)**:
   - Delivering to exit 2 (h2):
     `h1 python3 send.py 10.61.0.2 "to h2" --tunnel 2`
     Receiver log `logs/h2.txt` recorded:
     `Received (tunnelled, dst_id=2, hop_limit=62) from 10.61.0.1 to 10.61.0.2, TTL 64: 'to h2'`
   - Delivering to exit 3 (h3):
     `h1 python3 send.py 10.61.0.3 "to h3" --tunnel 3`
     Receiver log `logs/h3.txt` recorded:
     `Received (tunnelled, dst_id=3, hop_limit=62) from 10.61.0.1 to 10.61.0.3, TTL 64: 'to h3'`
   Both packets started with `hop_limit = 64` and were decremented once by s1 and once by s2 (`64 - 1 - 1 = 62`), proving that hop limits decrement correctly along multi-switch paths.

2. **Plain IPv4 Compatibility (`assignment/evidence/delivery.txt`)**:
   - `h1 ping -c 2 h3` succeeded with 0% loss (`ttl=62`, `rtt min/avg/max/mdev = 48.288/49.994/51.700/1.706 ms`). Non-tunnelled IPv4 traffic remains unaffected.

3. **Loop Termination (`assignment/evidence/loop-after.txt`)**:
   - Testing the C1 loop on a fresh network with default `hop_limit = 64`:
     - Initial check: `logs/s1.txt: 2`, `logs/s2.txt: 2`
     - 5 seconds later: `logs/s1.txt: 33`, `logs/s2.txt: 32`
   The packet count completely stopped increasing. Switch s1 processed 33 times (1 ingress packet from h1 plus 32 loop round-trips), and switch s2 processed 32 times. On the 33rd arrival at s1, `hdr.myTunnel.hop_limit == 0` evaluated to true, and s1 dropped the packet. The 64 total switch traversals match our 64-hop initial value design.

## 5. Evaluation

### a) Mixed Rollout Analysis (`assignment/evidence/mixed.txt`)
In the mixed rollout test, switch s1 ran the updated program (`tunnel.p4`) while switch s2 ran the legacy program (`tunnel_v1.p4`):
- **Prediction**:
  Switch s1 expects a 5-byte tunnel header and emits `hop_limit`. Switch s2 expects a 4-byte tunnel header (`proto_id` and `dst_id`). Because `dst_id` resides at bytes 2-3 in both formats, s2 will correctly extract `dst_id = 2` and forward the packet out of port 2 towards h2.
  However, s2 will begin parsing the subsequent IPv4 header starting at byte 18 instead of byte 19 (offset by 1 byte). The 1-byte `hop_limit` will be mistakenly consumed as the start of the IPv4 header. On h2, `receive.py` expects a 5-byte header, so the 1-byte shift will cause UDP port matching to fail.
  For the loop, s1 will decrement the hop limit on each return pass, while s2 will not decrement it. Thus, the loop should last for 64 round-trips through s1 before terminating.
- **Actual Observations**:
  The wire capture in `assignment/evidence/mixed.txt` via tcpdump on h2 confirmed our prediction byte-for-byte:
  ```text
  0x0000:  ffff ffff ffff 0000 0000 0001 1212 0800  ................
  0x0010:  0002 3f45 0000 2100 0100 0040 d565 4e0a  ..?E..!....@.eN.
  0x0020:  3d00 010a 3d00 0310 e110 e100 0d7f c56d  =...=..........m
  0x0030:  6978 6564                                ixed
  ```
  Bytes 12-13 show EtherType `0x1212`. Bytes 14-17 contain `0800 0002` (`proto_id=0x0800`, `dst_id=2`), which allowed s2 to route to h2. Byte 18 is `0x3f` (decimal 63, the hop limit decremented once by s1), which s2 merged into the subsequent header emission.
  In the loop test, packet counts froze at `logs/s1.txt: 67`, `logs/s2.txt: 65`. Accounting for baseline startup packets, s1 processed the looped packet 64 times and then dropped it when `hop_limit` reached 0. This proves that even in an incremental rollout, upgraded ingress switches successfully prevent infinite loops.

### b) Safe Header Change Rollout Proposal
When rolling out header modifications across a network that cannot be upgraded simultaneously:
1. **Option 1: Distinct EtherType**:
   Allocate a distinct EtherType for the revised format (e.g., `0x1213` for `myTunnel_v2`). Upgraded switches parse both `0x1212` and `0x1213`. Upgraded edge switches encapsulate traffic using the new EtherType only if downstream paths are upgraded, or continue using `0x1212` until all nodes support `0x1213`. Legacy switches seeing unknown EtherTypes drop or bypass without byte-misalignment.
2. **Option 2: Header Versioning / TLV Extensions**:
   Incorporate a 4-bit version field or explicit Header Length / Options flag in the base tunnel header. Legacy switches check `version == 1` and process base fields, while upgraded switches parse optional extension headers (Hop Limit).
- **Comparison**: Option 1 (Distinct EtherType) is superior in programmable data planes. It avoids complex variable-length parsing logic in P4 pipelines, prevents TCAM stage exhaustion, and provides strict isolation between protocol versions during staging.

### c) Data Plane vs. Control Plane Loop Protection
- **Data Plane (Hop Limit / TTL)**:
  - Strengths: Operates at line rate directly on packets. It protects against transient loops during dynamic routing convergence, link failover events, and race conditions between asynchronous table updates.
  - Limitations: Reactive. It cannot prevent loops from forming; it only limits the duration and resource waste of a looping packet by dropping it when the counter expires.
- **Control Plane (`check_paths()` / Routing Graph Validation)**:
  - Strengths: Proactive. Algorithms like Dijkstra or centralized SDN path checkers compute global directed acyclic graphs (DAGs) and ensure routing tables are mathematically loop-free before entries are installed.
  - Limitations: Static. It cannot detect hardware synchronization delays, inconsistent update ordering between distributed switches, or bugs in switch ASIC state.
- **Necessity of Both**: Modern production networks pair proactive control plane verification (to guarantee loop-free steady-state paths) with reactive data plane hop limits (to catch in-flight transient loops during topology re-convergence).

### d) Hardware Implementation Cost at Line Rate
On high-speed programmable ASICs (e.g., Intel Tofino):
- **Parser Resources**: Parsing an extra 8-bit field consumes 1 additional extraction byte in the parser buffer. Because 8 bits aligns to a single byte boundary, it does not require bit-shifting across word boundaries.
- **Match-Action Stages**: The decrement (`hop_limit - 1`) and zero comparison (`hop_limit == 0`) utilize primitive 8-bit integer ALUs within an existing pipeline stage. This does not require expensive TCAM blocks, consuming only trivial SRAM/ALU budget.
- **Checksum Complexity**: Unlike IPv4 where TTL modification requires an incremental header checksum recomputation (`csum16`), `myTunnel` has no header checksum. The modification avoids checksum recalculation overhead.
- **Bandwidth Overhead**: The 1-byte addition represents less than 0.07% overhead on a standard 1500-byte MTU frame, having virtually zero impact on line-rate throughput.

## AI use

Antigravity AI assistant was used for code and writing assistance.

## Evidence files

| File | What it shows |
|---|---|
| `evidence/loop-before.txt` | The loop with your finished lab program: pass counts growing |
| `evidence/delivery.txt` | Tunnelled delivery with the hop limit, and a plain ping |
| `evidence/loop-after.txt` | The same loop with your hop limit: pass counts that stop |
| `evidence/mixed.txt` | The mixed rollout: s1 new, s2 old |
