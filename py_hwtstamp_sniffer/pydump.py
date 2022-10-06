import socket
import fcntl
import sys
import traceback
import struct
import select
import mmap
from ctypes import Structure, POINTER, pointer, byref, addressof, c_int, c_char, c_uint, c_ulong, c_ushort, c_uint32, c_uint16, c_uint8
from datetime import datetime
from argparse import ArgumentParser
from binascii import hexlify


# grep -rnw "/usr/include" -e "SIOCGIFFLAGS"
ETH_P_ALL = 0x0003
IFF_PROMISC = 0x100
SIOCGIFFLAGS = 0x8913
SIOCSIFFLAGS = 0x8914
SIOCSHWTSTAMP = 0x89b0
SO_TIMESTAMPNS = 35  # or 64 ... idk
SO_TIMESTAMPING = 37  # or 65?
PACKET_TIMESTAMP = 17
SOL_PACKET = 263
PACKET_RX_RING = 5
PACKET_IGNORE_OUTGOING = 23
SOF_TIMESTAMPING_RX_HARDWARE = (1<<2)
SOF_TIMESTAMPING_RAW_HARDWARE = (1<<6)
HWTSTAMP_TX_OFF = 0
HWTSTAMP_TX_ON = 1  # /usr/include/linux/net_tstamp.h:111
HWTSTAMP_FILTER_NONE = 0
HWTSTAMP_FILTER_ALL = 1  # /usr/include/linux/net_tstamp.h:140
PACKET_VERSION = 10
TPACKET_V2 = 1


def _mac(bytestring): return hexlify(bytestring, ':').decode("utf-8")
def _hex(bytestring): return "0x" + hexlify(bytestring).decode("utf-8")


# https://man7.org/linux/man-pages/man7/netdevice.7.html
#
# IFNAMSIZ = 16
#
# struct ifreq {
#     char ifr_name[IFNAMSIZ]; /* Interface name */
#     union {
#         struct sockaddr ifr_addr;
#         struct sockaddr ifr_dstaddr;
#         struct sockaddr ifr_broadaddr;
#         struct sockaddr ifr_netmask;
#         struct sockaddr ifr_hwaddr;
#         short           ifr_flags;
#         int             ifr_ifindex;
#         int             ifr_metric;
#         int             ifr_mtu;
#         struct ifmap    ifr_map;
#         char            ifr_slave[IFNAMSIZ];
#         char            ifr_newname[IFNAMSIZ];
#         char           *ifr_data;
#     };
# };

# https://www.kernel.org/doc/Documentation/networking/timestamping.txt
#
# struct hwtstamp_config {
#     int flags;  /* no flags defined right now, must be zero */
#     int tx_type;    /* HWTSTAMP_TX_* */
#     int rx_filter;  /* HWTSTAMP_FILTER_* */
# };


# need explicit C struct for hwtstamp config, because we must pass it as a pointer to *ifr_data
# most other structs in this file are handled with struct.pack()
class HWTSTAMP_CONFIG(Structure):
    _fields_ = [
        ("flags", c_int),
        ("tx_type", c_int),
        ("rx_filter", c_int)]

class HWTSTAMP_IFREQ(Structure):
    _fields_ = [
        ("ifr_name", c_char * 16),
        ("ifr_data", POINTER(HWTSTAMP_CONFIG))]

# need this for the from_buffer() call
class TPACKET_HDR2(Structure):
    _fields_ = [
        ('tp_status', c_uint32),
        ('tp_len', c_uint32),
        ('tp_snaplen', c_uint32),
        ('tp_mac', c_uint16),
        ('tp_net', c_uint16),
        ('tp_sec', c_uint32),
        ('tp_nsec', c_uint32),
        ('tp_vlan_tci', c_uint16),
        ('tp_vlan_tpid', c_uint16),
        ('tp_padding', c_uint8 * 4)]


