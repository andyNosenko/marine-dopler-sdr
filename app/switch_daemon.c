#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <gpiod.h>
#include <limits.h>
#include <sched.h>
#include <signal.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>
#include <arpa/inet.h>

#define UDP_PORT 4210
#define STEP_NS 1000000L

static volatile sig_atomic_t running = 1;

static void stop_daemon(int signal_number) {
    (void)signal_number;
    running = 0;
}

static int get_line_offset(const char *name, unsigned int fallback,
                           unsigned int *offset) {
    const char *value = getenv(name);
    if (!value || !*value) {
        *offset = fallback;
        return 0;
    }

    char *end = NULL;
    errno = 0;
    unsigned long parsed = strtoul(value, &end, 10);
    if (errno || end == value || *end != '\0' || parsed > UINT_MAX) {
        fprintf(stderr, "Invalid %s: %s\n", name, value);
        return -1;
    }
    *offset = (unsigned int)parsed;
    return 0;
}

static void advance_deadline(struct timespec *deadline) {
    deadline->tv_nsec += STEP_NS;
    if (deadline->tv_nsec >= 1000000000L) {
        deadline->tv_sec++;
        deadline->tv_nsec -= 1000000000L;
    }
}

int main(void) {
    const char *chip_path = getenv("GPIO_CHIP");
    if (!chip_path || !*chip_path) chip_path = "/dev/gpiochip1";

    unsigned int offsets[3];
    if (get_line_offset("GPIO_LINE_A", 73, &offsets[0]) ||
        get_line_offset("GPIO_LINE_B", 70, &offsets[1]) ||
        get_line_offset("GPIO_LINE_GATE", 69, &offsets[2])) {
        return 2;
    }

    if (mlockall(MCL_CURRENT | MCL_FUTURE) != 0) {
        perror("mlockall");
    }
    struct sched_param scheduler = {.sched_priority = 90};
    if (sched_setscheduler(0, SCHED_FIFO, &scheduler) != 0) {
        perror("sched_setscheduler");
    }

    struct gpiod_chip *chip = gpiod_chip_open(chip_path);
    if (!chip) {
        fprintf(stderr, "Cannot open GPIO chip %s: %s\n",
                chip_path, strerror(errno));
        return 1;
    }

    struct gpiod_line_bulk lines;
    gpiod_line_bulk_init(&lines);
    if (gpiod_chip_get_lines(chip, offsets, 3, &lines) < 0) {
        fprintf(stderr, "Cannot acquire GPIO lines: %s\n", strerror(errno));
        gpiod_chip_close(chip);
        return 1;
    }

    int parked[3] = {0, 0, 0};
    if (gpiod_line_request_bulk_output(&lines, "yard-sdr-switch",
                                       parked) < 0) {
        fprintf(stderr, "Cannot request GPIO outputs: %s\n", strerror(errno));
        gpiod_chip_close(chip);
        return 1;
    }

    int socket_fd = socket(AF_INET, SOCK_DGRAM, 0);
    if (socket_fd < 0) {
        perror("socket");
        gpiod_line_release_bulk(&lines);
        gpiod_chip_close(chip);
        return 1;
    }
    int flags = fcntl(socket_fd, F_GETFL, 0);
    if (flags < 0 || fcntl(socket_fd, F_SETFL, flags | O_NONBLOCK) < 0) {
        perror("fcntl");
        close(socket_fd);
        gpiod_line_release_bulk(&lines);
        gpiod_chip_close(chip);
        return 1;
    }

    struct sockaddr_in address = {
        .sin_family = AF_INET,
        .sin_port = htons(UDP_PORT),
        .sin_addr.s_addr = htonl(INADDR_LOOPBACK)
    };
    if (bind(socket_fd, (struct sockaddr *)&address, sizeof(address)) < 0) {
        perror("bind UDP 4210");
        close(socket_fd);
        gpiod_line_release_bulk(&lines);
        gpiod_chip_close(chip);
        return 1;
    }

    int states[4][3] = {
        {0, 0, 1}, /* Antenna 1 + pilot reference */
        {1, 0, 0}, /* Antenna 2 */
        {0, 1, 0}, /* Antenna 3 */
        {1, 1, 0}  /* Antenna 4 */
    };
    signal(SIGINT, stop_daemon);
    signal(SIGTERM, stop_daemon);

    bool rotating = false;
    unsigned int antenna = 0;
    struct timespec deadline = {0};
    fprintf(stderr,
            "[yard-sdr-switch] chip=%s lines=A:%u B:%u GATE:%u UDP=127.0.0.1:%d\n",
            chip_path, offsets[0], offsets[1], offsets[2], UDP_PORT);

    while (running) {
        char command;
        if (recv(socket_fd, &command, 1, 0) > 0) {
            if (command == '1' && !rotating) {
                rotating = true;
                antenna = 0;
                clock_gettime(CLOCK_MONOTONIC, &deadline);
            } else if (command == '0' && rotating) {
                rotating = false;
                if (gpiod_line_set_value_bulk(&lines, parked) < 0) {
                    perror("set parked GPIO state");
                    running = 0;
                }
            }
        }

        if (rotating && running) {
            if (gpiod_line_set_value_bulk(&lines, states[antenna]) < 0) {
                perror("set antenna GPIO state");
                running = 0;
                break;
            }
            antenna = (antenna + 1U) % 4U;
            advance_deadline(&deadline);
            clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &deadline, NULL);
        } else {
            usleep(2000);
        }
    }

    (void)gpiod_line_set_value_bulk(&lines, parked);
    close(socket_fd);
    gpiod_line_release_bulk(&lines);
    gpiod_chip_close(chip);
    return 0;
}
