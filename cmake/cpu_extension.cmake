set(CMAKE_EXPORT_COMPILE_COMMANDS ON)
set(CMAKE_CXX_STANDARD 17)

#
# Define environment variables for special configurations
#
if(DEFINED ENV{APHRODITE_CPU_AVX512BF16})
    set(ENABLE_AVX512BF16 ON)
endif()

include_directories("${CMAKE_SOURCE_DIR}/kernels")

#
# Check the compile flags
#
list(APPEND CXX_COMPILE_FLAGS
    "-fopenmp"
    "-DAPHRODITE_CPU_EXTENSION")

execute_process(COMMAND cat /proc/cpuinfo
                RESULT_VARIABLE CPUINFO_RET
                OUTPUT_VARIABLE CPUINFO)

if (NOT CPUINFO_RET EQUAL 0)
    message(FATAL_ERROR "Failed to check CPU features via /proc/cpuinfo")
endif()

function (find_isa CPUINFO TARGET OUT)
    string(FIND ${CPUINFO} ${TARGET} ISA_FOUND)
    if(NOT ISA_FOUND EQUAL -1)
        set(${OUT} ON PARENT_SCOPE)
    else()
        set(${OUT} OFF PARENT_SCOPE)
    endif()
endfunction()

function (is_avx512_disabled OUT)
    set(DISABLE_AVX512 $ENV{APHRODITE_CPU_DISABLE_AVX512})
    if(DISABLE_AVX512 AND DISABLE_AVX512 STREQUAL "true")
        set(${OUT} ON PARENT_SCOPE)
    else()
        set(${OUT} OFF PARENT_SCOPE)
    endif()
endfunction()

is_avx512_disabled(AVX512_DISABLED)

find_isa(${CPUINFO} "avx2" AVX2_FOUND)
find_isa(${CPUINFO} "avx512f" AVX512_FOUND)
find_isa(${CPUINFO} "POWER10" POWER10_FOUND)
find_isa(${CPUINFO} "POWER9" POWER9_FOUND)

if (AVX512_FOUND AND NOT AVX512_DISABLED)
    list(APPEND CXX_COMPILE_FLAGS
        "-mavx512f"
        "-mavx512vl"
        "-mavx512bw"
        "-mavx512dq")

    find_isa(${CPUINFO} "avx512_bf16" AVX512BF16_FOUND)
    if (AVX512BF16_FOUND OR ENABLE_AVX512BF16)
        if (CMAKE_CXX_COMPILER_ID STREQUAL "GNU" AND
            CMAKE_CXX_COMPILER_VERSION VERSION_GREATER_EQUAL 12.3)
            list(APPEND CXX_COMPILE_FLAGS "-mavx512bf16")
        else()
            message(WARNING "Disable AVX512-BF16 ISA support, requires gcc/g++ >= 12.3")
        endif()
    else()
        message(WARNING "Disable AVX512-BF16 ISA support, no avx512_bf16 found in local CPU flags." " If cross-compilation is required, please set env APHRODITE_CPU_AVX512BF16=1.")
    endif()
elseif (AVX2_FOUND)
    list(APPEND CXX_COMPILE_FLAGS "-mavx2")
    message(WARNING "Aphrodite CPU backend using AVX2 ISA")
elseif (POWER9_FOUND OR POWER10_FOUND)
    message(STATUS "PowerPC detected")
    # Check for PowerPC VSX support
    list(APPEND CXX_COMPILE_FLAGS
        "-mvsx"
        "-mcpu=native"
        "-mtune=native")
else()
    message(FATAL_ERROR "Aphrodite CPU backend requires AVX512 or AVX2 or Power9+ ISA support.")
endif()

message(STATUS "CPU extension compile flags: ${CXX_COMPILE_FLAGS}")

list(APPEND LIBS numa)

# Appending the dnnl library for the AVX2 and AVX512, as it is not utilized by Power architecture.
if (AVX2_FOUND OR AVX512_FOUND)
    list(APPEND LIBS dnnl)
endif()

#
# _C extension
#
set(APHRODITE_EXT_SRC
    "kernels/cpu/activation.cpp"
    "kernels/cpu/attention.cpp"
    "kernels/cpu/cache.cpp"
    "kernels/cpu/utils.cpp"
    "kernels/cpu/layernorm.cpp"
    "kernels/cpu/pos_encoding.cpp"
    "kernels/cpu/torch_bindings.cpp")

if (AVX512_FOUND AND NOT AVX512_DISABLED)
    set(APHRODITE_EXT_SRC
        "kernels/cpu/quant.cpp"
        ${APHRODITE_EXT_SRC})
endif()

#
# Define extension targets
#

define_gpu_extension_target(
    _C
    DESTINATION aphrodite
    LANGUAGE CXX
    SOURCES ${APHRODITE_EXT_SRC}
    LIBRARIES ${LIBS}
    COMPILE_FLAGS ${CXX_COMPILE_FLAGS}
    USE_SABI 3
    WITH_SOABI
)

message(STATUS "Enabling C extension.")