# This class is only used for the RX_RING setup
class RXsniffer(object):
    def __init__(self, s, frame_size = 4096, frame_num = 4096):
        self.s = s
        self.offset = 0
        self.frame_size = frame_size
        self.frame_num = frame_num
        
        # throw sys calls around for setup
        rxring_conf = struct.pack("IIII", frame_size * frame_num, 1, frame_size, frame_num)
        s.setsockopt(SOL_PACKET, PACKET_VERSION, TPACKET_V2)
        s.setsockopt(SOL_PACKET, PACKET_RX_RING, rxring_conf)
        s.setsockopt(SOL_PACKET, PACKET_TIMESTAMP, SOF_TIMESTAMPING_RAW_HARDWARE)
        s.setsockopt(SOL_PACKET, PACKET_IGNORE_OUTGOING, 1)

        # map the ring buffer memory to user space
        self.ringbuffer = mmap.mmap(s.fileno(), frame_size * frame_num, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE)


    # from gteissier/tcpdump.py https://gist.github.com/gteissier/4e076b2645e1754c99c8278cd4a6a987
    def recv_packets(self):
        while True:
            hdr = TPACKET_HDR2.from_buffer(self.ringbuffer, self.offset * self.frame_size)
            if hdr.tp_status == 0:
                break

            pkt_offset = self.offset * self.frame_size + hdr.tp_mac
            pkt_length = hdr.tp_snaplen

            yield ((hdr.tp_sec, hdr.tp_nsec), self.ringbuffer[pkt_offset:pkt_offset+pkt_length])

            hdr.tp_status = 0
            self.offset += 1
          
            # should be a modulo, but we required to have a power of two
            # in this case, &= (self.nr_frames - 1) is equivalent to %= self.nr_frames
            self.offset &= (self.frame_num - 1)


