/* Week 5 lab — IPv4 forwarding, plus a custom tunnel header that you will finish.
 *
 * As supplied, this program forwards IPv4 by longest-prefix match. The tunnel header, its
 * table and its action are already declared; Steps 4–6 of the lab connect them to the
 * parser, the ingress control and the deparser. Look for the TODO comments.
 */
#include <core.p4>
#include <v1model.p4>

const bit<16> TYPE_IPV4     = 0x0800;
const bit<16> TYPE_MYTUNNEL = 0x1212;

typedef bit<9>  egressSpec_t;
typedef bit<48> macAddr_t;
typedef bit<32> ip4Addr_t;

/*************************** HEADERS ***************************/

header ethernet_t {
    macAddr_t dstAddr;
    macAddr_t srcAddr;
    bit<16>   etherType;
}

header myTunnel_t {
    bit<16> proto_id;   // what follows the tunnel header, like etherType (0x0800: IPv4)
    bit<16> dst_id;     // which tunnel exit to deliver to: 1 = h1, 2 = h2, 3 = h3
}

header ipv4_t {
    bit<4>    version;
    bit<4>    ihl;
    bit<8>    diffserv;
    bit<16>   totalLen;
    bit<16>   identification;
    bit<3>    flags;
    bit<13>   fragOffset;
    bit<8>    ttl;
    bit<8>    protocol;
    bit<16>   hdrChecksum;
    ip4Addr_t srcAddr;
    ip4Addr_t dstAddr;
}

struct metadata { }

struct headers {
    ethernet_t ethernet;
    myTunnel_t myTunnel;
    ipv4_t     ipv4;
}

/*************************** PARSER ***************************/

parser MyParser(packet_in packet,
                out headers hdr,
                inout metadata meta,
                inout standard_metadata_t standard_metadata) {

    state start {
        packet.extract(hdr.ethernet);
        transition select(hdr.ethernet.etherType) {
            TYPE_IPV4: parse_ipv4;
            // TODO (Step 4): send TYPE_MYTUNNEL packets to a parse_myTunnel state
            default: accept;
        }
    }

    // TODO (Step 4): add the parse_myTunnel state here

    state parse_ipv4 {
        packet.extract(hdr.ipv4);
        transition accept;
    }
}

/*************************** CHECKSUM VERIFICATION ***************************/

control MyVerifyChecksum(inout headers hdr, inout metadata meta) {
    apply { }
}

/*************************** INGRESS ***************************/

control MyIngress(inout headers hdr,
                  inout metadata meta,
                  inout standard_metadata_t standard_metadata) {

    action drop() {
        mark_to_drop(standard_metadata);
    }

    action ipv4_forward(egressSpec_t port) {
        standard_metadata.egress_spec = port;
        hdr.ipv4.ttl = hdr.ipv4.ttl - 1;
    }

    table ipv4_lpm {
        key = { hdr.ipv4.dstAddr: lpm; }
        actions = { ipv4_forward; drop; }
        size = 1024;
        default_action = drop();
    }

    action myTunnel_forward(egressSpec_t port) {
        standard_metadata.egress_spec = port;
    }

    table myTunnel_exact {
        key = { hdr.myTunnel.dst_id: exact; }
        actions = { myTunnel_forward; drop; }
        size = 1024;
        default_action = drop();
    }

    apply {
        // TODO (Step 5): tunnelled packets should use myTunnel_exact instead
        if (hdr.ipv4.isValid()) {
            ipv4_lpm.apply();
        } else {
            drop();
        }
    }
}

/*************************** EGRESS ***************************/

control MyEgress(inout headers hdr,
                 inout metadata meta,
                 inout standard_metadata_t standard_metadata) {
    apply { }
}

/*************************** CHECKSUM COMPUTATION ***************************/

// ipv4_forward changes the TTL, so the IPv4 header checksum must be recomputed.
control MyComputeChecksum(inout headers hdr, inout metadata meta) {
    apply {
        update_checksum(
            hdr.ipv4.isValid(),
            { hdr.ipv4.version, hdr.ipv4.ihl, hdr.ipv4.diffserv, hdr.ipv4.totalLen,
              hdr.ipv4.identification, hdr.ipv4.flags, hdr.ipv4.fragOffset,
              hdr.ipv4.ttl, hdr.ipv4.protocol, hdr.ipv4.srcAddr, hdr.ipv4.dstAddr },
            hdr.ipv4.hdrChecksum,
            HashAlgorithm.csum16);
    }
}

/*************************** DEPARSER ***************************/

control MyDeparser(packet_out packet, in headers hdr) {
    apply {
        packet.emit(hdr.ethernet);
        // TODO (Step 4): emit the tunnel header (only valid headers are written)
        packet.emit(hdr.ipv4);
    }
}

V1Switch(
    MyParser(),
    MyVerifyChecksum(),
    MyIngress(),
    MyEgress(),
    MyComputeChecksum(),
    MyDeparser()
) main;
