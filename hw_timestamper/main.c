#include <time.h>
#include <poll.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <stdlib.h>
#include <stdint.h>
#include <inttypes.h>
#include <net/if.h>
#include <arpa/inet.h>
#include <sys/types.h>
#include <sys/ioctl.h>
#include <errno.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <linux/if_ether.h>
#include <net/if.h>
#include <linux/if_packet.h>
#include <net/ethernet.h>
#include <linux/sockios.h>
#include <linux/net_tstamp.h>
#include <signal.h>
#include <pthread.h>
#include "ring_buffer.h"

#define NS_IN_S 1000000000
#define MAX_PACKET_SIZE 2048

int keep_running = 1;

void
print_usage()
{
    printf("Usage\n\thw_timestamp [options] <interface name>\n\n");
    printf("Options:\n");
}

int
init_socket(int socket_domain, int socket_type, int socket_protocol, const char *if_name)
{
    /**
     * This function creates the socket within the desired space and protocol.
     * Then it binds the socket to the required address.
     */

    // Create the socket
    int fd = socket(socket_domain, socket_type, htons(socket_protocol));
    if (fd == -1)
    {
        fprintf(stderr, "socket: %s\n", strerror(errno));
        return EXIT_FAILURE;
    }

    // Bind Socket to address that receives all packets
    int idx = if_nametoindex(if_name);
    struct sockaddr_ll link_layer = { 0 };
    link_layer.sll_family = socket_domain;
    link_layer.sll_ifindex = idx;
    link_layer.sll_protocol = htons(socket_protocol);

    if(bind(fd, (const struct sockaddr *) &link_layer, sizeof(link_layer)) == -1)
    {
        fprintf(stderr, "socket.bind: %s\n", strerror(errno));
        return EXIT_FAILURE;
    }

    // Explicitly bind socket to given interface
    struct ifreq iface;
    snprintf(iface.ifr_name, IFNAMSIZ, "%s", if_name);

    if (setsockopt(fd, SOL_SOCKET, SO_BINDTODEVICE, &iface, sizeof(iface)) < 0)
    {
        fprintf(stderr, "setsockopt(SO_BINDTODEVICE): %s\n", strerror(errno));
        return EXIT_FAILURE;
    }

    return fd;
}

int
activate_timestamping(int socket, const char *if_name)
{
    /**
     * Activate Timestamping on Hardware
     * */

    // Set Config
    struct hwtstamp_config hwts_config = { 0 };
    struct ifreq ifr = { 0 };

    hwts_config.tx_type = HWTSTAMP_TX_OFF;
    hwts_config.rx_filter = HWTSTAMP_FILTER_ALL;
    snprintf(ifr.ifr_name, IFNAMSIZ, "%s", "enp5s0");
    ifr.ifr_data = (void *)&hwts_config;

    // Apply to Device
    if (ioctl(socket, SIOCSHWTSTAMP, &ifr) < 0)
    {
        fprintf(stderr, "ioctl(SIOCSHWTSTAMP): %s %d\n", strerror(errno), errno);
        return EXIT_FAILURE;
    }

    /* Enable reporting of hardware timestamps */
    int hwts_rp = SOF_TIMESTAMPING_RX_HARDWARE | SOF_TIMESTAMPING_RAW_HARDWARE;
    if (setsockopt(socket, SOL_SOCKET, SO_TIMESTAMPING, &hwts_rp, sizeof(hwts_rp)) < 0)
    {
        fprintf(stderr, "setsockopt(SO_TIMESTAMPING): %s\n", strerror(errno));
        return EXIT_FAILURE;
    }

    return 0;
}

int
activate_promiscuous_mode(int socket, const char *if_name)
{
    /**
     * Activate promiscuous mode on the desired interface
     * */

    struct ifreq prom = { 0 };
    snprintf(prom.ifr_name, IFNAMSIZ, "%s", if_name);


    // get current config
    if(ioctl(socket, SIOCGIFFLAGS, &prom) == -1)
    {
        fprintf(stderr, "ioctl(SIOCGIFFLAGS): %s\n", strerror(errno));
    }

    prom.ifr_flags |= IFF_PROMISC;

    if(ioctl(socket, SIOCSIFFLAGS, &prom) == -1)
    {
        fprintf(stderr, "ioctl(SIOCSIFFLAGS): %s\n", strerror(errno));
    }
}

