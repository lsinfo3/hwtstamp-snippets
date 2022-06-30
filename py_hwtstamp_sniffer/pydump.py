import socket
import fcntl
import sys
import traceback
import struct
from ctypes import Structure, POINTER, pointer, c_int, c_char, byref, addressof
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
SOF_TIMESTAMPING_RX_HARDWARE = (1<<2)
SOF_TIMESTAMPING_RAW_HARDWARE = (1<<6)
HWTSTAMP_TX_OFF = 0
HWTSTAMP_TX_ON = 1  # /usr/include/linux/net_tstamp.h:111
HWTSTAMP_FILTER_ALL = 1  # /usr/include/linux/net_tstamp.h:140


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
# all other structs in this file are handled with struct.pack()
class HWTSTAMP_CONFIG(Structure):
    _fields_ = [
        ("flags", c_int),
        ("tx_type", c_int),
        ("rx_filter", c_int)]

class HWTSTAMP_IFREQ(Structure):
    _fields_ = [
        ("ifr_name", c_char * 16),
        ("ifr_data", POINTER(HWTSTAMP_CONFIG))]


def main(args):
    try:
        # create socket for ioctl call
        s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETH_P_ALL))
        s.bind((args.interface, 0))

        # get the current device flags; 16sh = char[16] + short
        ifr = struct.pack("16sh", args.int_b, 0)
        req = fcntl.ioctl(s.fileno(), SIOCGIFFLAGS, ifr)
        ifr_flags = struct.unpack("16sh", req)[1]

        # add PROMISC flag and set flags back on the interface
        ifr_flags |= IFF_PROMISC
        ifr = struct.pack("16sh", args.int_b, ifr_flags)
        fcntl.ioctl(s.fileno(), SIOCSIFFLAGS, ifr)

        # request nanosecond resolution
        s.setsockopt(socket.SOL_SOCKET, SO_TIMESTAMPING, SOF_TIMESTAMPING_RX_HARDWARE | SOF_TIMESTAMPING_RAW_HARDWARE)

        # request hardware timestamps
        conf = HWTSTAMP_CONFIG(0, HWTSTAMP_TX_OFF, HWTSTAMP_FILTER_ALL)
        ifr = HWTSTAMP_IFREQ(args.int_b, pointer(conf))
        if x := fcntl.ioctl(s.fileno(), SIOCSHWTSTAMP, ifr) != 0:
            raise ValueError(f"fcntl.ioctl(SIOCSHWTSTAMP) returned {x}")

        # hardware timestamp socket options
        #s.setsockopt(SOL_PACKET, PACKET_TIMESTAMP, SOF_TIMESTAMPING_RAW_HARDWARE)
        #rxring_conf = struct.pack("IIII", 4096*4096, 1, 4096, 4096)
        #s.setsockopt(SOL_PACKET, PACKET_RX_RING, rxring_conf)

        while True:
            # read 1 packet
            raw_data, ancdata, flags, address = s.recvmsg(65535, 1024)

            #print("---")
            #for cmsg_level, cmsg_type, cmsg_data in ancdata:
                #print(f"   {cmsg_level=}, {cmsg_type=}, {cmsg_data=}")
                #if cmsg_level == socket.SOL_SOCKET and cmsg_type == SO_TIMESTAMPNS:
                #    print(f"   {cmsg_level=}, {cmsg_type=}, {cmsg_data=}")

            # unpack auxiliary stuff
            #print(ancdata[0][2])
            if len(ancdata) > 0:
                _, _, _, _, ts_sec, ts_nsec = struct.unpack("@QQQQQQ", ancdata[0][2])  # 2x unsigned long long
                ts_human = datetime.fromtimestamp(ts_sec).strftime("%H:%M:%S") + "." + str(ts_nsec)  # %Y-%m-%d

                # unpack headers
                eth_hdr = struct.unpack("!6s6s2s", raw_data[0:14])  # 6 dst MAC, 6 src MAC, 2 ethType
                #ipHeader = raw_data[14:34]
                #ip_hdr = struct.unpack("!12s4s4s", ipHeader)  # 12s represents Identification, Time to Live, Protocol | Flags, Fragment Offset, Header Checksum
                #tcpHeader = raw_data[34:54]
                #tcp_hdr = struct.unpack("!HH16s", tcpHeader)

                print(f"{ts_human} \t {_mac(eth_hdr[1])} -> {_mac(eth_hdr[0])}, type={_hex(eth_hdr[2])}, hash={raw_data[14:]}", flush=True)

    except KeyboardInterrupt:
        print("Shutdown requested... exiting")

    except Exception as ex:
        traceback.print_exc()

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
    parser.add_argument("-r", "--remove", action="store_true",
                        help="remove promisc flag at the end",
                        default=False)
    args = parser.parse_args()
    args.int_b = args.interface.encode("utf-8")

    main(args)
