cmake_minimum_required(VERSION 3.16)
project(hw_ts_test)

find_package (Threads)

set(CMAKE_CXX_STANDARD 11)

add_executable(hw_ts_test main.c)
target_link_libraries(hw_ts_test ${CMAKE_THREAD_LIBS_INIT})