void
sig_handler(int signum)
{
    if (signum == SIGINT)
    {
        printf("Stopping system\n");
        keep_running = 0;
    }
}

void*
packet_receive(void* socket)
{
    int fd_socket = (int)socket;
    struct msghdr msg = {0};
    struct iovec iov;
    char pktbuf[4096] = {0};

    char ctrl[4096] = {0};
    struct cmsghdr *cmsg_hdr;

    iov.iov_base = pktbuf;
    iov.iov_len = sizeof(pktbuf);

    msg.msg_control = ctrl;
    msg.msg_controllen = sizeof(ctrl);

    msg.msg_name = NULL;
    msg.msg_namelen = 0;
    msg.msg_iov = &iov;
    msg.msg_iovlen = 1;

    struct timespec *ts_all;
    struct timespec ts;

    int level, type, buf_rc;
    ssize_t recv_rc;

    printf("Receiver Thread starting\n");

    for (;keep_running;) {
        recv_rc = recvmsg(fd_socket, &msg, MSG_DONTWAIT); // returns size or -1 in case of error

        if (recv_rc == -1)
        {
            switch (errno) {
                case EAGAIN:
                    continue;
                default:
                    fprintf(stderr, "recvmsg: %s\n", strerror(errno));
            }
        }

        if (recv_rc > 0)
        {
            for (cmsg_hdr = CMSG_FIRSTHDR(&msg); cmsg_hdr != NULL; cmsg_hdr = CMSG_NXTHDR(&msg, cmsg_hdr))
            {
                level = cmsg_hdr->cmsg_level;
                type  = cmsg_hdr->cmsg_type;
                if (SOL_SOCKET == level && SCM_TIMESTAMPING == type) {
                    ts_all = (struct timespec *) CMSG_DATA(cmsg_hdr);
                    ts = ts_all[2];
                    // Add to result buffer
                    if (RingBufferAdd(ts) < 0)
                    {
                        fprintf(stdout, "Buffer enqueue error: Buffer full.");
                    }
                }
            }
        }
    }
}

void*
result_worker()
{
    FILE* output;
    uint64_t  seq_num = 0;
    struct timespec res;
    uint64_t time_in_ns = 0;

    output = fopen("Test.csv", "w");
    fprintf(output, "SequenceNr;TimeInNs\n");

    printf("Starting Result Thread\n");

    while(keep_running)
    {
        if (RingBufferGet(&res) == 0)
        {
            time_in_ns = res.tv_sec * NS_IN_S + res.tv_nsec;
            fprintf(output, "%"PRIu64";%"PRIu64"\n", seq_num, time_in_ns);
            ++seq_num;
        }
    }

    fclose(output);
}

int
main(int argc, char *argv[]) {
    int fd_socket;
    pthread_t receive_thread;
    pthread_t result_thread;

    printf("Starting Timestamp test\n");

    //Setup signal handler
    signal(SIGINT, sig_handler);

    fd_socket = init_socket(AF_PACKET, SOCK_RAW, ETH_P_ALL, "enp5s0");
    if (fd_socket == EXIT_FAILURE)
    {
        return EXIT_FAILURE;
    }

    // Setup HW Timestamps
    int ret_code = activate_timestamping(fd_socket, "enp5s0");
    if (ret_code == EXIT_FAILURE)
    {
        return EXIT_FAILURE;
    }

    ret_code = pthread_create(&receive_thread, NULL, packet_receive, (void *)fd_socket);
    if (ret_code)
    {
        return EXIT_FAILURE;
    }

    ret_code = pthread_create(&receive_thread, NULL, result_worker, NULL);
    if (ret_code)
    {
        return EXIT_FAILURE;
    }

    while(keep_running)
    {
        sleep(1);
    }

    return 0;
}
