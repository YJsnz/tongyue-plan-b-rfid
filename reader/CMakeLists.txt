cmake_minimum_required(VERSION 3.16)
project(tongyue_reader LANGUAGES CXX)

add_library(audio_locator STATIC audio_locator.cpp)
target_compile_features(audio_locator PUBLIC cxx_std_17)
target_include_directories(audio_locator PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})

add_executable(locate_wav locate_wav.cpp)
target_link_libraries(locate_wav PRIVATE audio_locator)
