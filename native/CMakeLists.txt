cmake_minimum_required(VERSION 3.15)
project(RobloxReconstructionTools LANGUAGES C CXX)
add_compile_options("$<$<CXX_COMPILER_ID:MSVC>:/utf-8>")
set(LUAU_BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(LUAU_BUILD_WEB OFF CACHE BOOL "" FORCE)
add_subdirectory(luau)
set(DECODER_SOURCES
    luaudec/src/bytecode/Bytecode.cpp
    luaudec/src/disasm/Disasm.cpp
    luaudec/src/cfg/CFG.cpp
    luaudec/src/lift/Lift.cpp
    luaudec/src/emit/Emit.cpp)
add_executable(luaudec luaudec/src/main.cpp ${DECODER_SOURCES})
target_compile_features(luaudec PRIVATE cxx_std_17)
target_compile_definitions(luaudec PRIVATE _CRT_SECURE_NO_WARNINGS)
target_include_directories(luaudec PRIVATE luaudec/src luau/Common/include)
add_executable(rcr-native-tests
    ../skills/roblox-client-reverse/assets/native_tests.cpp
    luaudec/src/bytecode/Bytecode.cpp
    luaudec/src/disasm/Disasm.cpp
    luaudec/src/cfg/CFG.cpp
    luaudec/src/emit/Emit.cpp)
target_compile_features(rcr-native-tests PRIVATE cxx_std_17)
target_include_directories(rcr-native-tests PRIVATE luaudec/src luau/Common/include)