def main(args):
    raw_data = None

    if args.prefix:
        args.prefix = args.prefix + " "

    try:
        # create socket for ioctl call
        _opts = socket.SOCK_RAW if args.legacy else socket.SOCK_RAW | socket.SOCK_NONBLOCK
        s = socket.socket(socket.AF_PACKET, _opts, socket.htons(ETH_P_ALL))
        s.bind((args.interface, 0))

        # get the current device flags; 16sh = char[16] + short
        ifr = struct.pack("16sh", args.int_b, 0)
        req = fcntl.ioctl(s.fileno(), SIOCGIFFLAGS, ifr)
        ifr_flags = struct.unpack("16sh", req)[1]

        # add PROMISC flag and set flags back on the interface
        ifr_flags |= IFF_PROMISC
        ifr = struct.pack("16sh", args.int_b, ifr_flags)
        if not fcntl.ioctl(s.fileno(), SIOCSIFFLAGS, ifr):
            raise ValueError(f"fcntl.ioctl(SIOCSIFFLAGS) returned False")

        # request hardware timestamps and nanosecond resolution
        if args.legacy:
            #s.setsockopt(socket.SOL_SOCKET, SO_TIMESTAMPNS, 1)
            s.setsockopt(socket.SOL_SOCKET, SO_TIMESTAMPING, SOF_TIMESTAMPING_RX_HARDWARE | SOF_TIMESTAMPING_RAW_HARDWARE)
        conf = HWTSTAMP_CONFIG(0, HWTSTAMP_TX_OFF, HWTSTAMP_FILTER_ALL)
        ifr = HWTSTAMP_IFREQ(args.int_b, pointer(conf))
        if x := fcntl.ioctl(s.fileno(), SIOCSHWTSTAMP, ifr) != 0:
            raise ValueError(f"fcntl.ioctl(SIOCSHWTSTAMP) returned {x}")

        # --- RX RING stuff ---
        if not args.legacy:
            # rx ring instead of recvmsg system calls
            sniffer = RXsniffer(s)

            # set up IO polling for the socket
            poller = select.poll()
            poller.register(s, select.POLLIN)

            while True:
                events = poller.poll(500)
                for fd, evt in events:
                    for ts, raw_data in sniffer.recv_packets():
                        ts_sec, ts_nsec = ts
                        ts_human = datetime.fromtimestamp(ts_sec).strftime("%H:%M:%S") + "." + str(ts_nsec)

                        # unpack headers
                        eth_hdr = struct.unpack("!6s6s2s", raw_data[0:14])  # 6 dst MAC, 6 src MAC, 2 ethType

                        print(f"{args.prefix}{ts_human} \t {_mac(eth_hdr[1])} -> {_mac(eth_hdr[0])}, type={_hex(eth_hdr[2])}, hash={raw_data[14:214]}", flush=True)
        # --- /RX RING stuff ---
        
        # --- legacy stuff ---
        else:
            while True:
                # read 1 packet
                raw_data, ancdata, flags, address = s.recvmsg(65535, 1024)

                #print("---")
                #for cmsg_level, cmsg_type, cmsg_data in ancdata:
                    #print(f"   {cmsg_level=}, {cmsg_type=}, {cmsg_data=}")
                    #if cmsg_level == socket.SOL_SOCKET and cmsg_type == SO_TIMESTAMPNS:
                    #    print(f"   {cmsg_level=}, {cmsg_type=}, {cmsg_data=}")

                # do we even have a timestamp?
                # (we encountered some TX frames with broadcast dst from recvmsg that did not have timestamps)
                if len(ancdata) > 0:
                    # unpack auxiliary stuff
                    #ts_sec, ts_nsec = struct.unpack("@QQ", ancdata[0][2])  # 2x unsigned long long
                    _, _, _, _, ts_sec, ts_nsec = struct.unpack("@QQQQQQ", ancdata[0][2])  # 6x unsigned long long
                    ts_human = datetime.fromtimestamp(ts_sec).strftime("%H:%M:%S") + "." + str(ts_nsec)

                    # unpack headers
                    eth_hdr = struct.unpack("!6s6s2s", raw_data[0:14])  # 6 dst MAC, 6 src MAC, 2 ethType
                    #ipHeader = raw_data[14:34]
                    #ip_hdr = struct.unpack("!12s4s4s", ipHeader)  # 12s represents Identification, Time to Live, Protocol | Flags, Fragment Offset, Header Checksum
                    #tcpHeader = raw_data[34:54]
                    #tcp_hdr = struct.unpack("!HH16s", tcpHeader)

                    print(f"{args.prefix}{ts_human} \t {_mac(eth_hdr[1])} -> {_mac(eth_hdr[0])}, type={_hex(eth_hdr[2])}, hash={raw_data[14:214]}", flush=True)
        # --- /legacy stuff ---

    except KeyboardInterrupt:
        print("Shutdown requested... exiting")

    except Exception as ex:
        traceback.print_exc()
        if raw_data:
            print(f"{raw_data=}")

    # remove flag
    if args.remove:
        # get flags
        ifr = struct.pack("16sh", args.int_b, 0)
        req = fcntl.ioctl(s.fileno(), SIOCGIFFLAGS, ifr)
        ifr_flags = struct.unpack("16sh", req)[1]

        # remove promisc
        ifr_flags &= ~IFF_PROMISC

        # set flags
        ifr = struct.pack("16sh", args.int_b, ifr_flags)
        fcntl.ioctl(s.fileno(), SIOCSIFFLAGS, ifr)

    sys.exit(0)


if __name__ == "__main__":
    # parse command line arguments
    parser = ArgumentParser()
    parser.add_argument("-i", "--interface",
                        help="the interface to set into promisc mode",
                        default="eth0")
    parser.add_argument("-p", "--prefix",
                        help="prefix stdout with a string",
                        default=None)
    parser.add_argument("-r", "--remove", action="store_true",
                        help="remove promisc flag at the end",
                        default=False)
    parser.add_argument("-l", "--legacy", action="store_true",
                        help="use standard recvmsg instead of PACKET_RX_RING",
                        default=False)
    args = parser.parse_args()
    args.int_b = args.interface.encode("utf-8")

    main(args)
