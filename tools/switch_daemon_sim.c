#define _GNU_SOURCE
#include <gpiod.h>
#include <time.h>
#include <sched.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <arpa/inet.h>
#include <sys/socket.h>
#include <sys/mman.h>
#include <stdlib.h>

#define CHIP_NAME_ENV "GPIO_SIM_CHIP"   /* имя виртуального чипа */
#define UDP_PORT      4210

int main(void) {
    const char *chip_name = getenv(CHIP_NAME_ENV);
    if (!chip_name) chip_name = "rdf_sim";

    if (mlockall(MCL_CURRENT | MCL_FUTURE) != 0) {
        perror("mlockall");
        /* в виртуальной среде может не хватать прав — не фатально */
    }

    struct sched_param sp = { .sched_priority = 90 };
    sched_setscheduler(0, SCHED_FIFO, &sp);

    struct gpiod_chip *chip = gpiod_chip_open_by_name(chip_name);
    if (!chip) {
        fprintf(stderr, "gpiod_chip_open_by_name(%s) failed\n", chip_name);
        return 1;
    }

    unsigned int offs[3] = { 0, 1, 2 };  /* A, B, GATE */
    struct gpiod_line_bulk bulk;
    gpiod_line_bulk_init(&bulk);
    if (gpiod_chip_get_lines(chip, offs, 3, &bulk) < 0) {
        fprintf(stderr, "gpiod_chip_get_lines failed\n");
        return 1;
    }

    int parked[3] = { 0, 0, 0 };
    if (gpiod_line_request_bulk_output(&bulk, "rdf-sim", parked) < 0) {
        fprintf(stderr, "gpiod_line_request_bulk_output failed\n");
        return 1;
    }

    int sock = socket(AF_INET, SOCK_DGRAM, 0);
    fcntl(sock, F_SETFL, O_NONBLOCK);
    struct sockaddr_in addr = {
        .sin_family = AF_INET,
        .sin_port = htons(UDP_PORT),
        .sin_addr.s_addr = htonl(INADDR_LOOPBACK)
    };
    if (bind(sock, (struct sockaddr*)&addr, sizeof(addr)) < 0) {
        perror("bind UDP 4210"); return 1;
    }

    int st[4][3] = {
        { 0, 0, 1 },  /* Ant1 + пилот */
        { 1, 0, 0 },  /* Ant2 */
        { 0, 1, 0 },  /* Ant3 */
        { 1, 1, 0 }   /* Ant4 */
    };

    const long step_ns = 1000000L;  /* 1 мс → 250 Гц */
    struct timespec next = { 0 };
    int rotating = 0, ant = 0;
    char cmd;

    printf("[switch_daemon_sim] chip=%s, waiting UDP :%d\n",
           chip_name, UDP_PORT);
    fflush(stdout);

    while (1) {
        if (recv(sock, &cmd, 1, 0) > 0) {
            if (cmd == '1' && !rotating) {
                rotating = 1; ant = 0;
                clock_gettime(CLOCK_MONOTONIC, &next);
                printf("[switch_daemon_sim] START rotation\n"); fflush(stdout);
            } else if (cmd == '0' && rotating) {
                rotating = 0;
                gpiod_line_set_value_bulk(&bulk, parked);
                printf("[switch_daemon_sim] STOP rotation (parked)\n"); fflush(stdout);
            }
        }
        if (rotating) {
            gpiod_line_set_value_bulk(&bulk, st[ant]);
            ant = (ant + 1) & 3;
            next.tv_nsec += step_ns;
            if (next.tv_nsec >= 1000000000L) { next.tv_sec++; next.tv_nsec -= 1000000000L; }
            clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &next, NULL);
        } else {
            usleep(2000);
        }
    }
}
