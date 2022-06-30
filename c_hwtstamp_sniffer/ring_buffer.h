#pragma once

/**
 * Idea and code sample from https://www.mikrocontroller.net/articles/FIFO
 * */

#include <stdint.h>
#include <time.h>

#define BUF_SUCCESS 0
#define BUF_FILLED_UP 1
#define BUF_EMPTY 2

#define BUFFER_SIZE 1024 // Has to be 2^n
#define BUFFER_MASK (BUFFER_SIZE - 1)

struct RingBuffer {
    struct timespec data[BUFFER_SIZE];
    uint32_t read;
    uint32_t write;
} ring_buffer = { {}, 0, 0};

uint8_t RingBufferAdd(struct timespec ts_in)
{
    uint32_t next = ((ring_buffer.write + 1) & BUFFER_MASK);

    if (ring_buffer.read == next)
    {
        return -BUF_FILLED_UP;
    }

    ring_buffer.data[ring_buffer.write] = ts_in;
    ring_buffer.write = next;

    return BUF_SUCCESS;
}

uint8_t RingBufferGet(struct timespec *ts_out)
{
    if (ring_buffer.read == ring_buffer.write)
    {
        return -BUF_EMPTY;
    }

    *ts_out = ring_buffer.data[ring_buffer.read];
    ring_buffer.read = (ring_buffer.read + 1) & BUFFER_MASK;

    return BUF_SUCCESS;
}